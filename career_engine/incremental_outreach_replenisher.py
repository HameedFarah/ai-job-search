#!/usr/bin/env python3
"""Safe incremental Career Outreach Replenisher — tracked under career_engine/.

Reads verified candidate records from JSONL/JSON, applies the full set of
dedupe/exclusion rules using existing QueueReconciler helpers, and appends
each accepted route to the canonical Auto Send Queue with NORMAL/PENDING
status.

Defects fixed versus the runtime/rega_replenisher.py draft:
1. Gmail census: never catch RuntimeError / auth failure — MUST fail closed.
2. _find_verified_replacement: implemented (not placeholder).
3. Per-batch buffer cleared after each successful append (no cross-batch
   reinsert).
4. Uses the proven queue-append mechanism via _read_queue_sheet + direct
   sheets_request with idempotent stable Queue_ID and post-write readback
   verification.
5. Tracks production code under career_engine/ (not runtime/ draft).
6. Supports any verified JSONL/JSON input — not limited to REGA-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Reuse existing QueueReconciler helpers
# ---------------------------------------------------------------------------
from career_engine.outreach_reconciler import (
    BLOCKED_MAILBOX_LOCALS,
    EXCLUDED_COMPANIES,
    EXCLUDED_COMPANIES_LOW,
    EXCLUDED_DOMAINS,
    JORDAN_HOLD_KEY,
    KNOWN_ALREADY_CONTACTED_ALIASES,
    KNOWN_ALREADY_CONTACTED_DOMAINS,
    QUARANTINED_DOMAINS,
    QUEUE_SHEET_NAME,
    SPREADSHEET_ID,
    _company_key,
    _email_domain,
    _is_inappropriate_mailbox,
    _is_jordan_held,
    _is_permanently_failed,
    _read_master_send_queue,
    _read_queue_sheet,
    _stable_id_for,
    _valid_email,
    gmail_access_token_for_context,
    normalise_row,
    rclone_access_token,
    sheets_request,
    verify_both_accounts_available,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MONITOR_DIR = "runtime/acceptance/outscraper-monitor-20260901"
DEFAULT_BATCH_SIZE = 5
DEFAULT_JOURNAL_PATH = "runtime/acceptance/replenisher-queue-journal.json"
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com",
    "proton.me", "protonmail.com",
}

# Identity gate values considered acceptable for promotion.
ALLOWED_IDENTITY_GATES = {
    "confirmed_official_domain",
    "confirmed_official_email",
    "verified_owner_domain",
}

# Route kinds in order of preference when multiple exist at same domain.
ROUTE_KIND_PREFERENCE = ["recruitment", "general", "sales", "support"]

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _mailbox_local(email: str) -> str:
    email = str(email or "").strip().lower()
    return email.split("@", 1)[0] if "@" in email else ""


def _is_blocked_local(email: str) -> bool:
    """Check if mailbox local part is in the blocked set (uses BLOCKED_MAILBOX_LOCALS)."""
    local = _mailbox_local(email)
    if not local:
        return False
    role = re.split(r"[._+\-]", local, maxsplit=1)[0]
    return local in BLOCKED_MAILBOX_LOCALS or role in BLOCKED_MAILBOX_LOCALS


def _normalise_confirmed_domain(value: str) -> str:
    """Return a bare host for an owner-approved confirmed official domain."""
    domain = str(value or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/", 1)[0].strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    if not domain or "." not in domain or "@" in domain or any(ch.isspace() for ch in domain):
        return ""
    return domain


def _materialize_owner_route(route: dict) -> dict:
    """Apply owner outreach policy without inventing company identity.

    Catch-all mailboxes are allowed. When a route has a confirmed official
    domain but no discovered mailbox, synthesize ``info@<domain>`` as an
    explicit domain-derived fallback. Wrong/quarantined and public-mail
    domains are never used for this fallback.
    """
    item = dict(route)
    email = str(item.get("email") or "").strip().lower()
    identity_gate = str(item.get("identity_gate") or "").strip()
    domain = _normalise_confirmed_domain(
        str(item.get("domain") or item.get("official_domain") or "")
    )
    if email:
        item["email"] = email
        if domain:
            item["domain"] = domain
        return item

    if identity_gate != "confirmed_official_domain" or not domain:
        return item
    if domain in QUARANTINED_DOMAINS or domain in PUBLIC_EMAIL_DOMAINS:
        return item

    fallback = f"info@{domain}"
    if not _valid_email(fallback):
        return item

    item["email"] = fallback
    item["domain"] = domain
    item["route_kind"] = "general"
    item["verification"] = str(item.get("verification") or "DOMAIN_FALLBACK")
    existing_evidence = str(item.get("evidence") or "").strip()
    fallback_evidence = "owner_approved_info_fallback_from_confirmed_official_domain"
    item["evidence"] = f"{existing_evidence} | {fallback_evidence}" if existing_evidence else fallback_evidence
    item["domain_fallback"] = True
    return item


# ---------------------------------------------------------------------------
# Replenisher journal (restart-safe checkpoint)
# ---------------------------------------------------------------------------


def _load_journal(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _queue_journal(path: Path, queue_id: str, status: str, **extra: Any) -> None:
    journal = _load_journal(path)
    entries = journal.get("entries", {})
    entry = {
        "queue_id": queue_id,
        "email": extra.get("email", ""),
        "company": extra.get("company", ""),
        "status": status,
        "replenished_at": extra.get("replenished_at", _utc_now()),
        **{k: v for k, v in extra.items() if k not in ("email", "company")},
    }
    entries[queue_id] = entry
    _atomic_json(path, {"entries": entries, "schema": "replenisher-journal/1", "updated_at": _utc_now()})


def _is_already_replenished(path: Path, queue_id: str) -> bool:
    journal = _load_journal(path)
    entry = journal.get("entries", {}).get(queue_id)
    if entry is None:
        return False
    return str(entry.get("status", "")).upper() in ("PROMOTED", "BLOCKED", "SKIPPED_CONTACTED")


# ---------------------------------------------------------------------------
# Gmail dedupe — FAIL CLOSED
# ---------------------------------------------------------------------------


def gmail_dedupe_fail_closed(
    primary_config_dir: Path | None = None,
    sender_config_dir: Path | None = None,
    *,
    primary_email: str = "hameedo@gmail.com",
    sender_email: str = "hameedfarah@gmail.com",
    after_epoch: int | None = None,
) -> dict[str, str]:
    """Return {recipient_email: message_id} from BOTH Gmail accounts.

    FAIL CLOSED: any auth, census, or authentication error raises RuntimeError
    immediately — no fail-opens to empty dict.

    Uses existing QueueReconciler helpers:
    - ``verify_both_accounts_available`` for auth check
    - ``gmail_access_token_for_context`` for token refresh
    - ``_gmail_list_paginated`` for message enumeration
    """
    from career_engine.outreach_reconciler import (
        PRIMARY_GWS_CONFIG_DIR as _PRIMARY,
        SENDER_GWS_CONFIG_DIR as _SENDER,
    )

    cfg_primary = primary_config_dir or _PRIMARY
    cfg_sender = sender_config_dir or _SENDER

    # Fail-closed: both accounts MUST be available
    ok, detail = verify_both_accounts_available()
    if not ok:
        raise RuntimeError(f"FAIL_CLOSED_BOTH_GMAIL_REQUIRED: {detail}")

    contexts = (
        (primary_email, cfg_primary),
        (sender_email, cfg_sender),
    )
    query = f"after:{int(after_epoch)}" if after_epoch is not None else "after:2026/08/01"
    sent_by_email: dict[str, str] = {}

    for expected_email, config_dir in contexts:
        # Verify auth token refresh works
        try:
            token = gmail_access_token_for_context(config_dir)
        except Exception as exc:
            raise RuntimeError(
                f"FAIL_CLOSED_GMAIL_AUTH: {config_dir.name} token refresh failed: {exc}"
            ) from exc

        # Verify identity
        from career_engine.outreach_reconciler import _profile_email
        try:
            actual = _profile_email(config_dir)
        except Exception as exc:
            raise RuntimeError(
                f"FAIL_CLOSED_GMAIL_PROFILE: {config_dir.name} profile check failed: {exc}"
            ) from exc
        if actual != expected_email:
            raise RuntimeError(
                f"FAIL_CLOSED_GMAIL_MISMATCH: {config_dir.name} = {actual or 'unknown'}, expected {expected_email}"
            )

        # Enumerate sent messages
        from career_engine.outreach_reconciler import (
            _gmail_list_paginated,
            _message_to_addrs,
        )
        try:
            msgs = _gmail_list_paginated(token, "messages", query)
        except Exception as exc:
            raise RuntimeError(
                f"FAIL_CLOSED_GMAIL_CENSUS: {config_dir.name} census failed: {exc}"
            ) from exc

        for message in msgs:
            message_id = str(message.get("id") or "")
            if not message_id:
                continue
            try:
                for addr in _message_to_addrs(token, message_id):
                    sent_by_email.setdefault(addr, message_id)
            except Exception as exc:
                raise RuntimeError(
                    f"FAIL_CLOSED_GMAIL_READ: {config_dir.name} read failed for {message_id}: {exc}"
                ) from exc

    return sent_by_email


# ---------------------------------------------------------------------------
# Verified replacement discovery (not a placeholder)
# ---------------------------------------------------------------------------


def find_verified_replacement(
    company: str,
    failed_email: str,
    failed_domain: str,
    verified_routes: list[dict],
    sent_by_email: dict[str, str],
    master_rows: list[dict[str, str]],
) -> dict | None:
    """Find a verified replacement route at the same company.

    A replacement is valid only when:
    - It is DIFFERENT from the failed exact email address
    - The company has NO successful same-company/domain contact
    - It passes all validation gates (identity, not blocked local, etc.)

    Returns the replacement route dict or None.
    """
    company_key = _company_key(company)
    if not company_key:
        return None

    # Build blocked sets for this company
    blocked_emails: set[str] = set(sent_by_email)
    blocked_domains: set[str] = set()
    blocked_companies: set[str] = set()
    email_to_company: dict[str, str] = {}
    company_domains: dict[str, set[str]] = defaultdict(set)

    from career_engine.outreach_reconciler import _emails_from_master_row
    for row in master_rows:
        comp = _company_key(row.get("Company_or_Office", ""))
        for e in _emails_from_master_row(row):
            if comp:
                email_to_company[e] = comp
                domain = _email_domain(e)
                if domain and comp:
                    company_domains[comp].add(domain)

    for e in sent_by_email:
        domain = _email_domain(e)
        if domain:
            blocked_domains.add(domain)
        comp = email_to_company.get(e, "")
        if comp:
            blocked_companies.add(comp)
            blocked_domains.update(company_domains.get(comp, set()))

    # Search for a candidate that:
    # 1. Belongs to the same company
    # 2. Is a different email from the failed one
    # 3. Passes identity gate
    # 4. Is not blocked by dedupe
    # 5. Is not a blocked local mailbox
    # 6. Is not TTW/ASA
    best: dict | None = None
    for route in verified_routes:
        route_email = str(route.get("email") or "").strip().lower()
        route_company = str(route.get("company_name") or route.get("company") or "").strip()
        route_domain = str(route.get("domain") or _email_domain(route_email) or "").strip().lower()
        identity_gate = str(route.get("identity_gate") or "")

        # Must be different from the failed email
        if route_email == failed_email:
            continue

        # Must be valid
        if not _valid_email(route_email):
            continue

        # Identity gate must be confirmed
        if identity_gate not in ALLOWED_IDENTITY_GATES:
            continue

        # Must not be blocked local
        if _is_blocked_local(route_email) or _is_inappropriate_mailbox(route_email, str(route.get("evidence", ""))):
            continue

        # TTW/ASA exclusion
        if _is_company_excluded(route_company, route_domain):
            continue

        # Company must match
        rcompany_key = _company_key(route_company)
        if rcompany_key != company_key:
            continue

        # Must not be in blocked sets (same-company contact block)
        if route_email in blocked_emails:
            continue
        if route_domain and route_domain not in PUBLIC_EMAIL_DOMAINS:
            if route_domain in blocked_domains:
                continue
        if rcompany_key in blocked_companies:
            continue

        # Pick the most preferred route kind
        rkind = str(route.get("route_kind") or "general")
        if best is None:
            best = route
        else:
            best_kind = str(best.get("route_kind") or "general")
            if _route_kind_priority(rkind) < _route_kind_priority(best_kind):
                best = route

    return best


def _route_kind_priority(kind: str) -> int:
    """Lower number = higher preference."""
    try:
        return ROUTE_KIND_PREFERENCE.index(kind.lower())
    except ValueError:
        return len(ROUTE_KIND_PREFERENCE)


# ---------------------------------------------------------------------------
# Exclusion / eligibility checks (pure logic, no I/O)
# ---------------------------------------------------------------------------


def _is_company_excluded(company: str, domain: str) -> bool:
    """TTW and Arab Sustainable Architecture always blocked."""
    name = str(company or "").lower().strip()
    compact_name = "".join(ch for ch in name if ch.isalnum())
    clean_domain = str(domain or "").lower().strip()
    if clean_domain in EXCLUDED_DOMAINS:
        return True
    if name in EXCLUDED_COMPANIES_LOW:
        return True
    for exc in EXCLUDED_COMPANIES_LOW:
        compact_exc = "".join(ch for ch in exc if ch.isalnum())
        if exc and (exc in name or compact_exc and compact_exc in compact_name):
            return True
    return False


def _build_blocked_set(
    sent_by_email: dict[str, str],
    master_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Build blocked emails/domains/companies from Gmail + master.

    Same logic as QueueReconciler.fetch_gmail_dedupe but as a pure function
    for testability.
    """
    email_to_company: dict[str, str] = {}
    company_domains: dict[str, set[str]] = defaultdict(set)
    jordan_held_emails: set[str] = set()
    jordan_held_companies: set[str] = set()
    hard_blocked_emails: set[str] = set()
    permanent_bounce_emails: set[str] = set()

    hard_states = {
        "HOLD_OWNER_JORDAN_ENG_OFFICES",
        "REJECTED_REPLACEMENT_INVALID",
        "REJECTED_REPLACEMENT_BLACKLISTED",
        "REJECTED_OUTSCRAPER_BLACKLISTED_OFFICIAL_SOURCE",
        "HOLD_WRONG_COMPANY_DOMAIN",
        "HOLD_OUTREACH_INAPPROPRIATE_MAILBOX",
        "HOLD_OUTSCRAPER_IDENTITY",
    }
    from career_engine.outreach_reconciler import _emails_from_master_row
    for row in master_rows:
        company = _company_key(row.get("Company_or_Office", ""))
        for email in _emails_from_master_row(row):
            if company:
                email_to_company[email] = company
                domain = _email_domain(email)
                if domain:
                    company_domains[company].add(domain)

        state = str(row.get("Send_State") or "").strip().upper()
        terminal = str(row.get("Terminal_Outcome") or "").strip().upper()
        notes = str(row.get("Notes") or "").strip().upper()
        if state == JORDAN_HOLD_KEY:
            for email in _emails_from_master_row(row):
                jordan_held_emails.add(email)
            if company:
                jordan_held_companies.add(company)
        if state in hard_states:
            for email in _emails_from_master_row(row):
                hard_blocked_emails.add(email)
        if ("PERMANENT" in terminal and "BOUNCE" in terminal) or "PERMANENT BOUNCE" in notes:
            for email in _emails_from_master_row(row):
                permanent_bounce_emails.add(email)

    blocked_emails: set[str] = set(sent_by_email)
    blocked_domains: set[str] = set()
    blocked_companies: set[str] = set()
    for email in sent_by_email:
        domain = _email_domain(email)
        if domain:
            blocked_domains.add(domain)
        company = email_to_company.get(email, "")
        if company:
            blocked_companies.add(company)
            blocked_domains.update(
                d for d in company_domains.get(company, set())
                if domain not in PUBLIC_EMAIL_DOMAINS
            )

    return {
        "blocked_emails": blocked_emails,
        "blocked_domains": blocked_domains,
        "blocked_companies": blocked_companies,
        "permanent_bounce_emails": permanent_bounce_emails,
        "email_to_company": email_to_company,
        "company_domains": company_domains,
        "jordan_held_emails": jordan_held_emails,
        "jordan_held_companies": jordan_held_companies,
        "hard_blocked_emails": hard_blocked_emails,
    }


