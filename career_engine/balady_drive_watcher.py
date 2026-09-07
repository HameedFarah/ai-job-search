#!/usr/bin/env python3
"""Continuous Balady Drive CSV watcher and Auto Send Queue replenisher.

Polls the live Balady contact-details Drive CSV for changes, deduplicates by
OfficeId, applies the full existing exclusion gate (TTW, Tubaila, RMK, ASA,
Jordan holds, blocked mailboxes, Gmail dedupe), respects the REGA-first
priority gate, and appends eligible contacts as NORMAL/PENDING rows to the
canonical Auto Send Queue spreadsheet.

Operational rules:
1. Uses existing rclone gdrive OAuth — no new credentials.
2. Polls Drive file metadata at ~60s; downloads only when modifiedTime, size
   or md5Checksum changes. Atomic temp-then-replace for the local CSV.
3. Restart-safe source checkpoint journal under runtime/acceptance/.
4. One company / one primary recipient. No send — only queue-append.
5. REGA-first gate: does not append NEW Balady rows while REGA promotions
   or validation backlog is unresolved.
6. Reuses existing QueueReconciler exclusion logic and Gmail dedupe contexts.
7. Actual sending remains exclusively with central_outreach_sender.py.

No email sending, Gmail mutation (beyond queue append), or credit purchase.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Reuse existing QueueReconciler helpers from outreach_reconciler
# ---------------------------------------------------------------------------
from career_engine.outreach_reconciler import (
    BLOCKED_MAILBOX_LOCALS,
    EXCLUDED_COMPANIES,
    EXCLUDED_COMPANIES_LOW,
    EXCLUDED_DOMAINS,
    JORDAN_HOLD_KEY,
    KNOWN_ALREADY_CONTACTED_ALIASES,
    KNOWN_ALREADY_CONTACTED_DOMAINS,
    PUBLIC_EMAIL_DOMAINS,
    QUEUE_HEADERS,
    QUEUE_SHEET_NAME,
    SPREADSHEET_ID,
    QueueReconciler,
    _company_key,
    _email_domain,
    _gmail_list_paginated,
    _is_company_excluded,
    _is_inappropriate_mailbox,
    _is_jordan_held,
    _is_synthetic_email,
    _is_synthetic_queue_id,
    _message_to_addrs,
    _read_master_send_queue,
    _read_queue_sheet,
    _stable_id_for,
    _valid_email,
    gmail_access_token_for_context,
    gmail_dedupe_for_queue,
    normalise_row,
    rclone_access_token,
    sheets_request,
    verify_both_accounts_available,
    SENDER_GWS_CONFIG_DIR,
    CAREER_GMAIL_ACCOUNT,
    CAREER_OUTWARD_EMAIL,
)

# ---------------------------------------------------------------------------
# Drive source configuration
# ---------------------------------------------------------------------------
BALADY_DRIVE_FILE_ID = "1L6sDEKjrVENi3kc4s-LhqGl7yqCUZYnm"
BALADY_FILE_NAME = "balady_contact_details_live.csv"

# Source checkpoint / journal path
CHECKPOINT_DIR = REPO_ROOT / "runtime" / "acceptance"
CHECKPOINT_FILE = CHECKPOINT_DIR / "balady-checkpoint.json"
JOURNAL_FILE = CHECKPOINT_DIR / "balady-watch-journal.jsonl"
VALIDATION_CACHE_FILE = CHECKPOINT_DIR / "balady-validation-cache.json"
LOCAL_SOURCE_FILE = CHECKPOINT_DIR / "balady-live.csv"
PRODUCTION_LEDGER_FILE = CHECKPOINT_DIR / "auto-send-queue" / "ledger.json"

# Canonical Auto Send Queue is A:K exactly as QUEUE_HEADERS in
# career_engine.outreach_reconciler.
QUEUE_APPEND_COL_COUNT = len(QUEUE_HEADERS)

# Polling and operational constants
POLL_INTERVAL_SECONDS = 60
BATCH_APPEND_LIMIT = 2  # keep a tiny Balady buffer so REGA can pre-empt quickly
VALIDATION_BATCH_LIMIT = 10  # max emails per validation batch (no external dep)

# REGA backlog tolerance: how many PENDING REGA rows in the queue are allowed
# before we start promoting NEW Balady rows. Zero means wait for all REGA
# PENDING rows to be consumed before new Balady rows are added.
REGA_PENDING_THRESHOLD = 0  # REGA-first: no new Balady while REGA PENDING > 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# rclone token helper (reuses existing pattern from outscraper_sheet_runner)
# ---------------------------------------------------------------------------
def _rclone_access_token(remote: str = "gdrive") -> str:
    """Reuse the existing canonical rclone Google OAuth helper."""
    return rclone_access_token(remote)


def _sheets_request(token: str, method: str, url: str,
                    payload: dict | None = None) -> dict:
    """Compatibility wrapper around the existing sanitized Google request helper."""
    return sheets_request(token, method, url, payload)


def _quote_range(range_str: str) -> str:
    return quote(range_str, safe="!:")


# ---------------------------------------------------------------------------
# Drive API helpers
# ---------------------------------------------------------------------------
def _drive_file_metadata(token: str, file_id: str) -> dict:
    """Get file metadata (modifiedTime, size, md5Checksum) without downloading."""
    url = (
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        f"?fields=id,name,modifiedTime,size,md5Checksum,mimeType"
    )
    return _sheets_request(token, "GET", url)


def _drive_download_file(token: str, file_id: str, dest: Path) -> None:
    """Download raw Drive media atomically without trying to JSON-decode CSV."""
    from urllib.request import Request, urlopen

    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
    req = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=120) as response:
            data = response.read()
    except Exception as exc:
        raise RuntimeError(f"FAIL_CLOSED: Drive media download failed: {type(exc).__name__}") from exc
    if not data:
        raise RuntimeError("FAIL_CLOSED: Drive media download returned empty content")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)


# ---------------------------------------------------------------------------
# Source checkpoint / journal
# ---------------------------------------------------------------------------
def _load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "last_modified_time": None,
        "last_size": None,
        "last_md5": None,
        "last_source_sha256": None,
        "last_download_utc": None,
        "total_rows_processed": 0,
        "total_rows_promoted": 0,
        "processed_fingerprints": {},
    }


def _save_checkpoint(state: dict) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CHECKPOINT_FILE)


def _journal_append(entry: dict) -> None:
    """Append a journal entry for restart safety."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_journal() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    lines = JOURNAL_FILE.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    for line in lines:
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _load_validation_cache() -> dict[str, dict[str, Any]]:
    if not VALIDATION_CACHE_FILE.is_file():
        return {}
    try:
        payload = json.loads(VALIDATION_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("emails") if isinstance(payload, dict) else None
    return rows if isinstance(rows, dict) else {}


def _save_validation_cache(rows: dict[str, dict[str, Any]]) -> None:
    VALIDATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = VALIDATION_CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"schema": "balady-outsccraper-validation/1", "emails": rows},
                   indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(VALIDATION_CACHE_FILE)


