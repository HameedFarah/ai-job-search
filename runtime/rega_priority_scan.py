"""Portal-first, REGA-only employer-route enrichment.

The live Master Tracker is authoritative. This runtime discovers a verified
company domain, checks first-party employment routes before paid enrichment,
persists ATS/form successes back to Master Tracker, and queues only mailbox
validator RECEIVING results. It has no Gmail draft/send path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ["FIRECRAWL_ROTATED_CONFIRMED"] = "0"
os.environ["REGA_ALLOW_DATAFORSEO_EXISTING_CREDIT"] = "0"

from career_engine.rega_enrichment.discovery import generate_queries, searxng_qwant_search
from career_engine.rega_enrichment.employment_routes import EmploymentRoute, discover_employment_route
from career_engine.rega_enrichment.models import CandidateResult, CompanyRecord
from career_engine.rega_enrichment.outscraper_validation import validate_emails
from career_engine.rega_enrichment.provider_clients import OutscraperClient, ProviderBudget
from career_engine.rega_enrichment.verify import verify_candidate
from runtime.outscraper_sheet_runner import (
    EXPECTED_HEADERS,
    SPREADSHEET_ID,
    ensure_queue_metadata,
    read_queue,
    rclone_access_token,
    sheets_request,
)

MASTER_SHEET = "Master Tracker"
CONFIG_SHEET = "Config"
AUTO_QUEUE_SHEET = "Auto Send Queue"
SENT_TRACKER_SHEET = "Sent Email Tracker"
DEFAULT_ROOT = Path("runtime/acceptance/rega-priority-scan-v2")
DEFAULT_RESERVE_USD = 1.00
RECORD_USD_UPPER_BOUND = 0.003

DIRECT_LOCALS = {"hr", "career", "careers", "job", "jobs", "recruit", "recruitment", "talent", "hiring"}
GENERAL_LOCALS = {"info", "contact", "contactus", "hello", "admin", "office", "enquiry", "enquiries", "inquiry", "inquiries"}
EXCLUDED_LOCALS = {
    "support", "privacy", "legal", "abuse", "finance", "financial", "billing", "accounts", "accounting",
    "sales", "security", "webmaster", "investor", "investors", "ir",
}

# Company-level queue coverage. HOLD/REJECTED/FAILED rows deliberately do not
# block replacement discovery. Exact mailbox dedupe remains independent.
SEND_QUEUE_COVERED_STATES = {
    "READY_VERIFIED_OUTSCRAPER",
    "SENT",
    "ALREADY_SENT_DEDUPED",
    "OWNER_APPROVED_UNKNOWN_OFFICIAL_SOURCE",
    "SKIPPED_ALREADY_CONTACTED",
}
AUTO_QUEUE_COVERED_STATES = {"PENDING", "SENT", "SKIPPED_ALREADY_CONTACTED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_jsonl(path: Path, records: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for key in sorted(records):
            handle.write(json.dumps(records[key], sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            master_id = str(item.get("master_id") or "").strip()
            if master_id:
                out[master_id] = item
    return out


def read_named_sheet(token: str, sheet_name: str, a1_columns: str) -> list[list[str]]:
    encoded = urllib.parse.quote(f"{sheet_name}!{a1_columns}", safe="!:")
    body = sheets_request(
        token,
        "GET",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{encoded}",
    )
    values = body.get("values") or []
    if not isinstance(values, list):
        raise RuntimeError(f"{sheet_name} returned malformed values")
    return [[str(cell) for cell in row] for row in values if isinstance(row, list)]


def read_table(token: str, sheet_name: str, a1_columns: str) -> tuple[list[str], list[dict[str, str]]]:
    values = read_named_sheet(token, sheet_name, a1_columns)
    if not values:
        return [], []
    headers = [str(x) for x in values[0]]
    rows: list[dict[str, str]] = []
    for row_number, raw in enumerate(values[1:], start=2):
        padded = raw + [""] * (len(headers) - len(raw))
        row = dict(zip(headers, padded[: len(headers)]))
        row["__row_number"] = str(row_number)
        rows.append(row)
    return headers, rows


def read_master(token: str) -> tuple[list[str], list[dict[str, str]]]:
    headers, rows = read_table(token, MASTER_SHEET, "A:AF")
    required = {
        "Master_ID", "Record_Type", "Source_Record_ID", "Company_or_Office", "Arabic_Name", "Region",
        "Address_or_Website", "Email", "Email_Normalized", "Email_Type", "Source_Verification",
        "Source_Date_or_Freshness", "Source_Status", "Send_Eligibility", "Next_Action", "Notes",
    }
    if not required.issubset(set(headers)):
        raise RuntimeError("Master Tracker schema is missing required fields")
    return headers, rows


def read_config(token: str) -> dict[str, str]:
    values = read_named_sheet(token, CONFIG_SHEET, "A:B")
    return {
        str(row[0]).strip(): (str(row[1]).strip() if len(row) > 1 else "")
        for row in values[1:]
        if row
    }


def normalized_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw or raw.startswith("#"):
        return ""
    if "://" in raw:
        raw = (urlsplit(raw).hostname or "").lower()
    else:
        raw = raw.split("/", 1)[0]
    return raw.removeprefix("www.").strip("./ ") if "." in raw else ""


def normalized_company(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def mailbox_rank(email: str) -> tuple[int, str]:
    local = email.split("@", 1)[0].lower() if "@" in email else ""
    if not local or local in EXCLUDED_LOCALS:
        return 9, "excluded"
    if local in DIRECT_LOCALS:
        return 0, "recruitment"
    if local in GENERAL_LOCALS:
        return 2, "general"
    return 1, "person"


def row_rank(row: dict[str, str]) -> tuple[int, str, str]:
    status = str(row.get("Source_Status") or "").lower()
    verification = str(row.get("Source_Verification") or "").lower()
    if "hr email unresolved" in status:
        rank = 0
    elif "immediate target" in status:
        rank = 1
    elif "needs career-route research" in status:
        rank = 2
    elif "official domain" in status or "official domain" in verification:
        rank = 3
    elif "verified" in status or "verified" in verification:
        rank = 4
    else:
        rank = 5
    return rank, normalized_company(row.get("Company_or_Office", "")), str(row.get("Master_ID") or "")


def has_usable_nonemail_route(row: dict[str, str]) -> bool:
    eligibility = str(row.get("Send_Eligibility") or "").strip().upper()
    next_action = str(row.get("Next_Action") or "").lower()
    return eligibility == "NO_EMAIL_DRAFT_ATS_OR_FORM_ONLY" or "ats/form route" in next_action or "apply via verified" in next_action


def company_record(row: dict[str, str]) -> CompanyRecord:
    return CompanyRecord(
        company_id=str(row.get("Master_ID") or ""),
        license_no=str(row.get("Source_Record_ID") or row.get("Master_ID") or ""),
        english_name=str(row.get("Company_or_Office") or ""),
        arabic_name=str(row.get("Arabic_Name") or ""),
        location=str(row.get("Region") or ""),
        career_priority="",
        research_status=str(row.get("Source_Status") or ""),
    )


def free_discover_domain(row: dict[str, str]) -> tuple[str, dict]:
    existing = normalized_domain(str(row.get("Address_or_Website") or ""))
    verification = (str(row.get("Source_Verification") or "") + " " + str(row.get("Source_Status") or "")).lower()
    if existing and any(token in verification for token in ("verified", "official", "partial", "needs career-route research")):
        return existing, {"basis": "current_master_tracker_verified_site", "domain": existing}

    company = company_record(row)
    candidates: list[CandidateResult] = []
    seen: set[str] = set()
    for query in generate_queries(company):
        for position, item in enumerate(searxng_qwant_search(query.query_text, limit=5), start=1):
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            candidates.append(CandidateResult(
                company_id=company.company_id,
                license_no=company.license_no,
                query_id=query.query_id,
                url=url,
                title=str(item.get("title") or "")[:500],
                description=str(item.get("description") or "")[:2000],
                engine="searxng-qwant",
                position=position,
                retrieved_at=utc_now(),
            ))
        if len(candidates) >= 10:
            break

    best = None
    for candidate in candidates[:10]:
        verified = verify_candidate(candidate, company)
        if verified.verification_status == "confirmed" and verified.verification_score >= 10:
            if best is None or verified.verification_score > best.verification_score:
                best = verified
    if best is None:
        return "", {"basis": "free_discovery_no_confirmed_domain", "candidate_count": len(candidates)}
    domain = normalized_domain(best.url)
    return domain, {
        "basis": "searxng_qwant_plus_direct_identity_verification",
        "domain": domain,
        "source_url": best.url,
        "verification_score": best.verification_score,
        "verification_method": best.verification_method,
    }


def _column_letter(index_one_based: int) -> str:
    out = ""
    n = index_one_based
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def update_master_fields(token: str, headers: list[str], row: dict[str, str], updates: dict[str, str]) -> None:
    row_number = int(row["__row_number"])
    data = []
    for field, value in updates.items():
        if field not in headers:
            raise RuntimeError(f"Master Tracker field missing during write: {field}")
        col = _column_letter(headers.index(field) + 1)
        data.append({"range": f"'{MASTER_SHEET}'!{col}{row_number}", "values": [[str(value)]]})
    if not data:
        return
    sheets_request(
        token,
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchUpdate",
        {"valueInputOption": "USER_ENTERED", "data": data},
    )
    for field, value in updates.items():
        row[field] = str(value)


def append_note(existing: str, new: str) -> str:
    existing = str(existing or "").strip()
    if not existing:
        return new
    return existing if new in existing else existing + " | " + new


def persist_portal_route(token: str, headers: list[str], row: dict[str, str], domain: str, route: EmploymentRoute) -> None:
    update_master_fields(token, headers, row, {
        "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
        "Source_Verification": "Verified - official domain + first-party employment route",
        "Source_Date_or_Freshness": today(),
        "Source_Status": "Verified careers/ATS application route",
        "Send_Eligibility": "NO_EMAIL_DRAFT_ATS_OR_FORM_ONLY",
        "Next_Action": f"Apply via verified ATS/form route: {route.value}",
        "Notes": append_note(row.get("Notes", ""), f"REGA portal-first enrichment {today()}: {route.kind} {route.value}; evidence={route.evidence}"),
    })


def persist_no_domain(token: str, headers: list[str], row: dict[str, str], discovery: dict) -> None:
    update_master_fields(token, headers, row, {
        "Source_Date_or_Freshness": today(),
        "Source_Status": "No verified official domain",
        "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
        "Next_Action": "Manual identity/domain review",
        "Notes": append_note(row.get("Notes", ""), f"REGA enrichment {today()}: no confirmed official domain; free candidates={discovery.get('candidate_count', 0)}"),
    })


def persist_no_contact(token: str, headers: list[str], row: dict[str, str], domain: str, detail: str) -> None:
    update_master_fields(token, headers, row, {
        "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
        "Source_Date_or_Freshness": today(),
        "Source_Status": "Official domain verified; no usable recruiting route found",
        "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
        "Next_Action": "Manual review only if strategically important",
        "Notes": append_note(row.get("Notes", ""), f"REGA enrichment {today()}: {detail}"),
    })


def persist_non_sendable_email(token: str, headers: list[str], row: dict[str, str], domain: str, email: str, validation: dict, source_url: str) -> None:
    update_master_fields(token, headers, row, {
        "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
        "Source_Date_or_Freshness": today(),
        "Source_Status": f"Official mailbox found; validator {validation['status']}; do not send",
        "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
        "Next_Action": "Research another official employment route",
        "Notes": append_note(row.get("Notes", ""), f"REGA enrichment {today()}: {email} via {source_url}; validator={validation['status']}; not queued"),
    })


def persist_receiving_email(token: str, headers: list[str], row: dict[str, str], domain: str, email: str, email_kind: str, source_url: str) -> None:
    eligibility = "PREPARED_REGA_OFFICIAL_HR_EMAIL" if email_kind == "recruitment" else "PREPARED_REGA_SOURCE_EMAIL"
    update_master_fields(token, headers, row, {
        "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
        "Email": email,
        "Email_Normalized": email,
        "Email_Type": "HR/Recruitment" if email_kind == "recruitment" else "General",
        "Source_Verification": "Verified - official domain + mailbox validator RECEIVING",
        "Source_Date_or_Freshness": today(),
        "Source_Status": "Verified receiving email route",
        "Send_Eligibility": eligibility,
        "Next_Action": "Queue only; sender performs final Gmail/company dedupe before any send",
        "Notes": append_note(row.get("Notes", ""), f"REGA enrichment {today()}: validator RECEIVING for {email}; source={source_url}; queued, no send performed"),
    })


def balance(client: OutscraperClient) -> float:
    result = client.balance()
    meta = dict(result.get("metadata") or {})
    value = meta.get("balance")
    if str(result.get("status") or "") != "success" or meta.get("account_status") != "valid" or not isinstance(value, (int, float)):
        raise RuntimeError("Outscraper balance/account preflight failed")
    return float(value)


def contact_candidates(client: OutscraperClient, domain: str) -> list[dict]:
    raw = client.domain_contacts(domain, ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=0, max_domains=1))
    out: list[dict] = []
    for item in raw:
        meta = dict(item.get("metadata") or {})
        email = str(meta.get("email") or "").strip().lower()
        if not email or "@" not in email:
            continue
        rank, kind = mailbox_rank(email)
        if rank >= 9:
            continue
        email_domain = email.rsplit("@", 1)[1].removeprefix("www.")
        if not (email_domain == domain or email_domain.endswith("." + domain)):
            continue
        out.append({
            "email": email,
            "rank": rank,
            "kind": kind,
            "source_urls": [str(x) for x in meta.get("source_urls") or [] if str(x).startswith(("http://", "https://"))][:10],
            "route_source": "paid_domain_contacts",
        })
    return sorted(out, key=lambda x: (x["rank"], x["email"]))


def validate_one(client: OutscraperClient, email: str) -> dict:
    records = validate_emails(
        client,
        [email],
        ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1, max_domains=0),
        batch_size=1,
    )
    if len(records) != 1:
        raise RuntimeError("Outscraper validator did not return exactly one record")
    record = records[0]
    meta = dict(record.get("metadata") or {})
    return {
        "email": str(meta.get("email") or email).strip().lower(),
        "status": str(record.get("status") or "UNKNOWN").upper(),
        "verification": str(meta.get("verification") or record.get("status") or "UNKNOWN").upper(),
        "status_details": str(meta.get("status_details") or ""),
        "safe_to_send": bool(meta.get("safe_to_send", False)),
        "checked_at": str(record.get("retrieved_at") or utc_now()),
        "source_url": str(record.get("source_url") or "https://api.outscraper.com/email-validator"),
    }


def next_queue_id(existing_rows: list[dict[str, str]]) -> str:
    highest = 0
    for row in existing_rows:
        match = re.fullmatch(r"SEND-(\d+)", str(row.get("Queue_ID") or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"SEND-{highest + 1:04d}"


def append_queue_row(token: str, values: list[str]) -> None:
    encoded = urllib.parse.quote("Send Queue!A:W", safe="!:")
    sheets_request(
        token,
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{encoded}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
        {"majorDimension": "ROWS", "values": [values]},
    )


def queue_row_values(*, queue_id: str, row: dict[str, str], domain: str, candidate: dict, validation: dict, config: dict[str, str]) -> list[str]:
    evidence = json.dumps({
        "provider": "outscraper",
        "workflow": "rega_priority_scan_v2",
        "official_domain": domain,
        "contact_source_urls": candidate.get("source_urls") or [],
        "safe_to_send": validation["safe_to_send"],
        "source_url": validation["source_url"],
        "status_details": validation["status_details"],
        "route_source": candidate.get("route_source", "paid_domain_contacts"),
    }, separators=(",", ":"), ensure_ascii=False)
    direct = candidate.get("kind") == "recruitment"
    values = {
        "Queue_ID": queue_id,
        "Email": validation["email"],
        "Company_or_Office": str(row.get("Company_or_Office") or ""),
        "Source_Dataset": "REGA priority scan v2 / Master Tracker",
        "Source_Record_ID": str(row.get("Master_ID") or row.get("Source_Record_ID") or ""),
        "Source_Verification": "Verified - official domain + mailbox validator RECEIVING",
        "Source_Date_or_Freshness": today(),
        "Send_Eligibility": "PREPARED_REGA_OFFICIAL_HR_EMAIL" if direct else "PREPARED_REGA_SOURCE_EMAIL",
        "Gmail_Draft_ID": "",
        "Gmail_Message_ID": "",
        "Sender_Email": config.get("sender_email", "hameedfarah@gmail.com"),
        "Draft_Subject": config.get("subject", ""),
        "Attachment_1": config.get("attachment_1", ""),
        "Attachment_2": config.get("attachment_2", ""),
        "Send_State": "READY_VERIFIED_OUTSCRAPER",
        "Sent_Message_ID": "",
        "Terminal_Outcome": "",
        "Notes": "REGA-only enrichment; route verified and mailbox validator RECEIVING; no draft/send performed.",
        "Outscraper_Status": validation["status"],
        "Outscraper_Verification": validation["verification"],
        "Outscraper_Replacement_Email": "",
        "Outscraper_Evidence": evidence,
        "Outscraper_Checked_At": validation["checked_at"],
    }
    return [str(values.get(header, "")) for header in EXPECTED_HEADERS]


def _emails_from_rows(rows: list[dict[str, str]], possible_fields: tuple[str, ...]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        for field in possible_fields:
            email = str(row.get(field) or "").strip().lower()
            if email and "@" in email:
                out.add(email)
    return out


def build_dedupe_state(master: list[dict[str, str]], send_queue: list[dict[str, str]], auto_queue: list[dict[str, str]], sent_rows: list[dict[str, str]]) -> dict[str, set[str]]:
    # Exact mailbox dedupe is intentionally broader than company-level coverage:
    # a held/failed historical row cannot be appended again under the unique
    # Send Queue constraint, but its company may still receive a replacement.
    known_emails = _emails_from_rows(send_queue, ("Email",))
    known_emails |= _emails_from_rows(auto_queue, ("Email",))
    known_emails |= _emails_from_rows(sent_rows, ("Recipient_Email",))
    known_emails |= _emails_from_rows(master, ("Email", "Email_Normalized"))
    permanent_bounces = {
        str(row.get("Recipient_Email") or "").strip().lower()
        for row in sent_rows
        if str(row.get("Bounce_State") or "").strip().upper() == "PERMANENT"
    }
    contacted_companies = {
        normalized_company(row.get("Company_or_Office", ""))
        for row in sent_rows
        if str(row.get("Delivery_State") or "").strip().upper() == "SENT"
        and not str(row.get("Bounce_State") or "").strip()
    }
    queued_companies = {
        normalized_company(row.get("Company_or_Office", ""))
        for row in send_queue
        if str(row.get("Send_State") or "").strip().upper() in SEND_QUEUE_COVERED_STATES
    } | {
        normalized_company(row.get("Company_or_Office", ""))
        for row in auto_queue
        if str(row.get("Status") or "").strip().upper() in AUTO_QUEUE_COVERED_STATES
    }
    return {
        "known_emails": {x for x in known_emails if x},
        "permanent_bounces": {x for x in permanent_bounces if x},
        "contacted_companies": {x for x in contacted_companies if x},
        "queued_companies": {x for x in queued_companies if x},
    }


def read_dedupe_state(token: str, master: list[dict[str, str]], send_queue: list[dict[str, str]]) -> dict[str, set[str]]:
    _, auto_queue = read_table(token, AUTO_QUEUE_SHEET, "A:L")
    _, sent_rows = read_table(token, SENT_TRACKER_SHEET, "A:P")
    return build_dedupe_state(master, send_queue, auto_queue, sent_rows)


def _blocked_candidate(email: str, known_emails: set[str], permanent_bounces: set[str]) -> str:
    email = str(email or "").strip().lower()
    if email in permanent_bounces:
        return "permanent_bounce_exact_mailbox_blocked"
    if email in known_emails:
        return "email_already_known_or_queued"
    return ""


def select_best_eligible_candidate(candidates: list[dict], known_emails: set[str], permanent_bounces: set[str]) -> tuple[dict | None, dict[str, int]]:
    rejected = {"permanent_bounce": 0, "known_email": 0}
    for candidate in sorted(candidates, key=lambda x: (int(x.get("rank", 9)), str(x.get("email") or ""))):
        email = str(candidate.get("email") or "").strip().lower()
        reason = _blocked_candidate(email, known_emails, permanent_bounces)
        if reason == "permanent_bounce_exact_mailbox_blocked":
            rejected["permanent_bounce"] += 1
            continue
        if reason:
            rejected["known_email"] += 1
            continue
        return candidate, rejected
    return None, rejected


def _checkpoint(checkpoints: dict[str, dict], path: Path, master_id: str, payload: dict) -> None:
    checkpoints[master_id] = payload
    atomic_jsonl(path, checkpoints)


def _source_url(candidate: dict, domain: str) -> str:
    urls = candidate.get("source_urls") or []
    return str(urls[0]) if urls else f"https://{domain}/"


def _persist_paid_terminal(token: str, headers: list[str], row: dict[str, str], prior: dict) -> None:
    domain = str(prior.get("domain") or "")
    result = str(prior.get("result") or "")
    if result == "no_eligible_contact":
        persist_no_contact(token, headers, row, domain, str(prior.get("detail") or "no eligible new company-domain mailbox"))
        return
    validation = dict(prior.get("validation") or {})
    candidate = dict(prior.get("candidate") or {})
    if result == "nonreceiving_contact" and validation and candidate:
        persist_non_sendable_email(token, headers, row, domain, str(candidate.get("email") or ""), validation, _source_url(candidate, domain))
        return
    raise RuntimeError(f"invalid ready_terminal_write checkpoint for {row.get('Master_ID')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Required for provider calls and Sheet writes")
    parser.add_argument("--reserve-usd", type=float, default=DEFAULT_RESERVE_USD)
    parser.add_argument("--max-companies", type=int, default=0, help="0 means all current eligible REGA rows")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing provider/Sheet execution without --apply")

    key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
    if not key:
        raise SystemExit("missing Outscraper runtime key")

    root = Path(args.root)
    checkpoint_path = root / "checkpoint.jsonl"
    summary_path = root / "summary.json"
    checkpoints = load_jsonl(checkpoint_path)
    token = rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
    master_headers, master = read_master(token)
    config = read_config(token)
    current_queue = read_queue(token, SPREADSHEET_ID)
    dedupe = read_dedupe_state(token, master, current_queue)
    known_emails = dedupe["known_emails"]
    permanent_bounces = dedupe["permanent_bounces"]
    contacted_companies = dedupe["contacted_companies"]
    queued_companies = dedupe["queued_companies"]

    rega = [r for r in master if str(r.get("Record_Type") or "").strip() == "REGA_COMPANY_NO_EMAIL"]
    if not rega:
        raise SystemExit("no REGA_COMPANY_NO_EMAIL rows found in current Master Tracker")
    rega.sort(key=row_rank)

    client = OutscraperClient(key)
    start_balance = balance(client)
    reserve = max(0.0, float(args.reserve_usd))
    limit = max(0, int(args.max_companies))
    newly_queued_emails: set[str] = set()
    processed_this_run = 0
    counts = {
        "rega_no_email_current": len(rega),
        "skipped_existing_ats_form_route": 0,
        "skipped_prior_successful_company_contact": 0,
        "skipped_company_already_covered": 0,
        "free_domain_discovered": 0,
        "free_domain_not_found": 0,
        "free_portal_routes_persisted": 0,
        "free_email_candidates": 0,
        "free_email_validations": 0,
        "paid_domain_lookups": 0,
        "paid_validations": 0,
        "receiving_routes_staged": 0,
        "known_email_candidates_skipped": 0,
        "permanent_bounce_candidates_skipped": 0,
        "provider_no_contact": 0,
        "provider_not_receiving": 0,
        "ambiguous_inflight_skipped": 0,
        "resumed_ready_writes": 0,
        "balance_guard_stops": 0,
    }

    for row in rega:
        if limit and processed_this_run >= limit:
            break
        master_id = str(row.get("Master_ID") or "").strip()
        if not master_id:
            continue
        company_key = normalized_company(row.get("Company_or_Office", ""))
        prior = checkpoints.get(master_id) or {}
        prior_state = str(prior.get("state") or "")

        if prior_state == "inflight":
            counts["ambiguous_inflight_skipped"] += 1
            continue
        if prior_state in {"complete", "staged", "no_route"}:
            continue
        if prior_state == "ready_terminal_write":
            _persist_paid_terminal(token, master_headers, row, prior)
            checkpoints[master_id].update(state="complete", at=utc_now())
            atomic_jsonl(checkpoint_path, checkpoints)
            counts["resumed_ready_writes"] += 1
            continue
        if prior_state == "ready_to_write":
            validation = dict(prior.get("validation") or {})
            candidate = dict(prior.get("candidate") or {})
            domain = str(prior.get("domain") or "").strip()
            email = str(validation.get("email") or candidate.get("email") or "").strip().lower()
            if not domain or not email or str(validation.get("status") or "").upper() != "RECEIVING" or not validation.get("safe_to_send"):
                raise RuntimeError(f"invalid ready_to_write checkpoint for {master_id}")
            if email in permanent_bounces:
                raise RuntimeError(f"ready_to_write mailbox became a permanent bounce: {master_id}")
            queue_id = str(prior.get("queue_id") or "").strip() or next_queue_id(current_queue)
            if email not in known_emails:
                append_queue_row(token, queue_row_values(queue_id=queue_id, row=row, domain=domain, candidate=candidate, validation=validation, config=config))
                known_emails.add(email)
                newly_queued_emails.add(email)
            ensure_queue_metadata(token, SPREADSHEET_ID)
            current_queue = read_queue(token, SPREADSHEET_ID)
            persist_receiving_email(token, master_headers, row, domain, email, str(candidate.get("kind") or mailbox_rank(email)[1]), _source_url(candidate, domain))
            checkpoints[master_id].update(state="staged", result="receiving_route_ready", queue_id=queue_id, at=utc_now())
            atomic_jsonl(checkpoint_path, checkpoints)
            counts["resumed_ready_writes"] += 1
            continue

        if has_usable_nonemail_route(row):
            _checkpoint(checkpoints, checkpoint_path, master_id, {"master_id": master_id, "company": row.get("Company_or_Office", ""), "state": "complete", "result": "existing_ats_or_form_route_no_paid_lookup", "at": utc_now()})
            counts["skipped_existing_ats_form_route"] += 1
            continue
        if company_key and company_key in contacted_companies:
            _checkpoint(checkpoints, checkpoint_path, master_id, {"master_id": master_id, "company": row.get("Company_or_Office", ""), "state": "complete", "result": "company_already_successfully_contacted", "at": utc_now()})
            counts["skipped_prior_successful_company_contact"] += 1
            continue
        if company_key and company_key in queued_companies:
            _checkpoint(checkpoints, checkpoint_path, master_id, {"master_id": master_id, "company": row.get("Company_or_Office", ""), "state": "complete", "result": "company_already_covered_by_active_or_sent_queue", "at": utc_now()})
            counts["skipped_company_already_covered"] += 1
            continue

        domain, discovery = free_discover_domain(row)
        processed_this_run += 1
        if not domain:
            persist_no_domain(token, master_headers, row, discovery)
            _checkpoint(checkpoints, checkpoint_path, master_id, {"master_id": master_id, "company": row.get("Company_or_Office", ""), "state": "no_route", "result": "free_discovery_no_confirmed_domain", "discovery": discovery, "at": utc_now()})
            counts["free_domain_not_found"] += 1
            continue
        counts["free_domain_discovered"] += 1

        official_url = str(row.get("Address_or_Website") or "").strip()
        if normalized_domain(official_url) != domain:
            official_url = f"https://{domain}/"
        try:
            free_route = discover_employment_route(official_url)
        except Exception as exc:
            free_route = None
            discovery["employment_route_error"] = type(exc).__name__

        if free_route and free_route.is_portal:
            persist_portal_route(token, master_headers, row, domain, free_route)
            _checkpoint(checkpoints, checkpoint_path, master_id, {
                "master_id": master_id,
                "company": row.get("Company_or_Office", ""),
                "state": "complete",
                "result": "free_first_party_portal_route",
                "domain": domain,
                "route": {"kind": free_route.kind, "value": free_route.value, "source_url": free_route.source_url},
                "at": utc_now(),
            })
            counts["free_portal_routes_persisted"] += 1
            continue

        candidate: dict | None = None
        free_general_fallback: dict | None = None

        if free_route and free_route.is_email:
            counts["free_email_candidates"] += 1
            free_candidate = {
                "email": free_route.value.strip().lower(),
                "rank": mailbox_rank(free_route.value)[0],
                "kind": free_route.email_kind or mailbox_rank(free_route.value)[1],
                "source_urls": [free_route.source_url],
                "route_source": "free_first_party_site",
            }
            reason = _blocked_candidate(free_candidate["email"], known_emails, permanent_bounces)
            if reason == "permanent_bounce_exact_mailbox_blocked":
                counts["permanent_bounce_candidates_skipped"] += 1
            elif reason:
                counts["known_email_candidates_skipped"] += 1
            elif free_candidate["kind"] == "recruitment":
                candidate = free_candidate
            else:
                free_general_fallback = free_candidate

        # A free, unblocked recruitment mailbox is stronger than paid contact
        # hunting. It still requires real mailbox validation before queueing.
        if candidate is not None:
            if balance(client) - reserve < RECORD_USD_UPPER_BOUND:
                counts["balance_guard_stops"] += 1
                break
            _checkpoint(checkpoints, checkpoint_path, master_id, {
                "master_id": master_id,
                "company": row.get("Company_or_Office", ""),
                "state": "inflight",
                "phase": "outscraper_email_validation",
                "domain": domain,
                "candidate": candidate,
                "at": utc_now(),
            })
            validation = validate_one(client, candidate["email"])
            counts["free_email_validations"] += 1
        else:
            # No usable free recruitment mailbox. One domain-contact lookup may
            # find a stronger or replacement route; free general is fallback.
            if balance(client) - reserve < (2 * RECORD_USD_UPPER_BOUND):
                counts["balance_guard_stops"] += 1
                break
            _checkpoint(checkpoints, checkpoint_path, master_id, {
                "master_id": master_id,
                "company": row.get("Company_or_Office", ""),
                "state": "inflight",
                "phase": "outscraper_domain_contacts",
                "domain": domain,
                "discovery": discovery,
                "at": utc_now(),
            })
            provider_candidates = contact_candidates(client, domain)
            counts["paid_domain_lookups"] += 1
            candidate, rejected = select_best_eligible_candidate(provider_candidates, known_emails, permanent_bounces)
            counts["permanent_bounce_candidates_skipped"] += rejected["permanent_bounce"]
            counts["known_email_candidates_skipped"] += rejected["known_email"]
            if candidate is None and free_general_fallback is not None:
                candidate = free_general_fallback
            if candidate is None:
                checkpoints[master_id].update(
                    state="ready_terminal_write",
                    result="no_eligible_contact",
                    detail="no free employment portal and no eligible new company-domain mailbox after one paid domain lookup",
                    at=utc_now(),
                )
                atomic_jsonl(checkpoint_path, checkpoints)
                _persist_paid_terminal(token, master_headers, row, checkpoints[master_id])
                checkpoints[master_id].update(state="no_route", at=utc_now())
                atomic_jsonl(checkpoint_path, checkpoints)
                counts["provider_no_contact"] += 1
                continue
            if balance(client) - reserve < RECORD_USD_UPPER_BOUND:
                checkpoints[master_id].update(state="complete", result="reserve_reached_before_validation", at=utc_now())
                atomic_jsonl(checkpoint_path, checkpoints)
                counts["balance_guard_stops"] += 1
                break
            checkpoints[master_id].update(phase="outscraper_email_validation", candidate=candidate, selected_email=candidate["email"], at=utc_now())
            atomic_jsonl(checkpoint_path, checkpoints)
            validation = validate_one(client, candidate["email"])
            counts["paid_validations"] += 1

        if validation["status"] != "RECEIVING" or not validation["safe_to_send"]:
            checkpoints[master_id].update(
                state="ready_terminal_write",
                result="nonreceiving_contact",
                candidate=candidate,
                validation=validation,
                at=utc_now(),
            )
            atomic_jsonl(checkpoint_path, checkpoints)
            _persist_paid_terminal(token, master_headers, row, checkpoints[master_id])
            checkpoints[master_id].update(state="complete", result="best_contact_not_receiving", at=utc_now())
            atomic_jsonl(checkpoint_path, checkpoints)
            counts["provider_not_receiving"] += 1
            continue

        queue_id = next_queue_id(current_queue)
        checkpoints[master_id].update(
            state="ready_to_write",
            result="validated_receiving_pending_sheet_write",
            queue_id=queue_id,
            domain=domain,
            candidate=candidate,
            validation=validation,
            at=utc_now(),
        )
        atomic_jsonl(checkpoint_path, checkpoints)

        append_queue_row(token, queue_row_values(queue_id=queue_id, row=row, domain=domain, candidate=candidate, validation=validation, config=config))
        ensure_queue_metadata(token, SPREADSHEET_ID)
        current_queue = read_queue(token, SPREADSHEET_ID)
        known_emails.add(validation["email"])
        newly_queued_emails.add(validation["email"])
        if company_key:
            queued_companies.add(company_key)
        persist_receiving_email(token, master_headers, row, domain, validation["email"], str(candidate.get("kind") or mailbox_rank(validation["email"])[1]), _source_url(candidate, domain))
        checkpoints[master_id].update(state="staged", result="receiving_route_ready", queue_id=queue_id, at=utc_now())
        atomic_jsonl(checkpoint_path, checkpoints)
        counts["receiving_routes_staged"] += 1

    end_balance = balance(client)
    readback = read_queue(token, SPREADSHEET_ID)
    readback_emails = [str(r.get("Email") or "").strip().lower() for r in readback]
    if len(set(readback_emails)) != len(readback_emails):
        raise SystemExit("post-run Send Queue email uniqueness invariant failed")
    if permanent_bounces.intersection(newly_queued_emails):
        raise SystemExit("post-run permanent-bounce reintroduction invariant failed")

    summary = {
        "ok": True,
        "scope": "REGA_ONLY",
        "master_tracker_rega_no_email_rows": len(rega),
        "processed_this_run": processed_this_run,
        **counts,
        "balance_before": start_balance,
        "balance_after": end_balance,
        "balance_delta": round(end_balance - start_balance, 6),
        "reserve_usd": reserve,
        "send_queue_rows_after": len(readback),
        "sends": 0,
        "gmail_drafts": 0,
        "balady_outscraper_calls": 0,
        "engineering_outscraper_calls": 0,
        "purchases_or_topups": 0,
        "finished_at": utc_now(),
    }
    atomic_json(summary_path, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0