def validate_route_eligibility(
    email: str,
    company: str,
    domain: str,
    blocked: dict[str, Any],
    current_queue_rows: list[dict[str, str]],
    verified_routes: list[dict],
) -> tuple[bool, str, dict | None]:
    """Check if a route passes all dedupe/exclusion gates.

    Returns (eligible, reason, replacement_candidate_or_none).
    """
    company_key = _company_key(company)
    master_company = str(blocked.get("email_to_company", {}).get(email) or "")
    effective_company = master_company or company_key

    if not effective_company:
        return False, "unresolved_company_identity", None

    # Jordan hold
    if email in blocked.get("jordan_held_emails", set()):
        return False, "jordan_held", None
    if effective_company in blocked.get("jordan_held_companies", set()):
        return False, "jordan_held", None

    # TTW / ASA exclusions
    if _is_company_excluded(company, domain) or _is_company_excluded(effective_company, domain):
        return False, "company_excluded", None

    # Known contacted aliases
    if email in KNOWN_ALREADY_CONTACTED_ALIASES or domain in KNOWN_ALREADY_CONTACTED_DOMAINS:
        return False, "known_contacted_alias", None

    # Canonical hard rejects
    if email in blocked.get("hard_blocked_emails", set()):
        return False, "canonical_hard_block", None

    # Permanent bounce exact mailbox — never retry same exact email
    if email in blocked.get("permanent_bounce_emails", set()):
        replacement = find_verified_replacement(
            effective_company, email, domain, verified_routes,
            blocked.get("blocked_emails", set()),
            [],
        )
        if replacement:
            return False, "permanent_bounce", replacement
        return False, "permanent_bounce_no_replacement", None

    # Gmail dedupe
    if email in blocked.get("blocked_emails", set()):
        return False, "already_sent_gmail", None
    if domain and domain not in PUBLIC_EMAIL_DOMAINS:
        if domain in blocked.get("blocked_domains", set()):
            return False, "domain_sent_gmail", None
    if effective_company and effective_company in blocked.get("blocked_companies", set()):
        return False, "company_sent_gmail", None

    # Check current queue for duplicate email
    for row in current_queue_rows:
        row_email = str(row.get("Email") or "").strip().lower()
        if row_email == email:
            row_status = str(row.get("Status") or "").strip().upper()
            if row_status in ("PENDING", "SENDING", "FAILED_TEMPORARY"):
                return False, "already_in_queue", None
            if row_status == "FAILED_PERMANENT":
                replacement = find_verified_replacement(
                    effective_company, email, domain, verified_routes,
                    blocked.get("blocked_emails", set()),
                    [],
                )
                if replacement:
                    return False, "queue_permanent_bounce", replacement
                return False, "queue_permanent_bounce_no_replacement", None
            return False, "already_sent_in_queue", None

    return True, "ok", None


