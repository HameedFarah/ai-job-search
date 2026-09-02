#!/usr/bin/env python3
"""Central Auto Send Queue reconciler for the Career Outreach deterministic sender.

Reconciles the ``Auto Send Queue`` tab in the canonical spreadsheet against
both connected-Gmail accounts, enforces company/domain dedupe, priority
ordering, hard exclusions, permanent-bounce protection, cadence and daily
caps, and the 08:00-19:00 Asia/Riyadh send window.

This module is deliberately *state-only* — it never sends email or mutates
the spreadsheet unless an explicit ``--apply`` flag is given to the caller.
The ``main`` entry-point defaults to dry-run and includes a ``status``
sub-command for read-only inspection.

Integration points with the existing controller:
  - ``runtime/outreach_campaign_controller.py`` – uses the reconciler's
    deterministic ledger and selection to feed the materialize/send phases.
  - ``runtime/send_portfolio_outreach_20260831.py`` – preserves the proven
    transmission/verification/ledger behaviour.

SPREADSHEET_ID  – canonical Career Engine Outreach Master
QUEUE_SHEET_ID  – sheet "Auto Send Queue" (118870206)
QUEUE_COLUMNS   – Queue_ID, Email, Company_or_Office, Source, Priority,
                  Status, Added_At, Sent_At, Gmail_Message_ID, Last_Error,
                  Evidence_or_Notes
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from career_engine.gmail import (
    CAREER_GMAIL_ACCOUNT,
    CAREER_OUTWARD_EMAIL,
    _gmail_access_token,
    run_gws,
    verify_authenticated_mailbox,
)
from runtime.outscraper_sheet_runner import (
    SPREADSHEET_ID,
    rclone_access_token,
    sheets_request,
)

RIYADH = ZoneInfo("Asia/Riyadh")
PRIMARY_GWS_CONFIG_DIR = Path(os.environ.get(
    "CAREER_GWS_PRIMARY_CONFIG_DIR", str(Path.home() / ".config" / "gws")
))
# The actual gws account config for the sender is under the accounts subdirectory.
SENDER_GWS_CONFIG_DIR = Path(os.environ.get(
    "CAREER_GWS_SENDER_CONFIG_DIR",
    str(Path.home() / ".config" / "gws" / "accounts" / "hameedfarah"),
))

# ---------------------------------------------------------------------------
# Spreadsheet identity
# ---------------------------------------------------------------------------
QUEUE_SHEET_NAME = "Auto Send Queue"
QUEUE_SHEET_ID = 118870206

# Canonical columns in sheet order (A-K)
QUEUE_HEADERS = [
    "Queue_ID", "Email", "Company_or_Office", "Source", "Priority",
    "Status", "Added_At", "Sent_At", "Gmail_Message_ID", "Last_Error",
    "Evidence_or_Notes",
]
QUEUE_COL = {name: chr(ord("A") + idx) for idx, name in enumerate(QUEUE_HEADERS)}

# ---------------------------------------------------------------------------
# Hard constants
# ---------------------------------------------------------------------------
VALID_PRIORITIES = {"IMPORTANT", "NORMAL"}
VALID_STATUSES = {
    "PENDING", "SENDING", "SENT", "SKIPPED_ALREADY_CONTACTED",
    "HOLD", "FAILED_PERMANENT", "FAILED_TEMPORARY",
}

DEFAULT_PRIORITY = "NORMAL"
DEFAULT_STATUS = "PENDING"

SENDERS = {CAREER_OUTWARD_EMAIL}  # hameedfarah@gmail.com only

# >=90 sec cadence between sends
CADENCE_SECONDS = 90

MAX_DAILY = 300

# Send window: 08:00 <= local hour < 19 (i.e. 08:00 through 18:59)
WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 19

# Absolute exclusion lists
EXCLUDED_COMPANIES = {"ttw", "Arab Sustainable Architecture", "arab sustainable architecture"}
EXCLUDED_COMPANIES_LOW = {c.lower() for c in EXCLUDED_COMPANIES}
# Known current-employer domain evidence. Other protected-company domains are
# resolved through the canonical master company index.
EXCLUDED_DOMAINS = {"ttwsa.com"}

# Shared/public mailbox providers must never be deduped globally by domain.
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com",
    "proton.me", "protonmail.com",
}

# Hard mailbox-role exclusions from the canonical outreach contract. An
# explicit OWNER_APPROVED marker in Evidence_or_Notes may override executive
# routing only; legal/privacy/finance/abuse/support routes remain blocked.
BLOCKED_MAILBOX_LOCALS = {
    "privacy", "legal", "finance", "investor", "investors", "abuse", "support",
    "billing", "accounts", "compliance", "security",
}
EXECUTIVE_MAILBOX_LOCALS = {"ceo", "executive", "president", "chairman"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")

# Verified cross-domain replacements for companies already contacted in this
# campaign. These exact replacement routes must remain blocked even though the
# replacement domain differs from the historical contacted domain.
KNOWN_ALREADY_CONTACTED_ALIASES = {
    "info@masaralzamel.com": "Al Zamel",
    "info@hmfaqih.com": "Hassan Faqih",
    "info@marina-seas.sa": "Sarai",
}
KNOWN_ALREADY_CONTACTED_DOMAINS = {
    "masaralzamel.com", "alzamel-realestate.com",
    "hmfaqih.com", "hassanfaqih.com",
    "marina-seas.sa", "sarai.com",
}

# Jordan-held (from Engineering offices.xlsx / Sheet1 + HOLD tracker)
JORDAN_HOLD_SOURCE = "Engineering offices.xlsx / Sheet1"
JORDAN_HOLD_KEY = "HOLD_OWNER_JORDAN_ENG_OFFICES"

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_riyadh() -> datetime:
    return datetime.now(RIYADH)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _stable_id_for(email: str, company: str = "") -> str:
    """Generate a stable, deterministic Queue_ID from email (+ optional company)."""
    raw = f"{email.lower().strip()}|{company.strip().lower()}"
    return "OASQ-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()


# ---------------------------------------------------------------------------
# Spreadsheet read (read-only; no mutations)
# ---------------------------------------------------------------------------


def _read_queue_sheet(token: str, spreadsheet_id: str = SPREADSHEET_ID) -> list[dict[str, str]]:
    """Read every row from Auto Send Queue without mutation."""
    encoded = quote(f"{QUEUE_SHEET_NAME}!A:K", safe="!:")
    body = sheets_request(token, "GET",
                          f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded}")
    values = body.get("values")
    if not isinstance(values, list) or len(values) < 2:
        return []

    headers = [str(x) for x in values[0]]
    rows: list[dict[str, str]] = []
    for row_num, raw in enumerate(values[1:], start=2):
        if not isinstance(raw, list):
            continue
        padded = [str(x) for x in raw] + [""] * (len(headers) - len(raw))
        row = dict(zip(headers, padded))
        row["__row_number"] = str(row_num)
        rows.append(row)
    return rows


def _read_master_send_queue(token: str, spreadsheet_id: str = SPREADSHEET_ID) -> list[dict[str, str]]:
    """Read the canonical Send Queue used for company aliases and hard holds."""
    body = sheets_request(
        token,
        "GET",
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/Send%20Queue!A:W",
    )
    values = body.get("values")
    if not isinstance(values, list) or len(values) < 2:
        return []
    headers = [str(x) for x in values[0]]
    rows: list[dict[str, str]] = []
    for raw in values[1:]:
        if not isinstance(raw, list):
            continue
        padded = [str(x) for x in raw] + [""] * (len(headers) - len(raw))
        rows.append(dict(zip(headers, padded)))
    return rows


def _company_key(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value:
        return ""
    value = value.removeprefix("https://").removeprefix("http://").removeprefix("www.").strip(" /")
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    return "".join(ch for ch in value if ch.isalnum() or ch in ".-")


def _email_domain(value: str) -> str:
    email = str(value or "").strip().lower()
    return email.rsplit("@", 1)[1] if "@" in email else ""


def _valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(str(value or "").strip().lower()))


def _is_public_email_domain(domain: str) -> bool:
    return str(domain or "").strip().lower() in PUBLIC_EMAIL_DOMAINS


def _mailbox_local(value: str) -> str:
    email = str(value or "").strip().lower()
    return email.split("@", 1)[0] if "@" in email else ""


def _is_inappropriate_mailbox(email: str, evidence: str = "") -> bool:
    local = _mailbox_local(email)
    if not local:
        return False
    if local in BLOCKED_MAILBOX_LOCALS:
        return True
    if local in EXECUTIVE_MAILBOX_LOCALS:
        marker = str(evidence or "").upper()
        return "OWNER_APPROVED" not in marker and "EXECUTIVE_APPROVED" not in marker
    return False


def _emails_from_master_row(row: dict[str, str]) -> set[str]:
    emails: set[str] = set()
    for key in ("Email", "Outscraper_Replacement_Email"):
        value = str(row.get(key) or "").strip().lower()
        if "@" in value:
            emails.add(value)
    evidence = str(row.get("Outscraper_Evidence") or "").strip()
    if evidence.startswith("{"):
        try:
            payload = json.loads(evidence)
            for key in ("original_email", "replacement_email"):
                value = str(payload.get(key) or "").strip().lower()
                if "@" in value:
                    emails.add(value)
        except json.JSONDecodeError:
            pass
    return emails


def _master_index(rows: list[dict[str, str]]) -> dict[str, Any]:
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
    for row in rows:
        company = _company_key(row.get("Company_or_Office", ""))
        emails = _emails_from_master_row(row)
        for email in emails:
            if company:
                email_to_company[email] = company
            domain = _email_domain(email)
            if company and domain:
                company_domains[company].add(domain)

        state = str(row.get("Send_State") or "").strip().upper()
        terminal = str(row.get("Terminal_Outcome") or "").strip().upper()
        notes = str(row.get("Notes") or "").strip().upper()
        if state == JORDAN_HOLD_KEY:
            jordan_held_emails.update(emails)
            if company:
                jordan_held_companies.add(company)
        if state in hard_states:
            hard_blocked_emails.update(emails)
        if "PERMANENT" in terminal and "BOUNCE" in terminal or "PERMANENT BOUNCE" in notes:
            permanent_bounce_emails.update(emails)

    return {
        "email_to_company": email_to_company,
        "company_domains": company_domains,
        "jordan_held_emails": jordan_held_emails,
        "jordan_held_companies": jordan_held_companies,
        "hard_blocked_emails": hard_blocked_emails,
        "permanent_bounce_emails": permanent_bounce_emails,
    }


# ---------------------------------------------------------------------------
# Row normalisation & defaults
# ---------------------------------------------------------------------------


def normalise_row(row: dict[str, str]) -> dict[str, Any]:
    """Apply blank defaults and validate per the canonical spec."""
    email = str(row.get("Email") or "").strip().lower()
    company = str(row.get("Company_or_Office") or "").strip()
    source = str(row.get("Source") or "").strip()
    priority = str(row.get("Priority") or "").strip().upper()
    status = str(row.get("Status") or "").strip().upper()
    queue_id = str(row.get("Queue_ID") or "").strip()
    added_at = str(row.get("Added_At") or "").strip()
    sent_at = str(row.get("Sent_At") or "").strip()
    gmail_mid = str(row.get("Gmail_Message_ID") or "").strip()
    last_error = str(row.get("Last_Error") or "").strip()
    evidence = str(row.get("Evidence_or_Notes") or "").strip()

    # Blank defaults. Invalid nonblank values and unsafe recipient identities
    # fail closed to HOLD rather than being silently interpreted as sendable.
    errors: list[str] = []
    if not priority:
        priority = DEFAULT_PRIORITY
    elif priority not in VALID_PRIORITIES:
        errors.append(f"invalid priority: {priority}")
        priority = DEFAULT_PRIORITY
    if not status:
        status = DEFAULT_STATUS
    elif status not in VALID_STATUSES:
        errors.append(f"invalid status: {status}")
        status = "HOLD"

    if not email:
        errors.append("missing email")
    elif not _valid_email(email):
        errors.append("malformed email")
    elif _is_inappropriate_mailbox(email, evidence):
        errors.append("inappropriate mailbox")

    if errors:
        status = "HOLD"
    normalise_error = "; ".join(errors)

    if not queue_id and email:
        queue_id = _stable_id_for(email, company)
    if not added_at and email:
        added_at = _utc_now()

    domain = _email_domain(email) if _valid_email(email) else ""

    return {
        "queue_id": queue_id,
        "email": email,
        "company": company,
        "source": source,
        "priority": priority,
        "status": status,
        "added_at": added_at,
        "sent_at": sent_at,
        "gmail_message_id": gmail_mid,
        "last_error": last_error,
        "evidence": evidence,
        "domain": domain,
        "normalise_error": normalise_error,
        "row_number": int(str(row.get("__row_number") or "0") or 0),
    }


# ---------------------------------------------------------------------------
# Queue persistence
# ---------------------------------------------------------------------------


def write_queue_fields(token: str, row_number: int, updates: dict[str, str]) -> None:
    """Write only named Auto Send Queue fields for one existing row."""
    if row_number < 2:
        raise RuntimeError("invalid Auto Send Queue row number")
    data = []
    for field, value in updates.items():
        if field not in QUEUE_COL:
            raise RuntimeError(f"unknown Auto Send Queue field: {field}")
        col = QUEUE_COL[field]
        data.append({
            "range": f"'{QUEUE_SHEET_NAME}'!{col}{row_number}",
            "majorDimension": "ROWS",
            "values": [[str(value)]],
        })
    if not data:
        return
    sheets_request(
        token,
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchUpdate",
        {"valueInputOption": "RAW", "data": data},
    )


def persist_new_row_defaults(token: str, raw: dict[str, str], normalised: dict[str, Any]) -> dict[str, str]:
    """Persist machine-managed defaults for an owner-added queue row."""
    if not normalised.get("email"):
        return {}
    row_number = int(normalised.get("row_number") or 0)
    updates: dict[str, str] = {}
    if not str(raw.get("Queue_ID") or "").strip():
        updates["Queue_ID"] = str(normalised["queue_id"])
    if not str(raw.get("Priority") or "").strip():
        updates["Priority"] = str(normalised["priority"])
    if not str(raw.get("Status") or "").strip():
        updates["Status"] = str(normalised["status"])
    if not str(raw.get("Added_At") or "").strip():
        updates["Added_At"] = str(normalised["added_at"])
    if normalised.get("normalise_error"):
        updates["Status"] = "HOLD"
        updates["Last_Error"] = str(normalised["normalise_error"])
    write_queue_fields(token, row_number, updates)
    return updates


# ---------------------------------------------------------------------------
# Exclusion & dedupe checks
# ---------------------------------------------------------------------------


def _is_jordan_held(row: dict[str, Any]) -> bool:
    """Block rows explicitly tied to the held Jordan tranche.

    ``Engineering offices.xlsx / Sheet1`` contains both KSA and Jordan records,
    so the dataset name alone is not sufficient evidence of a Jordan hold.
    The canonical master ``Send_State`` (loaded separately) or an explicit hold
    marker in the queue source/evidence is authoritative.
    """
    marker = f"{row.get('source', '')} {row.get('evidence', '')}".upper()
    return JORDAN_HOLD_KEY in marker or "JORDAN" in marker and "HOLD" in marker


def _is_company_excluded(company: str, domain: str) -> bool:
    """TTW and Arab Sustainable Architecture always blocked."""
    name = str(company or "").lower().strip()
    clean_domain = str(domain or "").lower().strip()
    if clean_domain in EXCLUDED_DOMAINS:
        return True
    if name in EXCLUDED_COMPANIES_LOW:
        return True
    for exc in EXCLUDED_COMPANIES_LOW:
        if exc and exc in name:
            return True
    return False


def _is_permanently_failed(entry: dict[str, Any]) -> bool:
    """FAILED_PERMANENT entries are never retried."""
    return str(entry.get("status") or "").upper() == "FAILED_PERMANENT"


# ---------------------------------------------------------------------------
# Cross-account Gmail dedupe
# ---------------------------------------------------------------------------


def _gmail_list_paginated(token: str, kind: str, query: str) -> list[dict]:
    """Paginate Gmail list results for sent messages or drafts."""
    key = "messages" if kind == "messages" else "drafts"
    out: list[dict] = []
    page_token = ""
    while True:
        params: list[tuple[str, str | int]] = [("q", query), ("maxResults", 500)]
        if kind == "messages":
            # Use the immutable Gmail SENT label rather than the textual
            # ``in:sent`` search operator, whose parsing/case behavior has
            # already produced misleading campaign-census results.
            params.append(("labelIds", "SENT"))
        if page_token:
            params.append(("pageToken", page_token))
        qs = urlencode(params)
        resp = sheets_request(token, "GET",
                              f"https://gmail.googleapis.com/gmail/v1/users/me/{kind}?{qs}")
        items = resp.get(key, []) or []
        out.extend(items)
        page_token = str(resp.get("nextPageToken") or "")
        if not page_token:
            break
    return out


def _message_to_addrs(token: str, msg_id: str) -> set[str]:
    """Extract To/Cc/Bcc addresses from a sent message or draft."""
    params = [
        ("format", "metadata"),
        ("metadataHeaders", "To"),
        ("metadataHeaders", "Cc"),
        ("metadataHeaders", "Bcc"),
    ]
    qs = urlencode(params)
    data = sheets_request(token, "GET",
                          f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?{qs}")
    headers = (data.get("payload") or {}).get("headers") or []
    values: list[str] = []
    for hdr in headers:
        if str(hdr.get("name", "")).lower() in ("to", "cc", "bcc"):
            values.append(str(hdr.get("value", "")))
    return {
        address.lower().strip()
        for _display, address in getaddresses(values)
        if "@" in address
    }


def _gws_env(config_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = str(config_dir)
    return env


def run_gws_context(args: list[str], config_dir: Path, *, timeout: int = 120) -> dict[str, Any]:
    completed = subprocess.run(
        ["gws", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_gws_env(config_dir),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"gws failed ({completed.returncode}) in config context {config_dir.name}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from gws in config context {config_dir.name}") from exc


def _context_oauth_credentials(config_dir: Path, *, timeout: int = 30) -> dict[str, str]:
    """Load refresh credentials from an isolated Gmail context without exposing secrets.

    Prefer the native gws encrypted export. If that context is not exportable, reuse
    the already-authorized local token file in the same isolated account directory.
    """
    required = ("client_id", "client_secret", "refresh_token")
    completed = subprocess.run(
        ["gws", "auth", "export", "--unmasked"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=_gws_env(config_dir),
    )
    if completed.returncode == 0:
        start = completed.stdout.find("{")
        if start >= 0:
            try:
                raw = json.loads(completed.stdout[start:])
            except json.JSONDecodeError:
                raw = {}
            creds = {key: str(raw.get(key) or "").strip() for key in required}
            if all(creds.values()):
                return creds

    for name in ("google_token.json", "credentials.json", "token.json"):
        path = config_dir / name
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidates = [raw]
        for nested in ("installed", "web", "oauth"):
            value = raw.get(nested) if isinstance(raw, dict) else None
            if isinstance(value, dict):
                candidates.append(value)
        for candidate in candidates:
            creds = {key: str(candidate.get(key) or "").strip() for key in required}
            if all(creds.values()):
                return creds
    raise RuntimeError(f"OAuth refresh credentials unavailable in config context {config_dir.name}")


def gmail_access_token_for_context(config_dir: Path, *, timeout: int = 30) -> str:
    """Refresh an access token from one isolated account context without logging secrets."""
    creds = _context_oauth_credentials(config_dir, timeout=timeout)
    body = urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }).encode("utf-8")
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception as exc:
        raise RuntimeError(f"OAuth refresh failed for config context {config_dir.name}: {type(exc).__name__}") from exc
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"OAuth refresh returned no token for config context {config_dir.name}")
    return token


def _profile_email(config_dir: Path) -> str:
    token = gmail_access_token_for_context(config_dir)
    profile = sheets_request(token, "GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
    return str(profile.get("emailAddress") or "").strip().lower()


def gmail_dedupe_for_queue() -> dict[str, str]:
    """Return {recipient_email: message_id} from BOTH Gmail config contexts."""
    contexts = (
        (CAREER_GMAIL_ACCOUNT, PRIMARY_GWS_CONFIG_DIR),
        (CAREER_OUTWARD_EMAIL, SENDER_GWS_CONFIG_DIR),
    )
    sent_by_email: dict[str, str] = {}
    for expected_email, config_dir in contexts:
        actual = _profile_email(config_dir)
        if actual != expected_email:
            raise RuntimeError(
                f"Gmail config context {config_dir.name} authenticated as {actual or 'unknown'}, expected {expected_email}"
            )
        token = gmail_access_token_for_context(config_dir)
        msgs = _gmail_list_paginated(token, "messages", "after:2026/08/01")
        for message in msgs:
            message_id = str(message.get("id") or "")
            if not message_id:
                continue
            for addr in _message_to_addrs(token, message_id):
                sent_by_email.setdefault(addr, message_id)
    return sent_by_email


def verify_both_accounts_available() -> tuple[bool, str]:
    """Prove both required Gmail identities are live in distinct gws contexts."""
    checks = (
        (CAREER_GMAIL_ACCOUNT, PRIMARY_GWS_CONFIG_DIR),
        (CAREER_OUTWARD_EMAIL, SENDER_GWS_CONFIG_DIR),
    )
    for expected_email, config_dir in checks:
        try:
            actual = _profile_email(config_dir)
        except Exception as exc:
            return False, f"{expected_email} unavailable in {config_dir}: {type(exc).__name__}"
        if actual != expected_email:
            return False, f"{config_dir} authenticated as {actual or 'unknown'}, expected {expected_email}"
    return True, "both Gmail accounts verified in separate gws config contexts"


# ---------------------------------------------------------------------------
# Domain -> company mapping from known sent emails
# ---------------------------------------------------------------------------


def _extract_company_from_domain(domain: str, known: dict[str, str]) -> str:
    """Reverse-map domain to company using known sent records. Returns '' if unknown."""
    return known.get(domain, "")


# ---------------------------------------------------------------------------
# Reconciliation engine
# ---------------------------------------------------------------------------


class QueueLedger:
    """Deterministic, restart-safe local ledger for the Auto Send Queue."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
                    self.entries = payload["entries"]
            except (json.JSONDecodeError, OSError):
                self.entries = {}

    def save(self) -> None:
        _atomic_json(self.path, {"entries": self.entries, "schema": "auto-send-queue-ledger/1",
                                  "updated_at": _utc_now()})

    def get(self, queue_id: str) -> dict[str, Any]:
        return self.entries.get(queue_id, {})

    def upsert(self, queue_id: str, updates: dict[str, Any]) -> None:
        entry = self.entries.get(queue_id, {})
        entry.update(updates)
        entry["updated_at"] = _utc_now()
        self.entries[queue_id] = entry

    def mark_pending(self, queue_id: str, row: dict[str, Any]) -> None:
        if queue_id not in self.entries:
            self.entries[queue_id] = {
                "queue_id": queue_id,
                "email": row["email"],
                "company": row["company"],
                "priority": row["priority"],
                "status": "PENDING",
                "added_at": row["added_at"] or _utc_now(),
                "domain": row["domain"],
            }

    def mark_sending(self, queue_id: str) -> None:
        self.upsert(queue_id, {"status": "SENDING"})

    def mark_sent(self, queue_id: str, gmail_message_id: str, sent_at: str) -> None:
        self.upsert(queue_id, {
            "status": "SENT",
            "gmail_message_id": gmail_message_id,
            "sent_at": sent_at,
        })

    def mark_failed(self, queue_id: str, error: str, permanent: bool = False) -> None:
        status = "FAILED_PERMANENT" if permanent else "FAILED_TEMPORARY"
        self.upsert(queue_id, {"status": status, "last_error": error[:500]})

    def count_by_status(self) -> Counter:
        return Counter(str(e.get("status", "")) for e in self.entries.values())

    def count_by_priority(self) -> Counter:
        return Counter(str(e.get("priority", "")) for e in self.entries.values())


