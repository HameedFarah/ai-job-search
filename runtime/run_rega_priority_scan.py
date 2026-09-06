#!/usr/bin/env python3
"""Current-state REGA-first scanner with tightly conserved Outscraper spend.

The authoritative universe is read dynamically from the current Career Engine
Master Tracker; no 757/728/1236 snapshot constants are used.

Spend policy:
1. REGA only. Balady/engineering/non-REGA records are structurally excluded.
2. Existing usable careers/ATS/form routes are not paid-scanned for an email.
3. Missing domains are discovered first with local SearXNG/Qwant and verified
   with direct HTTP fetches; this phase does not use Outscraper.
4. For each verified official domain that still lacks a usable route, make at
   most one Outscraper Emails & Contacts lookup.
5. Select one best mailbox only (HR/careers/recruitment first; generic last),
   then make at most one Outscraper email-validation record for that company.
6. Stop paid work before the provider balance drops below the configured reserve
   (default $1.00). No provider top-up or purchase path exists.
7. Only RECEIVING routes are appended to Send Queue. No Gmail send path exists.

Restart safety is provided by a per-Master_ID JSONL checkpoint. Paid calls are
checkpointed as inflight before execution; ambiguous calls are never repeated
without operator intervention.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Force the discovery/verification phase to remain free of Firecrawl fallback.
os.environ["FIRECRAWL_ROTATED_CONFIRMED"] = "0"
os.environ["REGA_ALLOW_DATAFORSEO_EXISTING_CREDIT"] = "0"

from career_engine.rega_enrichment.discovery import generate_queries, searxng_qwant_search
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
DEFAULT_ROOT = Path("runtime/acceptance/rega-priority-scan")
DEFAULT_RESERVE_USD = 1.00
RECORD_USD_UPPER_BOUND = 0.003

DIRECT_LOCALS = {
    "hr", "career", "careers", "job", "jobs", "recruit", "recruitment", "talent", "hiring"
}
GENERAL_LOCALS = {
    "info", "contact", "contactus", "hello", "admin", "office", "enquiry", "enquiries", "inquiry", "inquiries"
}
EXCLUDED_LOCALS = {
    "support", "privacy", "legal", "abuse", "finance", "financial", "billing", "accounts", "accounting",
    "sales", "security", "webmaster", "investor", "investors", "ir"
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
            key = str(item.get("master_id") or "").strip()
            if key:
                out[key] = item
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


def read_master(token: str) -> list[dict[str, str]]:
    values = read_named_sheet(token, MASTER_SHEET, "A:AF")
    if not values:
        raise RuntimeError("Master Tracker is empty")
    headers = [str(x) for x in values[0]]
    required = {
        "Master_ID", "Record_Type", "Source_Record_ID", "Company_or_Office", "Arabic_Name", "Region",
        "Address_or_Website", "Email", "Source_Verification", "Source_Status", "Send_Eligibility", "Next_Action",
    }
    if not required.issubset(set(headers)):
        raise RuntimeError("Master Tracker schema is missing required fields")
    rows: list[dict[str, str]] = []
    for raw in values[1:]:
        padded = raw + [""] * (len(headers) - len(raw))
        rows.append(dict(zip(headers, padded[: len(headers)])))
    return rows


def read_config(token: str) -> dict[str, str]:
    values = read_named_sheet(token, CONFIG_SHEET, "A:B")
    out: dict[str, str] = {}
    for row in values[1:]:
        if row:
            out[str(row[0]).strip()] = str(row[1]).strip() if len(row) > 1 else ""
    return out


def normalized_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw or raw.startswith("#"):
        return ""
    if "://" in raw:
        raw = (urlsplit(raw).hostname or "").lower()
    else:
        raw = raw.split("/", 1)[0]
    return raw.removeprefix("www.").strip("./ ") if "." in raw else ""


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
    return rank, str(row.get("Company_or_Office") or "").lower(), str(row.get("Master_ID") or "")


def has_usable_nonemail_route(row: dict[str, str]) -> bool:
    eligibility = str(row.get("Send_Eligibility") or "").strip().upper()
    next_action = str(row.get("Next_Action") or "").lower()
    # A verified ATS/form route already reaches the employer and does not merit
    # paid email hunting while REGA coverage is incomplete elsewhere.
    return eligibility == "NO_EMAIL_DRAFT_ATS_OR_FORM_ONLY" or "ats/form route only" in next_action


def free_discover_domain(row: dict[str, str]) -> tuple[str, dict]:
    existing = normalized_domain(str(row.get("Address_or_Website") or ""))
    verification = (str(row.get("Source_Verification") or "") + " " + str(row.get("Source_Status") or "")).lower()
    if existing and any(token in verification for token in ("verified", "official", "partial", "needs career-route research")):
        return existing, {"basis": "current_master_tracker_verified_site", "domain": existing}

    company = CompanyRecord(
        company_id=str(row.get("Master_ID") or ""),
        license_no=str(row.get("Source_Record_ID") or row.get("Master_ID") or ""),
        english_name=str(row.get("Company_or_Office") or ""),
        arabic_name=str(row.get("Arabic_Name") or ""),
        location=str(row.get("Region") or ""),
        career_priority="",
        research_status=str(row.get("Source_Status") or ""),
    )
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


def balance(client: OutscraperClient) -> float:
    result = client.balance()
    meta = dict(result.get("metadata") or {})
    value = meta.get("balance")
    if str(result.get("status") or "") != "success" or meta.get("account_status") != "valid" or not isinstance(value, (int, float)):
        raise RuntimeError("Outscraper balance/account preflight failed")
    return float(value)


def contact_candidates(client: OutscraperClient, domain: str) -> list[dict]:
    budget = ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=0, max_domains=1)
    raw = client.domain_contacts(domain, budget)
    out: list[dict] = []
    for item in raw:
        meta = dict(item.get("metadata") or {})
        email = str(meta.get("email") or "").strip().lower()
        if not email or "@" not in email:
            continue
        rank, kind = mailbox_rank(email)
        if rank >= 9:
            continue
        out.append({
            "email": email,
            "rank": rank,
            "kind": kind,
            "source_urls": [str(x) for x in meta.get("source_urls") or [] if str(x).startswith(("http://", "https://"))][:10],
            "provider_status": str(item.get("status") or ""),
        })
    return sorted(out, key=lambda x: (x["rank"], x["email"]))


def validate_one(client: OutscraperClient, email: str) -> dict:
    budget = ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1, max_domains=0)
    records = validate_emails(client, [email], budget, batch_size=1)
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


def next_queue_id(existing_rows: list[dict[str, str]], offset: int) -> str:
    highest = 0
    for row in existing_rows:
        match = re.fullmatch(r"SEND-(\d+)", str(row.get("Queue_ID") or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"SEND-{highest + offset:04d}"


def append_queue_rows(token: str, rows: list[list[str]]) -> None:
    if not rows:
        return
    encoded = urllib.parse.quote("Send Queue!A:W", safe="!:")
    sheets_request(
        token,
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{encoded}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
        {"majorDimension": "ROWS", "values": rows},
    )


def queue_row_values(
    *, queue_id: str, row: dict[str, str], domain: str, candidate: dict, validation: dict, config: dict[str, str]
) -> list[str]:
    evidence = json.dumps({
        "provider": "outscraper",
        "workflow": "rega_priority_scan",
        "official_domain": domain,
        "contact_source_urls": candidate.get("source_urls") or [],
        "safe_to_send": validation["safe_to_send"],
        "source_url": validation["source_url"],
        "status_details": validation["status_details"],
    }, separators=(",", ":"), ensure_ascii=False)
    direct = candidate.get("kind") == "recruitment"
    values = {
        "Queue_ID": queue_id,
        "Email": validation["email"],
        "Company_or_Office": str(row.get("Company_or_Office") or ""),
        "Source_Dataset": "REGA priority scan / Master Tracker",
        "Source_Record_ID": str(row.get("Master_ID") or row.get("Source_Record_ID") or ""),
        "Source_Verification": "Verified - official domain + Outscraper public-source route",
        "Source_Date_or_Freshness": datetime.now(timezone.utc).date().isoformat(),
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
        "Notes": "REGA-first recovery: free domain discovery/verification, one Outscraper domain lookup, one best-mailbox validation; no send performed.",
        "Outscraper_Status": validation["status"],
        "Outscraper_Verification": validation["verification"],
        "Outscraper_Replacement_Email": "",
        "Outscraper_Evidence": evidence,
        "Outscraper_Checked_At": validation["checked_at"],
    }
    return [str(values.get(header, "")) for header in EXPECTED_HEADERS]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Required for paid provider calls and Send Queue appends")
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
    master = read_master(token)
    config = read_config(token)
    current_queue = read_queue(token, SPREADSHEET_ID)
    existing_emails = {str(r.get("Email") or "").strip().lower() for r in current_queue}

    rega = [r for r in master if str(r.get("Record_Type") or "").strip() == "REGA_COMPANY_NO_EMAIL"]
    if not rega:
        raise SystemExit("no REGA_COMPANY_NO_EMAIL rows found in current Master Tracker")
    rega.sort(key=row_rank)

    client = OutscraperClient(key)
    start_balance = balance(client)
    reserve = max(0.0, float(args.reserve_usd))
    limit = max(0, int(args.max_companies))

    staged: list[list[str]] = []
    counts = {
        "rega_no_email_current": len(rega),
        "skipped_existing_ats_form_route": 0,
        "free_domain_discovered": 0,
        "free_domain_not_found": 0,
        "paid_domain_lookups": 0,
        "paid_validations": 0,
        "receiving_routes_staged": 0,
        "duplicate_existing_queue_email": 0,
        "provider_no_contact": 0,
        "provider_not_receiving": 0,
        "ambiguous_inflight_skipped": 0,
    }
    processed_this_run = 0

    for row in rega:
        if limit and processed_this_run >= limit:
            break
        master_id = str(row.get("Master_ID") or "").strip()
        if not master_id:
            continue
        prior = checkpoints.get(master_id)
        if prior and str(prior.get("state") or "") == "inflight":
            counts["ambiguous_inflight_skipped"] += 1
            continue
        if prior and str(prior.get("state") or "") in {"complete", "staged", "no_route"}:
            continue
        if has_usable_nonemail_route(row):
            checkpoints[master_id] = {
                "master_id": master_id,
                "company": str(row.get("Company_or_Office") or ""),
                "state": "complete",
                "result": "existing_ats_or_form_route_no_paid_lookup",
                "at": utc_now(),
            }
            counts["skipped_existing_ats_form_route"] += 1
            atomic_jsonl(checkpoint_path, checkpoints)
            continue

        domain, discovery = free_discover_domain(row)
        processed_this_run += 1
        if not domain:
            checkpoints[master_id] = {
                "master_id": master_id,
                "company": str(row.get("Company_or_Office") or ""),
                "state": "no_route",
                "result": "free_discovery_no_confirmed_domain",
                "discovery": discovery,
                "at": utc_now(),
            }
            counts["free_domain_not_found"] += 1
            atomic_jsonl(checkpoint_path, checkpoints)
            continue
        counts["free_domain_discovered"] += 1

        # At most two $0.003-class records remain for this company: one domain
        # contact extraction record and one selected email validation record.
        current_balance = balance(client)
        if current_balance - reserve < (2 * RECORD_USD_UPPER_BOUND):
            break

        checkpoints[master_id] = {
            "master_id": master_id,
            "company": str(row.get("Company_or_Office") or ""),
            "state": "inflight",
            "phase": "outscraper_domain_contacts",
            "domain": domain,
            "discovery": discovery,
            "at": utc_now(),
        }
        atomic_jsonl(checkpoint_path, checkpoints)
        candidates = contact_candidates(client, domain)
        counts["paid_domain_lookups"] += 1
        if not candidates:
            checkpoints[master_id].update(state="no_route", result="outscraper_no_suitable_contact", at=utc_now())
            counts["provider_no_contact"] += 1
            atomic_jsonl(checkpoint_path, checkpoints)
            continue

        candidate = candidates[0]
        if candidate["email"] in existing_emails:
            checkpoints[master_id].update(
                state="complete", result="email_already_in_send_queue", selected_email=candidate["email"], at=utc_now()
            )
            counts["duplicate_existing_queue_email"] += 1
            atomic_jsonl(checkpoint_path, checkpoints)
            continue

        if balance(client) - reserve < RECORD_USD_UPPER_BOUND:
            checkpoints[master_id].update(state="complete", result="reserve_reached_before_validation", at=utc_now())
            atomic_jsonl(checkpoint_path, checkpoints)
            break

        checkpoints[master_id].update(phase="outscraper_email_validation", selected_email=candidate["email"], at=utc_now())
        atomic_jsonl(checkpoint_path, checkpoints)
        validation = validate_one(client, candidate["email"])
        counts["paid_validations"] += 1
        if validation["status"] != "RECEIVING":
            checkpoints[master_id].update(
                state="complete", result="best_contact_not_receiving", validation=validation, at=utc_now()
            )
            counts["provider_not_receiving"] += 1
            atomic_jsonl(checkpoint_path, checkpoints)
            continue

        queue_id = next_queue_id(current_queue, len(staged) + 1)
        staged.append(queue_row_values(
            queue_id=queue_id,
            row=row,
            domain=domain,
            candidate=candidate,
            validation=validation,
            config=config,
        ))
        existing_emails.add(validation["email"])
        checkpoints[master_id].update(
            state="staged", result="receiving_route_ready", queue_id=queue_id, validation=validation, at=utc_now()
        )
        counts["receiving_routes_staged"] += 1
        atomic_jsonl(checkpoint_path, checkpoints)

        if len(staged) >= 25:
            append_queue_rows(token, staged)
            current_queue.extend(dict(zip(EXPECTED_HEADERS, values)) for values in staged)
            staged.clear()
            ensure_queue_metadata(token, SPREADSHEET_ID)

    if staged:
        append_queue_rows(token, staged)
        current_queue.extend(dict(zip(EXPECTED_HEADERS, values)) for values in staged)
        ensure_queue_metadata(token, SPREADSHEET_ID)

    end_balance = balance(client)
    readback = read_queue(token, SPREADSHEET_ID)
    if len({str(r.get("Email") or "").strip().lower() for r in readback}) != len(readback):
        raise SystemExit("post-run Send Queue email uniqueness invariant failed")

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
        "balady_outscraper_calls": 0,
        "engineering_outscraper_calls": 0,
        "purchases_or_topups": 0,
        "finished_at": utc_now(),
    }
    atomic_json(summary_path, summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