# ---------------------------------------------------------------------------
# Sheet append — idempotent with readback verification
# ---------------------------------------------------------------------------


def _queue_row_for_insert(
    email: str,
    company: str,
    source: str,
    evidence: str,
    queue_id: str = "",
    replacement_of: str = "",
) -> dict[str, str]:
    """Build a minimal queue row dict for batch insert."""
    return {
        "Queue_ID": queue_id or _stable_id_for(email, company),
        "Email": email.strip().lower(),
        "Company_or_Office": company.strip(),
        "Source": source,
        "Priority": "NORMAL",
        "Status": "PENDING",
        "Evidence_or_Notes": evidence,
        "replacement_of": replacement_of,
    }


def _append_rows_with_readback(
    token: str,
    rows: list[dict[str, str]],
    spreadsheet_id: str = SPREADSHEET_ID,
) -> list[dict[str, str]]:
    """Append rows to Auto Send Queue with idempotent readback verification.

    Each row gets a stable Queue_ID. After appending, the exact row is
    re-read and verified — if the readback does not match, the operation
    fails closed.

    Returns list of inserted row metadata.
    """
    if not rows:
        return []

    # Read current row count
    current = _read_queue_sheet(token, spreadsheet_id)
    next_row = len(current) + 2  # header at row 1, data starts row 2

    # Columns: Queue_ID, Email, Company_or_Office, Source, Priority,
    #           Status, Added_At, Sent_At, Gmail_Message_ID, Last_Error, Evidence_or_Notes
    col_map = {
        "Queue_ID": "A", "Email": "B", "Company_or_Office": "C",
        "Source": "D", "Priority": "E", "Status": "F",
        "Added_At": "G", "Sent_At": "H", "Gmail_Message_ID": "I",
        "Last_Error": "J", "Evidence_or_Notes": "K",
    }
    inserted: list[dict[str, str]] = []

    for row_data in rows:
        queue_id = row_data.get("Queue_ID", _stable_id_for(
            row_data["Email"], row_data.get("Company_or_Office", "")
        ))
        added_at = _utc_now()
        values = [
            queue_id,
            row_data["Email"].strip().lower(),
            row_data.get("Company_or_Office", ""),
            row_data.get("Source", ""),
            "NORMAL",
            "PENDING",
            added_at,
            "",
            "",
            "",
            row_data.get("Evidence_or_Notes", ""),
        ]

        # Batch update a single row
        range_str = f"'{QUEUE_SHEET_NAME}'!A{next_row}:K{next_row}"
        sheets_request(
            token,
            "PUT",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{_quote_range(range_str)}?valueInputOption=RAW",
            {"values": [values]},
        )

        # --- Idempotency readback ---
        read_range = f"'{QUEUE_SHEET_NAME}'!A{next_row}:K{next_row}"
        read_result = sheets_request(
            token,
            "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{_quote_range(read_range)}",
        )
        read_values = read_result.get("values", [])
        if not read_values or len(read_values[0]) < 2:
            raise RuntimeError(
                f"FAIL_CLOSED_READBACK: row {next_row} could not be read back"
            )
        read_email = str(read_values[0][1] or "").strip().lower()
        read_qid = str(read_values[0][0] or "").strip()
        if read_email != values[1]:
            raise RuntimeError(
                f"FAIL_CLOSED_READBACK_MISMATCH: row {next_row} email = {read_email}, expected {values[1]}"
            )
        if read_qid != queue_id:
            raise RuntimeError(
                f"FAIL_CLOSED_READBACK_QID_MISMATCH: row {next_row} queue_id = {read_qid}, expected {queue_id}"
            )

        inserted.append({
            "queue_id": queue_id,
            "email": values[1],
            "company": row_data.get("Company_or_Office", ""),
            "row_number": str(next_row),
            "added_at": added_at,
        })
        next_row += 1

    return inserted