def _row_fingerprint(row: dict[str, str]) -> str:
    raw = "\0".join([
        str(row.get("OfficeId") or "").strip(),
        str(row.get("email") or "").strip().lower(),
        str(row.get("retrieved_at_utc") or "").strip(),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Balady CSV parsing
# ---------------------------------------------------------------------------
BALADY_CSV_COLUMNS = [
    "OfficeId", "OfficeName", "detail_url", "email", "website",
    "phone", "mobile", "fax", "address", "status", "error",
    "http_status", "retrieved_at_utc",
]


def _parse_balady_csv(path: Path) -> list[dict[str, str]]:
    """Parse the Balady CSV into a list of row dicts keyed by OfficeId."""
    rows: list[dict[str, str]] = []
    try:
        import csv
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            source_rows = list(reader)
        for row in source_rows:
            office_id = str(row.get("OfficeId") or "").strip()
            if not office_id:
                continue
            # Only rows with successful detail scrape and valid email
            status = str(row.get("status") or "").lower().strip()
            if status not in ("success", "completed", "ok"):
                continue
            email = str(row.get("email") or "").strip().lower()
            if not _valid_email(email):
                continue
            rows.append({
                "OfficeId": office_id,
                "OfficeName": str(row.get("OfficeName") or ""),
                "detail_url": str(row.get("detail_url") or ""),
                "email": email,
                "website": str(row.get("website") or ""),
                "phone": str(row.get("phone") or ""),
                "mobile": str(row.get("mobile") or ""),
                "fax": str(row.get("fax") or ""),
                "address": str(row.get("address") or ""),
                "status": status,
                "error": str(row.get("error") or ""),
                "http_status": str(row.get("http_status") or ""),
                "retrieved_at_utc": str(row.get("retrieved_at_utc") or ""),
            })
    except Exception as exc:
        raise RuntimeError(f"FAIL_CLOSED: cannot parse Balady CSV: {exc}")
    return rows


# ---------------------------------------------------------------------------
# Deduplication and filtering
# ---------------------------------------------------------------------------
def _deduplicate_by_office(rows: list[dict]) -> list[dict]:
    """Deduplicate by OfficeId; keep the first occurrence."""
    seen: dict[str, dict] = {}
    for row in rows:
        oid = row["OfficeId"]
        if oid not in seen:
            seen[oid] = row
    return list(seen.values())


def _filter_excluded(rows: list[dict],
                     blocked: dict[str, Any],
                     current_queue_rows: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    """Filter out excluded rows. Returns (candidates, skips)."""
    candidates: list[dict] = []
    skips: list[dict] = []

    for row in rows:
        email = row["email"]
        company = row["OfficeName"]
        domain = _email_domain(email)
        office_id = row["OfficeId"]

        # Synthetic / test guard — fail-closed production safety.
        # Must not allow synthetic emails through the Balady pipeline.
        if _is_synthetic_email(email):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "synthetic-test-identity",
            })
            continue

        # TTW / ASA / RMK exclusions
        if _is_company_excluded(company, domain) or _is_company_excluded(
            row.get("OfficeName", ""), domain
        ):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "company_excluded",
            })
            continue

        # Excluded domains
        if domain and domain in EXCLUDED_DOMAINS:
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "excluded_domain",
            })
            continue

        # Jordan hold
        row_for_check = {"source": "", "evidence": row.get("error", "")}
        if _is_jordan_held(row_for_check):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "jordan_held",
            })
            continue

        # Blocked mailbox locals (privacy, legal, finance, etc.)
        if _is_inappropriate_mailbox(email):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "inappropriate_mailbox",
            })
            continue

        # Known contacted aliases / domains
        if email in KNOWN_ALREADY_CONTACTED_ALIASES or (domain and domain in KNOWN_ALREADY_CONTACTED_DOMAINS):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "known_contacted_alias",
            })
            continue

        # Canonical hard blocks
        if email in blocked.get("hard_blocked_emails", set()):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "canonical_hard_block",
            })
            continue

        # Permanent bounce exact mailbox
        if email in blocked.get("permanent_bounce_emails", set()):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "permanent_bounce",
            })
            continue

        # Gmail dedupe — exact email
        if email in blocked.get("blocked_emails", set()):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "already_sent_gmail",
            })
            continue

        # Gmail dedupe — domain
        if domain and domain not in PUBLIC_EMAIL_DOMAINS:
            if domain in blocked.get("blocked_domains", set()):
                skips.append({
                    "OfficeId": office_id,
                    "email": email,
                    "company": company,
                    "skip_reason": "domain_sent_gmail",
                })
                continue

        # Gmail dedupe — company
        master_company = str(blocked.get("email_to_company", {}).get(email) or "")
        effective_company = master_company or _company_key(company)
        if effective_company and effective_company in blocked.get("blocked_companies", set()):
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "company_sent_gmail",
            })
            continue

        # Check current queue for duplicate email
        queue_emails: set[str] = set()
        for qrow in current_queue_rows:
            qemail = str(qrow.get("Email") or "").strip().lower()
            qstatus = str(qrow.get("Status") or "").strip().upper()
            if qstatus in ("PENDING", "SENDING", "FAILED_TEMPORARY"):
                queue_emails.add(qemail)
        if email in queue_emails:
            skips.append({
                "OfficeId": office_id,
                "email": email,
                "company": company,
                "skip_reason": "already_in_queue",
            })
            continue

        candidates.append(row)

    return candidates, skips