class QueueReconciler:
    """Central reconciler: reads sheet, applies rules, returns selection order."""

    def __init__(self, spreadsheet_token: str, ledger_path: Path | None = None):
        self.sheet_token = spreadsheet_token
        self.raw_rows: list[dict[str, str]] = []
        self.normalised: list[dict[str, Any]] = []
        self.ledger = QueueLedger(ledger_path) if ledger_path else QueueLedger(Path("/dev/null"))
        self.sent_by_email: dict[str, str] = {}
        self.blocked_domains: set[str] = set()
        self.blocked_emails: set[str] = set()
        self.blocked_companies: set[str] = set()
        self.master_rows: list[dict[str, str]] = []
        self.master: dict[str, Any] = _master_index([])

    # ------------------------------------------------------------------
    def read_sheet(self) -> list[dict[str, str]]:
        self.raw_rows = _read_queue_sheet(self.sheet_token)
        self.master_rows = _read_master_send_queue(self.sheet_token)
        self.master = _master_index(self.master_rows)
        return self.raw_rows

    def normalise_all(self) -> list[dict[str, Any]]:
        self.normalised = [normalise_row(row) for row in self.raw_rows]
        return self.normalised

    def fetch_gmail_dedupe(self) -> dict[str, str]:
        ok, detail = verify_both_accounts_available()
        if not ok:
            raise RuntimeError(f"FAIL_CLOSED_BOTH_GMAIL_REQUIRED: {detail}")
        self.sent_by_email = gmail_dedupe_for_queue()
        # Build blocked sets from Gmail-sent emails and canonical master aliases.
        email_to_company = self.master.get("email_to_company", {})
        company_domains = self.master.get("company_domains", {})
        for email, _mid in self.sent_by_email.items():
            email = email.lower().strip()
            self.blocked_emails.add(email)
            domain = _email_domain(email)
            if domain and not _is_public_email_domain(domain):
                self.blocked_domains.add(domain)
            company = str(email_to_company.get(email) or "")
            if company:
                self.blocked_companies.add(company)
                self.blocked_domains.update(
                    d for d in company_domains.get(company, set())
                    if not _is_public_email_domain(d)
                )
        return self.sent_by_email

    def apply_exclusions(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Filter normalised rows through exclusions, dedupe, and permanent-fail."""
        filtered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in self.normalised:
            email = row["email"]
            domain = row["domain"]
            company = row["company"]
            status = row["status"]

            # Skip terminal statuses
            if status in ("SENT", "SKIPPED_ALREADY_CONTACTED", "HOLD"):
                continue

            # Permanent failures never retry
            if status == "FAILED_PERMANENT":
                continue

            company_key = _company_key(company)
            master_company = str(self.master.get("email_to_company", {}).get(email) or "")
            effective_company = master_company or company_key

            # Jordan hold from queue evidence or canonical master Send_State.
            if (
                _is_jordan_held(row)
                or email in self.master.get("jordan_held_emails", set())
                or effective_company in self.master.get("jordan_held_companies", set())
            ):
                skipped.append({**row, "skip_reason": "jordan_held"})
                continue

            # TTW / ASA exclusions
            if _is_company_excluded(company, domain):
                skipped.append({**row, "skip_reason": "company_excluded"})
                continue

            # Verified cross-domain aliases for companies already contacted.
            if email in KNOWN_ALREADY_CONTACTED_ALIASES or domain in KNOWN_ALREADY_CONTACTED_DOMAINS:
                skipped.append({**row, "skip_reason": "known_contacted_company_alias"})
                continue

            # Canonical hard rejects and permanent bounce evidence.
            if email in self.master.get("hard_blocked_emails", set()):
                skipped.append({**row, "skip_reason": "canonical_hard_block"})
                continue
            if email in self.master.get("permanent_bounce_emails", set()):
                skipped.append({**row, "skip_reason": "permanent_bounce"})
                continue

            # Company/domain dedupe against both Gmail histories.
            if email in self.blocked_emails:
                skipped.append({**row, "skip_reason": "already_sent_gmail"})
                continue
            if domain in self.blocked_domains:
                skipped.append({**row, "skip_reason": "domain_sent_gmail"})
                continue
            if effective_company and effective_company in self.blocked_companies:
                skipped.append({**row, "skip_reason": "company_sent_gmail"})
                continue

            filtered.append(row)
        return filtered, skipped

    def apply_ledge_dedupe(self, filtered: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cross-check against durable sent state and collapse duplicate companies."""
        result: list[dict[str, Any]] = []
        sent_companies: set[str] = set()
        sent_domains: set[str] = set()
        for entry in self.ledger.entries.values():
            if str(entry.get("status") or "").upper() != "SENT":
                continue
            sent_company = _company_key(str(entry.get("company") or ""))
            sent_domain = str(entry.get("domain") or _email_domain(str(entry.get("email") or ""))).lower()
            if sent_company:
                sent_companies.add(sent_company)
            if sent_domain and not _is_public_email_domain(sent_domain):
                sent_domains.add(sent_domain)

        selected_companies: set[str] = set()
        selected_domains: set[str] = set()
        for row in filtered:
            email = row["email"]
            domain = row["domain"]
            company_key = _company_key(row["company"])
            master_company = str(self.master.get("email_to_company", {}).get(email) or "")
            effective_company = master_company or company_key

            entry = self.ledger.get(row["queue_id"])
            if str(entry.get("status") or "").upper() == "SENT":
                continue
            if effective_company and effective_company in sent_companies:
                continue
            if domain and domain in sent_domains:
                continue
            if effective_company and effective_company in selected_companies:
                continue
            if domain and not _is_public_email_domain(domain) and domain in selected_domains:
                continue

            result.append(row)
            if effective_company:
                selected_companies.add(effective_company)
            if domain and not _is_public_email_domain(domain):
                selected_domains.add(domain)
        return result

    def sort_by_priority(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """IMPORTANT rows first, then oldest Added_At within same priority."""
        priority_order = {"IMPORTANT": 0, "NORMAL": 1}
        def sort_key(r):
            prio = priority_order.get(r.get("priority", "NORMAL"), 1)
            added = r.get("added_at", "9999")
            return (prio, added, r.get("email", ""))
        return sorted(items, key=sort_key)

    def reconcile(self) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Full reconciliation pipeline. Returns (eligible_rows, skips)."""
        if not self.normalised:
            self.read_sheet()
            self.normalise_all()
        if not self.sent_by_email:
            self.fetch_gmail_dedupe()

        filtered, skip_excluded = self.apply_exclusions()
        deduped = self.apply_ledge_dedupe(filtered)
        ordered = self.sort_by_priority(deduped)

        skips = {
            "excluded": skip_excluded,
            "permanent_fail": [r for r in self.normalised if r["status"] == "FAILED_PERMANENT"],
        }
        return ordered, skips

    # ------------------------------------------------------------------
    # Selection for next send (pure logic, no I/O)
    # ------------------------------------------------------------------

    def select_next(
        self,
        items: list[dict[str, Any]],
        now_utc: datetime | None = None,
        last_send_utc: datetime | None = None,
        sent_today_count: int = 0,
    ) -> dict[str, Any] | None:
        """Return the next eligible row or None if cadence/window/cap blocks."""
        if not items:
            return None

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        local = now_utc.astimezone(RIYADH)

        # Send window check: 08:00 <= hour < 19
        if not (WINDOW_START_HOUR <= local.hour < WINDOW_END_HOUR):
            return None  # outside window

        # Cadence
        if last_send_utc is not None:
            elapsed = (now_utc - last_send_utc).total_seconds()
            if elapsed < CADENCE_SECONDS:
                return None

        # Daily cap
        if sent_today_count >= MAX_DAILY:
            return None

        return items[0]

    def seconds_until_window_open(self, now_utc: datetime | None = None) -> float:
        """Seconds until the send window opens (or closes if inside)."""
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        local = now_utc.astimezone(RIYADH)

        if local.hour >= WINDOW_START_HOUR and local.hour < WINDOW_END_HOUR:
            # We're in the window — return seconds until it closes
            if local.hour == WINDOW_END_HOUR - 1 and local.minute >= 0:
                target = local.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
                return max(0.0, (target - local).total_seconds())
            return 0.0

        if local.hour < WINDOW_START_HOUR:
            target = local.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
            return max(0.0, (target - local).total_seconds())

        # After window closes today
        tomorrow_start = (local + timedelta(days=1)).replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
        return max(0.0, (tomorrow_start - local).total_seconds())


# ---------------------------------------------------------------------------
# Status / dry-run command
# ---------------------------------------------------------------------------


def status_command(args: argparse.Namespace) -> dict[str, Any]:
    """Read-only dry-run status: inspect live queue and Gmail dedupe without mutating."""
    token = rclone_access_token()
    reconciler = QueueReconciler(token, ledger_path=Path(args.ledger if hasattr(args, 'ledger') and args.ledger else "/tmp/oasq-ledger.json"))

    # Read sheet
    raw = reconciler.read_sheet()
    normalised = reconciler.normalise_all()

    # Gmail dedupe
    gmail_sent = reconciler.fetch_gmail_dedupe()

    # Full reconcile
    eligible, skips = reconciler.reconcile()

    # Priority distribution
    priority_dist = Counter(r["priority"] for r in normalised)
    status_dist = Counter(r["status"] for r in normalised)

    # Ledger state
    ledger_counts = reconciler.ledger.count_by_status()
    ledger_priorities = reconciler.ledger.count_by_priority()

    # Domain dedupe view
    domains_seen: dict[str, list[str]] = defaultdict(list)
    for r in normalised:
        if r["domain"]:
            domains_seen[r["domain"]].append(r["email"])

    now = datetime.now(timezone.utc)
    local = now.astimezone(RIYADH)
    window_open = WINDOW_START_HOUR <= local.hour < WINDOW_END_HOUR

    result = {
        "ok": True,
        "timestamp_utc": _utc_now(),
        "riyadh_local": local.isoformat(),
        "send_window_open": window_open,
        "spreadsheet": {
            "spreadsheet_id": SPREADSHEET_ID,
            "sheet_name": QUEUE_SHEET_NAME,
            "total_rows": len(raw),
            "normalised_rows": len(normalised),
            "priority_distribution": dict(priority_dist),
            "status_distribution": dict(status_dist),
        },
        "gmail_dedupe": {
            "primary_account": CAREER_GMAIL_ACCOUNT,
            "sent_count": len(gmail_sent),
            "unique_domains": len({e.split("@", 1)[1] for e in gmail_sent if "@" in e}),
        },
        "reconciliation": {
            "eligible_for_send": len(eligible),
            "skipped": {k: len(v) for k, v in skips.items()},
            "top_eligible": [
                {"queue_id": r["queue_id"], "email": r["email"], "company": r["company"], "priority": r["priority"]}
                for r in eligible[:5]
            ],
        },
        "ledger": {
            "total_entries": len(reconciler.ledger.entries),
            "status_distribution": dict(ledger_counts),
            "priority_distribution": dict(ledger_priorities),
        },
        "domain_coverage": {d: len(addrs) for d, addrs in sorted(domains_seen.items())[:20]},
        "blocked_domains_sample": sorted(reconciler.blocked_domains)[:20],
        "mode": "dry_run",
    }
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto Send Queue reconciler (dry-run by default)")
    sub = parser.add_subparsers(dest="command")

    # status sub-command
    status_p = sub.add_parser("status", help="Read-only queue & dedupe inspection (dry-run)")
    status_p.add_argument("--ledger", default="/tmp/oasq-ledger.json", help="Ledger file path")

    # reconcile sub-command (dry-run, no sheet mutation)
    reconc_p = sub.add_parser("reconcile", help="Run reconciliation and print eligible order")
    reconc_p.add_argument("--ledger", default="/tmp/oasq-ledger.json", help="Ledger file path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "status":
            result = status_command(args)
        elif args.command == "reconcile":
            token = rclone_access_token()
            r = QueueReconciler(token, ledger_path=Path(args.ledger))
            r.read_sheet()
            r.normalise_all()
            r.fetch_gmail_dedupe()
            eligible, skips = r.reconcile()
            result = {
                "command": "reconcile",
                "mode": "dry_run",
                "eligible_count": len(eligible),
                "skipped_counts": {k: len(v) for k, v in skips.items()},
                "eligible_rows": [
                    {k: v for k, v in r.items()} for r in eligible
                ],
            }
        else:
            parser.print_help()
            return 0
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=not isinstance(result.get("eligible_rows"), list)))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