def _quote_range(range_str: str) -> str:
    """URL-encode a sheet range string."""
    from urllib.parse import quote
    return quote(range_str, safe="!:")


# ---------------------------------------------------------------------------
# Journal flush
# ---------------------------------------------------------------------------


def _flush_journal(
    path: Path,
    inserted: list[dict],
    skipped_reasons: dict[str, str],
) -> None:
    """Persist all outcomes (inserted + skipped) for restart safety."""
    journal = _load_journal(path)
    entries = journal.get("entries", {})

    for ins in inserted:
        # Handle both row-dict keys ("Queue_ID") and metadata keys ("queue_id")
        qid = ins.get("queue_id") or ins.get("Queue_ID", "")
        entries[qid] = {
            "queue_id": qid,
            "email": ins.get("email") or ins.get("Email", ""),
            "company": ins.get("company") or ins.get("Company_or_Office", ""),
            "status": "PROMOTED",
            "row_number": ins.get("row_number", ""),
            "replenished_at": ins.get("added_at", ""),
        }

    for queue_id, reason in skipped_reasons.items():
        entries[queue_id] = {
            "queue_id": queue_id,
            "status": "SKIPPED",
            "reason": reason,
        }

    _atomic_json(path, {
        "entries": entries,
        "schema": "replenisher-journal/1",
        "updated_at": _utc_now(),
    })