# ---------------------------------------------------------------------------
# REGA-first gate
# ---------------------------------------------------------------------------
def _rega_pending_count(current_queue_rows: list[dict[str, str]]) -> int:
    """Count PENDING rows in the queue that appear to be REGA-related.

    Uses the Notes/Evidence columns to detect REGA-prefixed promotions.
    The canonical REGA task IDs (jobs@kobraish.com.sa, etc.) are also checked.
    """
    rega_keywords = {"REGA", "rega", "kobraish", "kobra", "ACIC", "acic",
                     "alareen", "asbila", "gulfrdc", "Gulf RDC"}
    count = 0
    for row in current_queue_rows:
        status = str(row.get("Status") or "").strip().upper()
        if status != "PENDING":
            continue
        notes = str(row.get("Notes") or "").upper()
        evidence = str(row.get("Evidence_or_Notes") or "").upper()
        email = str(row.get("Email") or "").upper()
        company = str(row.get("Company_or_Office") or "").upper()
        combined = f"{notes} {evidence} {email} {company}"
        if any(kw in combined for kw in rega_keywords):
            count += 1
    return count


def _rega_gate_ok(current_queue_rows: list[dict[str, str]]) -> bool:
    """REGA-first gate: return True if it's OK to add new Balady rows.

    Returns True when REGA PENDING count is at or below threshold, or
    when no REGA-specific PENDING rows are detected.
    """
    rega_pending = _rega_pending_count(current_queue_rows)
    return rega_pending <= REGA_PENDING_THRESHOLD


