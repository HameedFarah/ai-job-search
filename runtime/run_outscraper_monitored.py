#!/usr/bin/env python3
"""Restart-safe monitored Outscraper preparation for Career Engine outreach.

Phases:
1. Finish the authoritative Send Queue in bounded 5-email validation batches,
   committing/verifying progress every <=100 rows.
2. Retry transient NETWORK_FAILED validator outcomes in 5-email batches.
3. Normalize RECEIVING catch-all rows to the normal identity gate (owner decision:
   catch-all deliverability is acceptable for this preparation phase).
4. Finish the 728 unresolved REGA identity-enrichment queue in restart-safe
   chunks using the existing canonical REGA enrichment pipeline/cache.
5. Run existing Outscraper domain-contact discovery on confirmed official REGA
   domains, validate every discovered email, and produce a conservative usable
   recruitment/general-route artifact.

No email sending, Gmail mutation, recruiter contact, application submission,
credit purchase, or top-up code exists in this script.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from career_engine.rega_enrichment.outscraper_validation import validate_emails
from career_engine.rega_enrichment.pipeline import load_canonical, run_pipeline
from career_engine.rega_enrichment.provider_clients import OutscraperClient, ProviderBudget
from runtime.outscraper_sheet_runner import (
    SPREADSHEET_ID,
    read_queue,
    rclone_access_token,
    write_state_updates,
)

DEFAULT_REGA_INPUT = Path(
    "/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/research/rega-enrichment-queue.csv"
)
EXPECTED_QUEUE_ROWS = 1236
EXPECTED_REGA_UNRESOLVED = 728
REGA_BASELINE_COMPLETE = 29
EXPECTED_REGA_UNIVERSE = 757
QUEUE_CHUNK = 25
VALIDATION_BATCH = 5
RETRY_BATCH = 5
REGA_CHUNK = 50
MAX_NETWORK_RETRY_PASSES = 3
ACCEPTED_REGA_SIDECAR_ROOTS = (
    Path("/mnt/storage-box/tmp/rega-enrichment-acceptance/final-hardening-20260823T184847Z/rerun"),
    Path("/home/hameedo/tmp/rega-enrichment/rega-enrichment-latest"),
)

DIRECT_LOCALS = {"hr", "career", "careers", "job", "jobs", "recruit", "recruitment", "talent", "hiring"}
GENERAL_LOCALS = {"info", "contact", "hello", "admin", "office", "enquiry", "enquiries", "inquiry", "inquiries"}
EXCLUDED_LOCALS = {
    "support", "privacy", "legal", "abuse", "finance", "financial", "investor", "investors",
    "ir", "billing", "accounts", "accounting", "sales", "security", "webmaster",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def atomic_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_jsonl(path: Path, key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            value = str(record.get(key) or "").strip().lower()
            if value:
                out[value] = record
    return out


def monitor_paths(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "status": root / "status.json",
        "progress": root / "progress.jsonl",
        "rega_consolidated": root / "rega-enrichment-consolidated.csv",
        "rega_contacts": root / "rega-outscraper-domain-contacts.jsonl",
        "rega_validated": root / "rega-outscraper-validated-emails.jsonl",
        "rega_routes": root / "rega-outscraper-usable-routes.jsonl",
        "final": root / "final-summary.json",
    }


def progress(paths: dict[str, Path], phase: str, **data) -> None:
    event = {"at": utc_now(), "phase": phase, **data, "sends": 0}
    with paths["progress"].open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
    atomic_json(paths["status"], event)
    print(json.dumps(event, sort_keys=True, ensure_ascii=False), flush=True)


def run_queue_chunk(*, retry_network: bool = False, limit: int = 0, batch_size: int = VALIDATION_BATCH) -> dict:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "runtime" / "outscraper_bulk_queue_validate.py"),
        "--batch-size", str(batch_size),
        "--apply",
    ]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    if retry_network:
        cmd.append("--retry-network-failed")
    proc = subprocess.run(cmd, cwd=REPO_ROOT, text=True, capture_output=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError("bounded queue validator failed closed: " + (proc.stderr.strip() or proc.stdout.strip())[-800:])
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("bounded queue validator returned no summary")
    summary = json.loads(lines[-1])
    if not summary.get("ok") or int(summary.get("sends", 0)) != 0:
        raise RuntimeError("bounded queue validator returned unsafe/invalid summary")
    return summary


def queue_snapshot(token: str) -> tuple[list[dict[str, str]], dict]:
    rows = read_queue(token, SPREADSHEET_ID)
    emails = [str(row.get("Email") or "").strip().lower() for row in rows]
    if len(rows) != EXPECTED_QUEUE_ROWS or len({e for e in emails if e}) != EXPECTED_QUEUE_ROWS:
        raise RuntimeError("authoritative Send Queue row/uniqueness invariant failed")
    return rows, {
        "rows": len(rows),
        "without_evidence": sum(not str(row.get("Outscraper_Evidence") or "").strip() for row in rows),
        "network_failed": sum(str(row.get("Outscraper_Status") or "").strip().upper() == "NETWORK_FAILED" for row in rows),
        "status_counts": dict(Counter(str(row.get("Outscraper_Status") or "").strip().upper() or "PENDING" for row in rows)),
        "state_counts": dict(Counter(str(row.get("Send_State") or "").strip() or "EMPTY" for row in rows)),
        "source_counts": dict(Counter(str(row.get("Source_Dataset") or "").strip() or "EMPTY" for row in rows)),
    }


def normalize_receiving_states(token: str, rows: list[dict[str, str]]) -> int:
    updates: list[tuple[str, str, str]] = []
    for row in rows:
        if str(row.get("Outscraper_Status") or "").strip().upper() != "RECEIVING":
            continue
        state = str(row.get("Send_State") or "").strip()
        if state == "HOLD_OUTSCRAPER_CATCH_ALL":
            updates.append((
                str(row.get("Queue_ID") or "").strip(),
                str(row.get("Email") or "").strip().lower(),
                "HOLD_OUTSCRAPER_IDENTITY",
            ))
    written = 0
    for offset in range(0, len(updates), 25):
        written += write_state_updates(token, updates[offset : offset + 25], SPREADSHEET_ID)
    return written


def finish_queue(paths: dict[str, Path], token: str) -> dict:
    rows, snap = queue_snapshot(token)
    progress(paths, "queue-start", **snap)
    cycles = 0
    max_cycles = ((EXPECTED_QUEUE_ROWS + QUEUE_CHUNK - 1) // QUEUE_CHUNK) + 2
    while snap["without_evidence"]:
        cycles += 1
        if cycles > max_cycles:
            raise RuntimeError("queue monitor exceeded bounded chunk cycles")
        result = run_queue_chunk(limit=QUEUE_CHUNK, batch_size=VALIDATION_BATCH)
        rows, snap = queue_snapshot(token)
        progress(paths, "queue-chunk", cycle=cycles, selected=result.get("selected", 0), provider_calls=result.get("provider_calls", 0), **snap)
        if int(result.get("selected", 0)) == 0 and snap["without_evidence"]:
            raise RuntimeError("queue monitor made no progress with evidence still pending")

    for retry_pass in range(1, MAX_NETWORK_RETRY_PASSES + 1):
        rows, snap = queue_snapshot(token)
        before_failed = snap["network_failed"]
        if not before_failed:
            break
        result = run_queue_chunk(retry_network=True, limit=0, batch_size=RETRY_BATCH)
        rows, after = queue_snapshot(token)
        progress(
            paths,
            "queue-network-retry",
            retry_pass=retry_pass,
            selected=result.get("selected", 0),
            before_network_failed=snap["network_failed"],
            after_network_failed=after["network_failed"],
            **{k: v for k, v in after.items() if k != "network_failed"},
        )
        snap = after
        if after["network_failed"] >= before_failed and int(result.get("selected", 0)) == 0:
            break

    rows, snap = queue_snapshot(token)
    normalized = normalize_receiving_states(token, rows)
    if normalized:
        rows, snap = queue_snapshot(token)
    progress(paths, "queue-complete", normalized_catch_all_states=normalized, **snap)
    return snap


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_consolidated(path: Path, fieldnames: list[str], rows_by_company_id: dict[str, dict[str, str]], order: list[str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for company_id in order:
            row = rows_by_company_id.get(company_id)
            if row is not None:
                writer.writerow(row)
    tmp.replace(path)


def bootstrap_accepted_rega_sidecar(consolidated: Path, expected_identity: dict[str, tuple[str, str]]) -> Path | None:
    """Reuse an accepted 728-row sidecar only on exact company-row identity match.

    License numbers are immutable join values but are not a uniqueness key in the
    derived unresolved queue. The canonical pipeline's stable 1-indexed company_id
    is the row key, so checkpointing must preserve duplicate license values.
    """
    expected_ids = set(expected_identity)
    candidates: list[Path] = []
    for root in ACCEPTED_REGA_SIDECAR_ROOTS:
        if not root.exists():
            continue
        candidates.extend(root.rglob("rega-enrichment-sidecar-*.csv"))
    for candidate in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            fieldnames, rows = read_csv_rows(candidate)
        except (OSError, UnicodeError, csv.Error):
            continue
        by_id = {str(row.get("company_id") or "").strip(): row for row in rows}
        if len(rows) != EXPECTED_REGA_UNRESOLVED or set(by_id) != expected_ids or len(by_id) != len(rows):
            continue
        if "assignment" not in fieldnames or "official_domain" not in fieldnames:
            continue
        identity_ok = all(
            (
                str(by_id[company_id].get("License No") or "").strip(),
                str(by_id[company_id].get("English Name") or "").strip(),
            ) == expected_identity[company_id]
            for company_id in expected_ids
        )
        if not identity_ok:
            continue
        write_consolidated(consolidated, fieldnames, by_id, sorted(expected_ids, key=int))
        return candidate
    return None


def finish_rega_identity(paths: dict[str, Path], input_path: Path, workers: int, delay: float) -> tuple[Path, dict]:
    canonical = load_canonical(input_path)
    if len(canonical) != EXPECTED_REGA_UNRESOLVED:
        raise RuntimeError(f"REGA unresolved input expected {EXPECTED_REGA_UNRESOLVED}, got {len(canonical)}")
    order = [c.company_id for c in canonical]
    if len(set(order)) != len(order):
        raise RuntimeError("REGA unresolved input contains duplicate company IDs")
    expected_identity = {c.company_id: (c.license_no, c.english_name) for c in canonical}

    consolidated = paths["rega_consolidated"]
    if not consolidated.is_file():
        bootstrap = bootstrap_accepted_rega_sidecar(consolidated, expected_identity)
        if bootstrap is not None:
            progress(paths, "rega-identity-bootstrap", rows=EXPECTED_REGA_UNRESOLVED, accepted_sidecar=True)
    fieldnames: list[str] = []
    rows_by_company_id: dict[str, dict[str, str]] = {}
    if consolidated.is_file():
        fieldnames, existing = read_csv_rows(consolidated)
        rows_by_company_id = {
            str(row.get("company_id") or "").strip(): row
            for row in existing
            if str(row.get("company_id") or "").strip()
        }
        if not fieldnames or len(rows_by_company_id) != len(existing):
            raise RuntimeError("existing REGA consolidated checkpoint is malformed")
        for company_id, row in rows_by_company_id.items():
            if company_id not in expected_identity:
                raise RuntimeError("existing REGA checkpoint contains unknown company ID")
            identity = (
                str(row.get("License No") or "").strip(),
                str(row.get("English Name") or "").strip(),
            )
            if identity != expected_identity[company_id]:
                raise RuntimeError("existing REGA checkpoint identity mismatch")

    pending = [c for c in canonical if c.company_id not in rows_by_company_id]
    progress(paths, "rega-identity-start", unresolved_total=len(canonical), already_checkpointed=len(rows_by_company_id), pending=len(pending), baseline_complete_preserved=REGA_BASELINE_COMPLETE)

    for offset in range(0, len(pending), REGA_CHUNK):
        chunk = pending[offset : offset + REGA_CHUNK]
        chunk_ids = [c.company_id for c in chunk]
        chunk_dir = paths["root"] / "rega-chunks" / f"{int(chunk_ids[0]):04d}-{int(chunk_ids[-1]):04d}"
        manifest = run_pipeline(
            input_path,
            chunk_dir,
            company_ids=chunk_ids,
            delay_s=delay,
            use_cache=True,
            refresh=False,
            workers=workers,
        )
        sidecar = Path(str(manifest.get("sidecar_path") or ""))
        if not sidecar.is_file() or int(manifest.get("sidecar_rows", 0)) != len(chunk):
            raise RuntimeError("REGA chunk did not produce the exact expected sidecar")
        chunk_fields, chunk_rows = read_csv_rows(sidecar)
        if len(chunk_rows) != len(chunk):
            raise RuntimeError("REGA chunk sidecar row count mismatch")
        if not fieldnames:
            fieldnames = chunk_fields
        elif fieldnames != chunk_fields:
            raise RuntimeError("REGA chunk sidecar schema changed")
        for row in chunk_rows:
            company_id = str(row.get("company_id") or "").strip()
            if not company_id or company_id not in expected_identity:
                raise RuntimeError("REGA chunk emitted invalid company ID")
            identity = (
                str(row.get("License No") or "").strip(),
                str(row.get("English Name") or "").strip(),
            )
            if identity != expected_identity[company_id]:
                raise RuntimeError("REGA chunk emitted identity mismatch")
            rows_by_company_id[company_id] = row
        write_consolidated(consolidated, fieldnames, rows_by_company_id, order)
        counts = Counter(str(r.get("assignment") or "").strip().lower() or "blank" for r in rows_by_company_id.values())
        progress(paths, "rega-identity-chunk", completed=len(rows_by_company_id), pending=EXPECTED_REGA_UNRESOLVED - len(rows_by_company_id), assignment_counts=dict(counts), chunk_first=chunk_ids[0], chunk_last=chunk_ids[-1])

    fields, rows = read_csv_rows(consolidated)
    if len(rows) != EXPECTED_REGA_UNRESOLVED:
        raise RuntimeError("REGA consolidated checkpoint is not complete")
    counts = Counter(str(row.get("assignment") or "").strip().lower() or "blank" for row in rows)
    summary = {
        "unresolved_processed": len(rows),
        "baseline_complete_preserved": REGA_BASELINE_COMPLETE,
        "full_universe": len(rows) + REGA_BASELINE_COMPLETE,
        "assignment_counts": dict(counts),
        "confirmed_domains": len({normalized_domain(str(row.get("official_domain") or "")) for row in rows if str(row.get("assignment") or "").strip().lower() == "confirmed" and normalized_domain(str(row.get("official_domain") or ""))}),
        "careers_or_ats_routes": sum(bool(str(row.get("careers_page") or "").strip() or str(row.get("ats_url") or "").strip()) for row in rows),
    }
    if summary["full_universe"] != EXPECTED_REGA_UNIVERSE:
        raise RuntimeError("REGA full-universe invariant failed")
    progress(paths, "rega-identity-complete", **summary)
    return consolidated, summary


def normalized_domain(value: str) -> str:
    raw = value.strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = (urlsplit(raw).hostname or "").lower()
    return raw.removeprefix("www.").strip().strip("/")


def safe_contact_record(record: dict) -> dict:
    meta = dict(record.get("metadata") or {})
    return {
        "provider": "outscraper",
        "status": str(record.get("status") or "failed"),
        "retrieved_at": str(record.get("retrieved_at") or ""),
        "source_url": str(record.get("source_url") or "https://api.outscraper.com/emails-and-contacts"),
        "email": str(meta.get("email") or "").strip().lower(),
        "domain": normalized_domain(str(meta.get("domain") or "")),
        "source_urls": [str(x) for x in (meta.get("source_urls") or []) if str(x).startswith(("http://", "https://"))][:20],
        "mailbox_class": str(meta.get("mailbox_class") or "unknown"),
        "cost_status": str(record.get("cost_status") or "unknown"),
    }


def safe_validation(record: dict) -> dict:
    meta = dict(record.get("metadata") or {})
    return {
        "provider": "outscraper",
        "email": str(meta.get("email") or "").strip().lower(),
        "provider_status": str(record.get("status") or "provider_failed").upper(),
        "verification": str(meta.get("verification") or record.get("status") or "UNKNOWN").upper(),
        "status_details": str(meta.get("status_details") or ""),
        "safe_to_send_provider_flag": bool(meta.get("safe_to_send", False)),
        "checked_at": str(record.get("retrieved_at") or ""),
        "source_url": str(record.get("source_url") or "https://api.outscraper.com/email-validator"),
        "checkpoint_state": "complete",
    }


def checkpoint_validation_inflight(paths: dict[str, Path], validated: dict[str, dict], emails: list[str]) -> None:
    for email in emails:
        validated[email] = {
            "provider": "outscraper",
            "email": email,
            "provider_status": "INFLIGHT_AMBIGUOUS",
            "verification": "UNKNOWN",
            "status_details": "provider call started; completion not yet checkpointed",
            "safe_to_send_provider_flag": False,
            "checked_at": utc_now(),
            "source_url": "https://api.outscraper.com/email-validator",
            "checkpoint_state": "inflight",
        }
    atomic_jsonl(paths["rega_validated"], [validated[k] for k in sorted(validated)])


def assert_no_ambiguous_validation(validated: dict[str, dict], candidate_emails: list[str]) -> None:
    ambiguous = [
        email for email in candidate_emails
        if str(validated.get(email, {}).get("checkpoint_state") or "") == "inflight"
    ]
    if ambiguous:
        raise RuntimeError("ambiguous prior Outscraper email validation exists; refusing automatic paid-call repeat")


def mailbox_route_kind(email: str) -> str:
    local = email.split("@", 1)[0].lower() if "@" in email else ""
    if local in EXCLUDED_LOCALS:
        return "excluded"
    if local in DIRECT_LOCALS:
        return "recruitment"
    if local in GENERAL_LOCALS:
        return "general"
    return "other"


def finish_rega_outscraper(paths: dict[str, Path], consolidated: Path, client: OutscraperClient) -> dict:
    _, rows = read_csv_rows(consolidated)
    by_domain: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if str(row.get("assignment") or "").strip().lower() != "confirmed":
            continue
        domain = normalized_domain(str(row.get("official_domain") or ""))
        if domain and "." in domain:
            by_domain[domain].append(row)
    domains = sorted(by_domain)

    contact_records = load_jsonl(paths["rega_contacts"], "domain")
    progress(paths, "rega-outscraper-contacts-start", confirmed_domains=len(domains), already_checkpointed=len(set(contact_records) & set(domains)), pending=len([d for d in domains if d not in contact_records]))
    for domain in domains:
        existing = contact_records.get(domain)
        if existing is not None:
            if str(existing.get("checkpoint_state") or "") == "inflight":
                raise RuntimeError(f"ambiguous prior Outscraper domain call for {domain}; refusing automatic paid-call repeat")
            existing_statuses = {str(item.get("status") or "").lower() for item in existing.get("contacts", [])}
            checkpoint_state = str(existing.get("checkpoint_state") or "")
            legacy_complete = not checkpoint_state and bool(str(existing.get("retrieved_at") or ""))
            if (checkpoint_state == "complete" or legacy_complete) and not existing_statuses.intersection({"failed", "network_failed"}):
                continue
        contacts = []
        budget = None
        attempt_used = 0
        for attempt_used in range(1, 4):
            contact_records[domain] = {
                "domain": domain,
                "license_nos": sorted({str(row.get("License No") or "") for row in by_domain[domain]}),
                "english_names": sorted({str(row.get("English Name") or "") for row in by_domain[domain]}),
                "contacts": [],
                "candidate_emails": [],
                "checkpoint_state": "inflight",
                "attempt": attempt_used,
                "started_at": utc_now(),
            }
            atomic_jsonl(paths["rega_contacts"], [contact_records[k] for k in sorted(contact_records)])
            budget = ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=0, max_domains=1)
            raw = client.domain_contacts(domain, budget)
            contacts = [safe_contact_record(item) for item in raw]
            emails = sorted({item["email"] for item in contacts if item["email"]})
            record = {
                "domain": domain,
                "license_nos": sorted({str(row.get("License No") or "") for row in by_domain[domain]}),
                "english_names": sorted({str(row.get("English Name") or "") for row in by_domain[domain]}),
                "contacts": contacts,
                "candidate_emails": emails,
                "provider_calls": budget.calls,
                "retrieved_at": utc_now(),
                "checkpoint_state": "complete",
                "attempt": attempt_used,
            }
            contact_records[domain] = record
            atomic_jsonl(paths["rega_contacts"], [contact_records[k] for k in sorted(contact_records)])
            statuses = {str(item.get("status") or "").lower() for item in contacts}
            if not statuses.intersection({"failed", "network_failed"}):
                break
        emails = sorted({item["email"] for item in contacts if item["email"]})
        progress(paths, "rega-outscraper-domain", domain=domain, candidate_emails=len(emails), completed=len(set(contact_records) & set(domains)), total=len(domains))

    candidate_emails = sorted({email for domain in domains for email in contact_records.get(domain, {}).get("candidate_emails", []) if email})
    validated = load_jsonl(paths["rega_validated"], "email")
    assert_no_ambiguous_validation(validated, candidate_emails)
    pending = [email for email in candidate_emails if email not in validated]
    progress(paths, "rega-outscraper-validation-start", unique_candidate_emails=len(candidate_emails), already_validated=len(validated), pending=len(pending))
    for offset in range(0, len(pending), VALIDATION_BATCH):
        chunk = pending[offset : offset + VALIDATION_BATCH]
        checkpoint_validation_inflight(paths, validated, chunk)
        budget = ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=float(len(chunk)), max_domains=0)
        records = validate_emails(client, chunk, budget, batch_size=min(VALIDATION_BATCH, len(chunk)))
        items = [safe_validation(record) for record in records]
        by_email = {item["email"]: item for item in items if item["email"]}
        if set(by_email) != set(chunk):
            raise RuntimeError("REGA candidate validator did not return exact per-email results")
        validated.update(by_email)
        atomic_jsonl(paths["rega_validated"], [validated[k] for k in sorted(validated)])
        progress(paths, "rega-outscraper-validation-chunk", completed=len(set(validated) & set(candidate_emails)), total=len(candidate_emails), provider_calls=budget.calls)

    for retry_pass in range(1, MAX_NETWORK_RETRY_PASSES + 1):
        retry_emails = [
            email for email in candidate_emails
            if validated.get(email, {}).get("provider_status") == "NETWORK_FAILED"
        ]
        if not retry_emails:
            break
        before_failed = len(retry_emails)
        for offset in range(0, len(retry_emails), RETRY_BATCH):
            chunk = retry_emails[offset : offset + RETRY_BATCH]
            checkpoint_validation_inflight(paths, validated, chunk)
            budget = ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=float(len(chunk)), max_domains=0)
            records = validate_emails(client, chunk, budget, batch_size=min(RETRY_BATCH, len(chunk)))
            items = [safe_validation(record) for record in records]
            by_email = {item["email"]: item for item in items if item["email"]}
            if set(by_email) != set(chunk):
                raise RuntimeError("REGA retry validator did not return exact per-email results")
            validated.update(by_email)
            atomic_jsonl(paths["rega_validated"], [validated[k] for k in sorted(validated)])
        after_failed = sum(validated.get(email, {}).get("provider_status") == "NETWORK_FAILED" for email in candidate_emails)
        progress(paths, "rega-outscraper-validation-retry", retry_pass=retry_pass, before_network_failed=before_failed, after_network_failed=after_failed)
        if after_failed >= before_failed:
            break

    routes: list[dict] = []
    for domain in domains:
        emails = contact_records.get(domain, {}).get("candidate_emails", [])
        receiving = [email for email in emails if validated.get(email, {}).get("provider_status") == "RECEIVING"]
        direct = [email for email in receiving if mailbox_route_kind(email) == "recruitment"]
        general = [email for email in receiving if mailbox_route_kind(email) == "general"]
        selected = direct if direct else general[:1]
        for email in selected:
            val = validated[email]
            routes.append({
                "domain": domain,
                "email": email,
                "route_kind": mailbox_route_kind(email),
                "verification": val.get("verification"),
                "status_details": val.get("status_details"),
                "catch_all_accepted_for_preparation": "CATCH ALL" in str(val.get("status_details") or "").upper() or "CATCH ALL" in str(val.get("verification") or "").upper(),
                "identity_gate": "confirmed_official_domain",
                "usable_for_preparation": True,
                "send_authorized": False,
            })
    atomic_jsonl(paths["rega_routes"], routes)
    status_counts = Counter(str(item.get("provider_status") or "UNKNOWN") for item in validated.values() if item.get("email") in candidate_emails)
    summary = {
        "confirmed_official_domains": len(domains),
        "candidate_emails_discovered": len(candidate_emails),
        "validated_candidate_emails": len([e for e in candidate_emails if e in validated]),
        "validation_status_counts": dict(status_counts),
        "usable_routes": len(routes),
        "companies_with_usable_routes": len({r["domain"] for r in routes}),
        "companies_without_usable_route": len(domains) - len({r["domain"] for r in routes}),
        "catch_all_accepted": sum(bool(r["catch_all_accepted_for_preparation"]) for r in routes),
        "sends": 0,
    }
    progress(paths, "rega-outscraper-complete", **summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Required for live provider/Sheet execution")
    parser.add_argument("--monitor-dir", default="runtime/acceptance/outscraper-monitor-20260901")
    parser.add_argument("--rega-input", default=str(DEFAULT_REGA_INPUT))
    parser.add_argument("--rega-workers", type=int, default=4)
    parser.add_argument("--rega-delay", type=float, default=0.2)
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing live monitor without --apply")
    key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
    if not key:
        raise SystemExit("missing Outscraper runtime key")

    paths = monitor_paths(REPO_ROOT / args.monitor_dir)
    progress(paths, "start", catch_all_policy="acceptable_deliverability_then_identity_gate", no_send=True)
    token = rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
    client = OutscraperClient(key)
    before = client.balance()
    if str(before.get("status") or "") != "success":
        raise SystemExit("Outscraper account/balance preflight failed")
    balance_before = (before.get("metadata") or {}).get("balance")
    account_status = (before.get("metadata") or {}).get("account_status")
    if account_status != "valid" or not isinstance(balance_before, (int, float)):
        raise SystemExit("Outscraper account/balance preflight returned invalid state")
    progress(paths, "provider-preflight", account_status=account_status, balance=balance_before)

    queue = finish_queue(paths, token)
    consolidated, rega_identity = finish_rega_identity(
        paths,
        Path(args.rega_input),
        workers=max(1, min(int(args.rega_workers), 8)),
        delay=max(0.0, float(args.rega_delay)),
    )
    rega_provider = finish_rega_outscraper(paths, consolidated, client)

    after = client.balance()
    balance_after = (after.get("metadata") or {}).get("balance")
    if str(after.get("status") or "") != "success" or (after.get("metadata") or {}).get("account_status") != "valid" or not isinstance(balance_after, (int, float)):
        raise SystemExit("Outscraper post-run balance verification failed")

    final = {
        "ok": True,
        "finished_at": utc_now(),
        "queue": queue,
        "rega_identity": rega_identity,
        "rega_outscraper": rega_provider,
        "provider": {
            "account_status": "valid",
            "balance_before": balance_before,
            "balance_after": balance_after,
            "balance_delta": round(float(balance_after) - float(balance_before), 6),
        },
        "catch_all_policy": "acceptable_deliverability_then_identity_gate",
        "sends": 0,
        "purchases_or_topups": 0,
        "secret_values_in_output": False,
    }
    atomic_json(paths["final"], final)
    progress(paths, "complete", final_summary=str(paths["final"]), balance_after=balance_after, balance_delta=final["provider"]["balance_delta"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