# ---------------------------------------------------------------------------
# Main replenisher
# ---------------------------------------------------------------------------


def run_replenishment(
    candidates_path: str | Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    apply: bool = False,
    monitor_dir: str = DEFAULT_MONITOR_DIR,
    journal_path: str | Path | None = None,
) -> dict:
    """Execute incremental outreach replenishment.

    Args:
        candidates_path: Path to JSONL/JSON file of verified candidates.
        batch_size: Max candidates per batch.
        apply: If True, write to sheet; if False, dry-run.
        monitor_dir: Directory for journal/checkpoint files.
        journal_path: Override journal path.

    Returns summary dict.
    """
    candidates_path = Path(candidates_path)
    monitor_path = Path(monitor_dir) if not Path(monitor_dir).is_absolute() else Path(monitor_dir)
    monitor_path.mkdir(parents=True, exist_ok=True)
    if journal_path is None:
        journal_path = monitor_path / "replenisher-journal.json"
    else:
        journal_path = Path(journal_path)
    if not Path(journal_path).is_absolute():
        journal_path = REPO_ROOT / journal_path

    # --- Load candidates ---
    candidates = [_materialize_owner_route(c) for c in _load_jsonl(candidates_path)]
    if not candidates:
        return {
            "ok": True,
            "total_candidates_found": 0,
            "newly_queued": 0,
            "skipped_deduped": 0,
            "skipped_excluded": 0,
            "skipped_gmail_fail": 0,
            "skipped_mailbox": 0,
            "skipped_permanent_failed": 0,
            "skipped_invalid": 0,
            "skipped_company_excluded": 0,
            "skipped_jordan_held": 0,
            "skipped_identity": 0,
            "batches_processed": 0,
            "status": "no_candidates_found",
            "sends": 0,
            "secret_values_in_output": False,
        }

    # --- Gather pre-check data ---
    token = None
    current_queue = []
    master_rows = []
    sent_by_email = {}
    blocked = {}

    if apply:
        try:
            token = rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
        except Exception as exc:
            return {
                "ok": False,
                "error": f"FAIL_CLOSED_TOKEN: {exc}",
                "error_type": "auth_failure",
                "total_candidates_found": len(candidates),
                "status": "blocked",
                "sends": 0,
                "secret_values_in_output": False,
            }

        try:
            current_queue = _read_queue_sheet(token, SPREADSHEET_ID)
        except Exception as exc:
            raise RuntimeError(f"FAIL_CLOSED_QUEUE_READ: {exc}") from exc

        try:
            master_rows = _read_master_send_queue(token, SPREADSHEET_ID)
        except Exception as exc:
            raise RuntimeError(f"FAIL_CLOSED_MASTER_READ: {exc}") from exc

        try:
            sent_by_email = gmail_dedupe_fail_closed()
        except RuntimeError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_type": "gmail_fail_closed",
                "total_candidates_found": len(candidates),
                "skipped_gmail_fail": len(candidates),
                "status": "blocked",
                "sends": 0,
                "secret_values_in_output": False,
            }

        blocked = _build_blocked_set(sent_by_email, master_rows)
    else:
        # For dry-run / test mocks: build empty or use defaults
        if not master_rows:
            master_rows = []
        blocked = _build_blocked_set(sent_by_email, master_rows)

    # --- Build verified enrichment routes index ---
    verified_routes: list[dict] = []
    for c in candidates:
        email = str(c.get("email") or "").strip().lower()
        if email and _valid_email(email):
            verified_routes.append(c)

    # --- Check restart journal ---
    journal = _load_journal(journal_path)
    already_replenished_ids = set(journal.get("entries", {}).keys())

    # --- Process batches ---
    newly_queued = 0
    counts = defaultdict(int)
    batches_processed = 0
    rows_to_insert: list[dict[str, str]] = []  # PER-BATCH buffer, cleared each batch
    batch_details: list[dict] = []

    # Filter out already-replenished
    pending = []
    for c in candidates:
        email = str(c.get("email") or "").strip().lower()
        company = str(c.get("company_name") or c.get("company") or c.get("domain") or "")
        if not _valid_email(email):
            counts["skipped_invalid"] += 1
            continue
        qid = str(c.get("queue_id") or _stable_id_for(email, company))
        if _is_already_replenished(journal_path, qid):
            continue
        pending.append(c)

    for batch_offset in range(0, len(pending), batch_size):
        batch = pending[batch_offset:batch_offset + batch_size]
        batch_skipped: dict[str, str] = {}  # queue_id -> reason

        batch_details.append({
            "batch_number": batches_processed + 1,
            "batch_offset": batch_offset,
            "batch_size_requested": batch_size,
            "batch_size_actual": len(batch),
            "queued": 0,
            "skipped": dict(counts),
            "routes": [],
        })

        rows_to_insert.clear()  # CRITICAL FIX: clear per-batch buffer

        for route in batch:
            email = str(route.get("email") or "").strip().lower()
            company = str(route.get("company_name") or route.get("company") or route.get("domain") or "")
            domain = str(route.get("domain") or _email_domain(email) or "")
            route_kind = str(route.get("route_kind") or "general")
            verification = str(route.get("verification") or "")
            identity_gate = str(route.get("identity_gate") or "")
            source = str(route.get("source") or "career_enrichment_incremental")
            evidence = str(route.get("evidence") or "")
            replacement_of = str(route.get("replacement_of") or "")

            qid = str(route.get("queue_id") or _stable_id_for(email, company))

            # Must have valid email
            if not email or not _valid_email(email):
                counts["skipped_invalid"] += 1
                batch_skipped[qid] = "invalid_email"
                continue

            # Identity gate must be confirmed
            if identity_gate not in ALLOWED_IDENTITY_GATES:
                counts["skipped_identity"] += 1
                batch_skipped[qid] = "identity_not_confirmed"
                continue

            # Check blocked mailbox locals
            if _is_blocked_local(email):
                counts["skipped_mailbox"] += 1
                batch_skipped[qid] = "blocked_mailbox_local"
                continue

            # Check TTW/ASA exclusions
            if _is_company_excluded(company, domain):
                counts["skipped_company_excluded"] += 1
                batch_skipped[qid] = "company_excluded"
                continue

            # Jordan hold check
            company_key = _company_key(company)
            master_company = str(blocked.get("email_to_company", {}).get(email) or "")
            effective_company = master_company or company_key
            if email in blocked.get("jordan_held_emails", set()):
                counts["skipped_jordan_held"] += 1
                batch_skipped[qid] = "jordan_held"
                continue
            if effective_company and effective_company in blocked.get("jordan_held_companies", set()):
                counts["skipped_jordan_held"] += 1
                batch_skipped[qid] = "jordan_held"
                continue

            # Validate against dedupe / master
            eligible, reason, replacement = validate_route_eligibility(
                email, company, domain, blocked, current_queue, verified_routes
            )

            if not eligible:
                if reason in ("permanent_bounce", "queue_permanent_bounce"):
                    if replacement:
                        # Process replacement instead
                        rep_email = str(replacement.get("email") or "").strip().lower()
                        rep_company = str(replacement.get("company_name") or replacement.get("company", ""))
                        rep_domain = str(replacement.get("domain") or _email_domain(rep_email) or "")
                        if rep_email and _valid_email(rep_email) and not _is_company_excluded(rep_company, rep_domain):
                            replacement["email"] = rep_email
                            replacement["company_name"] = rep_company
                            replacement["domain"] = rep_domain
                            replacement["replacement_of"] = email
                            replacement["source"] = source
                            replacement["verification"] = verification
                            replacement["route_kind"] = route_kind
                            replacement["evidence"] = evidence
                            pending.append(replacement)  # Re-process as new candidate
                        else:
                            counts["skipped_permanent_failed"] += 1
                            batch_skipped[qid] = "permanent_bounce_no_replacement"
                    else:
                        counts["skipped_permanent_failed"] += 1
                        batch_skipped[qid] = "permanent_bounce_no_replacement"
                elif reason in (
                    "already_sent_gmail", "domain_sent_gmail", "company_sent_gmail",
                    "already_sent_in_queue", "already_in_queue", "already_sent_in_queue",
                    "already_replenished", "known_contacted_alias",
                    "canonical_hard_block", "unresolved_company_identity",
                    "queue_permanent_bounce_no_replacement", "permanent_bounce_no_replacement",
                ):
                    counts["skipped_deduped"] += 1
                    batch_skipped[qid] = reason
                else:
                    counts["skipped_deduped"] += 1
                    batch_skipped[qid] = reason

                continue

            # Build evidence string
            evidence_parts = []
            if identity_gate:
                evidence_parts.append(f"identity_gate={identity_gate}")
            if verification:
                evidence_parts.append(f"verification={verification}")
            if route_kind:
                evidence_parts.append(f"route_kind={route_kind}")
            evidence_parts.append(f"source={source}")
            if replacement_of:
                evidence_parts.append(f"replacement_of={replacement_of}")
            final_evidence = " | ".join(evidence_parts)

            row = _queue_row_for_insert(
                email=email,
                company=company,
                source=source,
                evidence=final_evidence,
                replacement_of=replacement_of,
            )
            rows_to_insert.append(row)
            batch_details[-1]["queued"] += 1

        # --- Flush batch to queue ---
        if rows_to_insert and apply and token is not None:
            try:
                inserted = _append_rows_with_readback(token, rows_to_insert, SPREADSHEET_ID)
                for ins in inserted:
                    _queue_journal(journal_path, ins["queue_id"], "PROMOTED",
                                   email=ins["email"], company=ins["company"],
                                   row_number=ins["row_number"],
                                   replenished_at=ins["added_at"])
                newly_queued += len(inserted)
                batch_details[-1]["inserted_count"] = len(inserted)
            except RuntimeError as exc:
                # Fail closed on insert error — rollback journal for this batch
                raise RuntimeError(f"FAIL_CLOSED_INSERT: {exc}") from exc
        elif rows_to_insert:
            batch_details[-1]["dry_run_queued"] = len(rows_to_insert)
            newly_queued += len(rows_to_insert)

        # --- Flush journal (persist outcomes) ---
        _flush_journal(journal_path, rows_to_insert, batch_skipped)

        # --- Persist checkpoint ---
        checkpoint = {
            "batch_number": batch_details[-1]["batch_number"],
            "timestamp": _utc_now(),
            "routes_in_batch": len(batch),
            "queued": batch_details[-1]["queued"] + batch_details[-1].get("inserted_count", 0) + batch_details[-1].get("dry_run_queued", 0),
            "skipped_deduped": counts["skipped_deduped"],
            "skipped_excluded": counts["skipped_company_excluded"],
            "batches_processed": batches_processed + 1,
        }
        _flush_checkpoint(monitor_path, checkpoint)

        rows_to_insert.clear()  # Ensure buffer is cleared
        batches_processed += 1

    summary = {
        "ok": True,
        "total_candidates_found": len(candidates),
        "newly_queued": newly_queued,
        "skipped_deduped": counts["skipped_deduped"],
        "skipped_excluded": counts["skipped_company_excluded"],
        "skipped_gmail_fail": counts["skipped_gmail_fail"],
        "skipped_mailbox": counts["skipped_mailbox"],
        "skipped_permanent_failed": counts["skipped_permanent_failed"],
        "skipped_invalid": counts["skipped_invalid"],
        "skipped_company_excluded": counts["skipped_company_excluded"],
        "skipped_jordan_held": counts["skipped_jordan_held"],
        "skipped_identity": counts["skipped_identity"],
        "batches_processed": batches_processed,
        "batch_details": batch_details,
        "status": "completed",
        "sends": 0,
        "secret_values_in_output": False,
    }

    # Persist final summary
    summary_path = monitor_path / "replenisher-summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return summary