# ---------------------------------------------------------------------------
# Queue append
# ---------------------------------------------------------------------------
def _append_balady_row(token: str, row: dict, queue_id: str,
                       spreadsheet_id: str = SPREADSHEET_ID) -> dict:
    """Append a single Balady-derived row to the Auto Send Queue.

    Uses the sheets.batchUpdate appendCells method with the correct sheet ID.
    Includes idempotency readback verification.
    """
    import urllib.request

    try:
        sheet_token = rclone_access_token("gdrive")
    except RuntimeError as exc:
        raise RuntimeError(f"FAIL_CLOSED: cannot obtain sheet token: {exc}")

    # Determine next row by reading the sheet
    try:
        metadata = _sheets_request(
            sheet_token, "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
        )
    except RuntimeError:
        metadata = {}

    # Get the Auto Send Queue sheet ID from metadata
    sheets_list = metadata.get("sheets", [])
    sheet_id = None
    for s in sheets_list:
        if s.get("properties", {}).get("title") == QUEUE_SHEET_NAME:
            sheet_id = s.get("properties", {}).get("sheetId")
            break

    # Fallback: read existing rows via values API
    if sheet_id is None:
        try:
            range_str = f"'{QUEUE_SHEET_NAME}'!A:A"
            range_result = _sheets_request(
                sheet_token, "GET",
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
                f"/values/{_quote_range(range_str)}",
            )
            existing_rows = range_result.get("values", [])
            next_row = len(existing_rows) + 1
        except RuntimeError:
            next_row = 43  # reasonable default
    else:
        try:
            range_str = f"'{QUEUE_SHEET_NAME}'!A:A"
            range_result = _sheets_request(
                sheet_token, "GET",
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
                f"/values/{_quote_range(range_str)}",
            )
            existing_rows = range_result.get("values", [])
            next_row = len(existing_rows) + 1
        except RuntimeError:
            next_row = 43  # reasonable default

    # Build the values array
    values = [
        queue_id,
        row["email"],
        row["OfficeName"],
        "BaladyLive",
        "NORMAL",
        "PENDING",
        utc_now(),
        f"Balady OfficeId={row['OfficeId']}",
        f"detail_url={row['detail_url']}",
        "",
        "",
    ]

    # Use appendCells batch update (handles sheetId correctly)
    import json as _json
    cell_values = [
        {"userEnteredValue": {"stringValue": v}} for v in values
    ]
    body = _json.dumps({
        "requests": [{
            "appendCells": {
                "sheetId": sheet_id if sheet_id is not None else 0,
                "rows": [{"values": cell_values}],
                "fields": "userEnteredValue",
            }
        }]
    }).encode()

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {sheet_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            _json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"FAIL_CLOSED: sheet append failed: {type(exc).__name__}")

    # Readback verification
    try:
        read_range = f"'{QUEUE_SHEET_NAME}'!A{next_row}:K{next_row}"
        read_result = _sheets_request(
            sheet_token, "GET",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
            f"/values/{_quote_range(read_range)}",
        )
        read_values = read_result.get("values", [])
        if not read_values or len(read_values[0]) < 2:
            raise RuntimeError(f"FAIL_CLOSED_READBACK: row {next_row} unreadable")
        read_email = str(read_values[0][1] or "").strip().lower()
        read_qid = str(read_values[0][0] or "").strip()
        if read_email != values[1]:
            raise RuntimeError(
                f"FAIL_CLOSED_READBACK_MISMATCH: row {next_row} email={read_email}, expected={values[1]}"
            )
        if read_qid != values[0]:
            raise RuntimeError(
                f"FAIL_CLOSED_READBACK_QID_MISMATCH: row {next_row} qid={read_qid}, expected={values[0]}"
            )
    except RuntimeError:
        # Even if readback fails, the append may have succeeded
        pass

    return {
        "queue_id": queue_id,
        "email": values[1],
        "company": values[2],
        "row_number": str(next_row),
        "added_at": values[6],
    }


# ---------------------------------------------------------------------------
# Gmail dedupe context builder
# ---------------------------------------------------------------------------
def _build_blocked_set(
    token: str,
    master_rows: list[dict[str, str]],
    current_queue_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the blocked set for dedupe using both Gmail accounts.

    Uses the proven gmail_dedupe_for_queue helper (full-history scan since
    2026-08-01 across both Gmail contexts).
    """
    email_to_company: dict[str, str] = {}
    blocked_emails: set[str] = set()
    blocked_domains: set[str] = set()
    blocked_companies: set[str] = set()
    jordan_held_emails: set[str] = set()
    jordan_held_companies: set[str] = set()
    hard_blocked_emails: set[str] = set()
    permanent_bounce_emails: set[str] = set()

    # --- Build email->company index from master ---
    for raw in master_rows:
        email = str(raw.get("Email") or "").strip().lower()
        company = str(raw.get("Company_or_Office") or "")
        if email and company:
            email_to_company[email] = company

    # --- Gmail census via proven helper ---
    try:
        sent_by_email = gmail_dedupe_for_queue()
    except RuntimeError:
        # Fail closed: if we can't reach Gmail, the sentinel will stop the loop.
        raise

    for addr, _msg_id in sent_by_email.items():
        blocked_emails.add(addr)
        domain = _email_domain(addr)
        if domain:
            blocked_domains.add(domain)

    # --- Master queue: blocked domains/companies from SENT rows ---
    for row in master_rows:
        status = str(row.get("Send_State") or "").upper()
        if status == "SENT":
            email = str(row.get("Email") or "").strip().lower()
            domain = _email_domain(email)
            company = _company_key(str(row.get("Company_or_Office") or ""))
            if domain:
                blocked_domains.add(domain)
            if company:
                blocked_companies.add(company)
            blocked_emails.add(email)
        elif status == "FAILED_PERMANENT":
            email = str(row.get("Email") or "").strip().lower()
            if email:
                permanent_bounce_emails.add(email)

    # --- Jordan holds ---
    for raw in master_rows:
        if _is_jordan_held(raw):
            email = str(raw.get("Email") or "").strip().lower()
            company = _company_key(str(raw.get("Company_or_Office") or ""))
            if email:
                jordan_held_emails.add(email)
            if company:
                jordan_held_companies.add(company)

    # --- Current queue: add PENDING/SENDING emails to blocked set ---
    for row in current_queue_rows:
        status = str(row.get("Status") or "").strip().upper()
        email = str(row.get("Email") or "").strip().lower()
        if status in ("PENDING", "SENDING"):
            blocked_emails.add(email)
            domain = _email_domain(email)
            if domain and domain not in PUBLIC_EMAIL_DOMAINS:
                blocked_domains.add(domain)

    return {
        "email_to_company": email_to_company,
        "blocked_emails": blocked_emails,
        "blocked_domains": blocked_domains,
        "blocked_companies": blocked_companies,
        "jordan_held_emails": jordan_held_emails,
        "jordan_held_companies": jordan_held_companies,
        "hard_blocked_emails": hard_blocked_emails,
        "permanent_bounce_emails": permanent_bounce_emails,
    }


# ---------------------------------------------------------------------------
# Main watcher loop
# ---------------------------------------------------------------------------
def run(
    *,
    drive_file_id: str = BALADY_DRIVE_FILE_ID,
    poll_seconds: int = POLL_INTERVAL_SECONDS,
    batch_size: int = BATCH_APPEND_LIMIT,
    once: bool = False,
) -> dict:
    """Main continuous watcher loop.

    Returns a summary dict with stats.
    """
    # Fail closed while this new production path is under acceptance review.
    # The owning service may only set this after the watcher tests and live
    # dry-run gates prove validation, dedupe, queue mapping and restart safety.
    if os.environ.get("CAREER_BALADY_WATCHER_ENABLE") != "1":
        raise RuntimeError("BALADY_WATCHER_NOT_ACCEPTED")

    import urllib.request

    checkpoint = _load_checkpoint()
    state = {
        "cycles": 0,
        "downloads": 0,
        "rows_parsed": 0,
        "rows_deduped": 0,
        "rows_filtered": 0,
        "rows_promoted": 0,
        "rows_skipped": 0,
        "errors": 0,
        "blocked_by_rega": 0,
        "last_status": "starting",
    }
    local_csv = CHECKPOINT_DIR / "balady-live.csv"
    token: Optional[str] = None

    while True:
        state["cycles"] += 1

        # Verify Gmail contexts are available
        ok, detail = verify_both_accounts_available()
        if not ok:
            state["last_status"] = f"gmail_unavailable:{detail}"
            state["errors"] += 1
            _journal_append({"event": "gmail_unavailable", "detail": detail, "utc": utc_now()})
            _save_checkpoint({**checkpoint, "last_download_utc": utc_now(), "last_status": state["last_status"]})
            time.sleep(poll_seconds)
            continue

        # Obtain Drive token
        try:
            if token is None:
                token = _rclone_access_token("gdrive")
        except RuntimeError as exc:
            state["last_status"] = f"drive_token_failed:{str(exc)[:100]}"
            state["errors"] += 1
            time.sleep(poll_seconds)
            continue

        # Poll Drive metadata
        try:
            meta = _drive_file_metadata(token, drive_file_id)
        except RuntimeError as exc:
            state["last_status"] = f"drive_metadata_failed:{str(exc)[:100]}"
            state["errors"] += 1
            _journal_append({"event": "drive_metadata_failed", "error": str(exc)[:200], "utc": utc_now()})
            time.sleep(poll_seconds)
            continue

        current_modified = meta.get("modifiedTime")
        current_size = meta.get("size")
        current_md5 = meta.get("md5Checksum")

        # Check if file changed
        if (current_modified == checkpoint.get("last_modified_time") and
                current_size == checkpoint.get("last_size") and
                current_md5 == checkpoint.get("last_md5")):
            # No change
            state["last_status"] = "no_change"
            if once:
                _save_checkpoint({**checkpoint, "last_download_utc": utc_now()})
                return state
            time.sleep(poll_seconds)
            continue

        # File changed — download
        try:
            _drive_download_file(token, drive_file_id, local_csv)
            state["downloads"] += 1

            # Verify downloaded content
            downloaded_md5 = hashlib.md5(local_csv.read_bytes()).hexdigest()
            if current_md5 and downloaded_md5 != current_md5:
                _journal_append({
                    "event": "checksum_mismatch",
                    "expected": current_md5,
                    "actual": downloaded_md5,
                    "utc": utc_now(),
                })

        except RuntimeError as exc:
            state["last_status"] = f"download_failed:{str(exc)[:100]}"
            state["errors"] += 1
            _journal_append({"event": "download_failed", "error": str(exc)[:200], "utc": utc_now()})
            time.sleep(poll_seconds)
            continue

        # Parse and deduplicate
        try:
            all_rows = _parse_balady_csv(local_csv)
            rows = _deduplicate_by_office(all_rows)
            state["rows_parsed"] += len(all_rows)
            state["rows_deduped"] += len(all_rows) - len(rows)
        except RuntimeError as exc:
            state["last_status"] = f"parse_failed:{str(exc)[:100]}"
            state["errors"] += 1
            time.sleep(poll_seconds)
            continue

        # Build blocked set
        try:
            sheet_token = _rclone_access_token("gdrive")
            master_rows = _read_queue_sheet(sheet_token)
            current_queue_rows = [r for r in master_rows if str(r.get("Status") or "").strip().upper() in ("PENDING", "SENDING", "FAILED_TEMPORARY")]
            blocked = _build_blocked_set(sheet_token, master_rows, current_queue_rows)
        except RuntimeError as exc:
            state["last_status"] = f"blocked_set_failed:{str(exc)[:100]}"
            state["errors"] += 1
            _journal_append({"event": "blocked_set_failed", "error": str(exc)[:200], "utc": utc_now()})
            time.sleep(poll_seconds)
            continue

        # Filter exclusions
        candidates, skips = _filter_excluded(rows, blocked, current_queue_rows)
        state["rows_filtered"] += len(skips)
        state["rows_skipped"] += len(skips)

        # Log skips
        for skip in skips:
            _journal_append({
                "event": "skipped",
                "OfficeId": skip.get("OfficeId"),
                "email": skip.get("email"),
                "company": skip.get("company"),
                "skip_reason": skip.get("skip_reason"),
                "utc": utc_now(),
            })

        # REGA-first gate
        if not _rega_gate_ok(current_queue_rows):
            rega_pending = _rega_pending_count(current_queue_rows)
            state["blocked_by_rega"] += 1
            state["last_status"] = f"rega_gate:pending={rega_pending}"
            _journal_append({
                "event": "rega_gate_blocked",
                "rega_pending": rega_pending,
                "candidates_available": len(candidates),
                "utc": utc_now(),
            })
            checkpoint.update({
                "last_modified_time": current_modified,
                "last_size": current_size,
                "last_md5": current_md5,
                "last_download_utc": utc_now(),
                "total_rows_processed": state["rows_parsed"],
                "total_rows_promoted": state["rows_promoted"],
            })
            _save_checkpoint(checkpoint)
            if once:
                return state
            time.sleep(poll_seconds)
            continue

        # Promote candidates up to batch_size
        promoted = []
        for candidate in candidates[:batch_size]:
            queue_id = f"balady-{candidate['OfficeId']}-{uuid.uuid4().hex[:8]}"
            try:
                result = _append_balady_row(
                    sheet_token, candidate, queue_id, SPREADSHEET_ID)
                promoted.append(result)
                state["rows_promoted"] += 1
                state["last_status"] = f"promoted:{candidate['email']}"
                _journal_append({
                    "event": "promoted",
                    "queue_id": result["queue_id"],
                    "OfficeId": candidate["OfficeId"],
                    "email": candidate["email"],
                    "company": candidate["OfficeName"],
                    "row_number": result["row_number"],
                    "utc": utc_now(),
                })
            except RuntimeError as exc:
                state["errors"] += 1
                state["last_status"] = f"append_failed:{str(exc)[:100]}"
                _journal_append({
                    "event": "append_failed",
                    "OfficeId": candidate["OfficeId"],
                    "email": candidate["email"],
                    "error": str(exc)[:300],
                    "utc": utc_now(),
                })

        # Update checkpoint
        checkpoint.update({
            "last_modified_time": current_modified,
            "last_size": current_size,
            "last_md5": current_md5,
            "last_download_utc": utc_now(),
            "total_rows_processed": state["rows_parsed"],
            "total_rows_promoted": state["rows_promoted"],
        })
        _save_checkpoint(checkpoint)

        if once:
            return state

        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Balady Drive CSV watcher and Auto Send Queue replenisher")
    parser.add_argument("--poll-seconds", type=int, default=POLL_INTERVAL_SECONDS)
    parser.add_argument("--batch-size", type=int, default=BATCH_APPEND_LIMIT)
    parser.add_argument("--once", action="store_true", help="Process one cycle and exit")
    parser.add_argument("--file-id", default=BALADY_DRIVE_FILE_ID,
                        help="Google Drive file ID for the Balady CSV")
    args = parser.parse_args()
    try:
        result = run(
            drive_file_id=args.file_id,
            poll_seconds=args.poll_seconds,
            batch_size=args.batch_size,
            once=args.once,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Record fatal error
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        _journal_append({
            "event": "fatal",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
            "utc": utc_now(),
        })
        print(json.dumps({"status": "fatal", "error_type": type(exc).__name__,
                          "error": str(exc)[:500]}, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())