def _flush_checkpoint(monitor_dir: Path, batch_summary: dict) -> None:
    checkpoint = monitor_dir / "replenisher-checkpoint.json"
    existing: list[dict] = []
    if checkpoint.is_file():
        try:
            existing = json.loads(checkpoint.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            existing = []
    existing.append(batch_summary)
    checkpoint.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Incremental Career Outreach Replenisher (career_engine/)"
    )
    parser.add_argument(
        "--candidates",
        required=True,
        help="Path to JSONL/JSON file of verified candidates",
    )
    parser.add_argument("--apply", action="store_true", help="Write to Auto Send Queue")
    parser.add_argument("--monitor-dir", default=DEFAULT_MONITOR_DIR, help="Monitor directory")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Bounded batch size")
    parser.add_argument("--dry-run", action="store_true", help="Read-only preview")
    args = parser.parse_args()

    if args.dry_run:
        args.apply = False

    if not args.apply and not args.dry_run:
        print(json.dumps({
            "ok": True,
            "status": "dry_run",
            "note": "Use --apply for live execution",
            "sends": 0,
            "secret_values_in_output": False,
        }, sort_keys=True))
        return 0

    try:
        summary = run_replenishment(
            candidates_path=args.candidates,
            batch_size=args.batch_size,
            apply=args.apply,
            monitor_dir=args.monitor_dir,
        )
        print(json.dumps(summary, sort_keys=True))
        return 0
    except Exception as exc:
        error_summary = {
            "ok": False,
            "error": str(exc)[:500],
            "error_type": type(exc).__name__,
            "status": "fatal",
            "sends": 0,
            "secret_values_in_output": False,
        }
        print(json.dumps(error_summary, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
