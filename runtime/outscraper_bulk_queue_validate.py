#!/usr/bin/env python3
"""Task-only bounded Outscraper validation for the authoritative Send Queue.

No email send path exists here. Uses the existing canonical validator, existing
rclone Google auth, and the dedicated Infisical-injected Outscraper key.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from career_engine.rega_enrichment.outscraper_validation import MAX_BATCH_SIZE, validate_emails
from career_engine.rega_enrichment.provider_clients import OutscraperClient, ProviderBudget
from runtime.outscraper_sheet_runner import (
    SPREADSHEET_ID,
    _sheet_values,
    read_queue,
    rclone_access_token,
    write_updates,
)

RATE_USD_PER_EMAIL_UPPER_BOUND = 0.003
EXPECTED_ROWS = 1236
JOURNAL_PATH = Path("runtime/acceptance/outscraper-queue-journal.json")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_journal(path: Path = JOURNAL_PATH) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Outscraper queue journal is malformed")
    return value


def journal_replay_items(journal: dict, selected_emails: set[str]) -> list[dict]:
    state = str(journal.get("state") or "")
    if state == "inflight":
        raise RuntimeError("prior Outscraper queue call is ambiguous; refusing automatic paid-call repeat")
    if state in {"", "applied"}:
        return []
    if state != "complete":
        raise RuntimeError("Outscraper queue journal has unknown state")
    results = [dict(item) for item in journal.get("results", []) if isinstance(item, dict)]
    by_email = {str(item.get("email") or "").strip().lower(): item for item in results}
    if selected_emails and not selected_emails.issubset(set(by_email)):
        raise RuntimeError("completed Outscraper journal does not match current pending queue")
    return [by_email[email] for email in sorted(selected_emails)]


def safe_item(record: dict) -> dict:
    meta = dict(record.get("metadata") or {})
    return {
        "provider": "outscraper",
        "email": str(meta.get("email") or "").strip().lower(),
        "verification": str(meta.get("verification") or record.get("status") or "UNKNOWN").upper(),
        "safe_to_send": bool(meta.get("safe_to_send", False)),
        "status_details": str(meta.get("status_details") or ""),
        "provider_status": str(record.get("status") or "provider_failed").upper(),
        "checked_at": str(record.get("retrieved_at") or ""),
        "source_url": str(record.get("source_url") or "https://api.outscraper.com/email-validator"),
    }


def balance_snapshot(client: OutscraperClient) -> dict:
    result = client.balance()
    meta = dict(result.get("metadata") or {})
    return {
        "status": str(result.get("status") or "failed"),
        "account_status": str(meta.get("account_status") or ""),
        "balance": meta.get("balance"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Validate at most N selected rows; 0 means all")
    parser.add_argument("--batch-size", type=int, default=10, help="Provider batch size, bounded to 1..25")
    parser.add_argument("--retry-network-failed", action="store_true", help="Select NETWORK_FAILED rows instead of evidence-empty rows")
    parser.add_argument("--apply", action="store_true", help="Required to call provider and write Sheet")
    parser.add_argument("--spreadsheet-id", default=SPREADSHEET_ID)
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing provider/write execution without --apply")

    key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
    if not key:
        raise SystemExit("missing Outscraper runtime key")

    token = rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
    rows = read_queue(token, args.spreadsheet_id)
    emails = [str(row.get("Email") or "").strip().lower() for row in rows]
    if len(rows) != EXPECTED_ROWS or len({e for e in emails if e}) != EXPECTED_ROWS:
        raise SystemExit("authoritative queue row/uniqueness preflight failed")

    batch_size = max(1, min(int(args.batch_size), MAX_BATCH_SIZE))
    pending = [
        (str(row.get("Queue_ID") or "").strip(), row, email)
        for row, email in zip(rows, emails)
        if email and (
            str(row.get("Outscraper_Status") or "").strip().upper() == "NETWORK_FAILED"
            if args.retry_network_failed
            else not str(row.get("Outscraper_Evidence") or "").strip()
        )
    ]
    if args.limit > 0:
        pending = pending[: args.limit]
    if not pending:
        print(json.dumps({"ok": True, "selected": 0, "writes": 0, "sends": 0, "provider_calls": 0, "secret_values_in_output": False}, sort_keys=True))
        return 0

    selected = [
        {"queue_id": queue_id, "email": email}
        for queue_id, _row, email in pending
    ]
    selected_emails = {item["email"] for item in selected}
    journal = load_journal()
    try:
        replay_items = journal_replay_items(journal, selected_emails)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    client = OutscraperClient(key)
    before = balance_snapshot(client)
    estimate = round(len(pending) * RATE_USD_PER_EMAIL_UPPER_BOUND, 6)
    if before["status"] != "success" or before["account_status"] != "valid" or not isinstance(before["balance"], (int, float)):
        raise SystemExit("Outscraper account/balance preflight failed")
    if float(before["balance"]) < estimate:
        raise SystemExit("existing Outscraper balance is below conservative validation cost bound")

    if replay_items:
        items = replay_items
        budget = ProviderBudget(allow_existing_credit=True, max_calls=0, max_credits=0, max_domains=0)
    else:
        atomic_json(JOURNAL_PATH, {
            "state": "inflight",
            "selected": selected,
            "retry_network_failed": bool(args.retry_network_failed),
            "batch_size": batch_size,
        })
        budget = ProviderBudget(
            allow_existing_credit=True,
            max_calls=max(1, math.ceil(len(pending) / batch_size)),
            max_credits=float(len(pending)),
            max_domains=0,
        )
        records = validate_emails(client, [email for _, _, email in pending], budget, batch_size=batch_size)
        items = [safe_item(record) for record in records]
        by_email_after_call = {item["email"]: item for item in items if item["email"]}
        if set(by_email_after_call) != selected_emails:
            raise SystemExit("validator did not return exactly one result per selected email")
        atomic_json(JOURNAL_PATH, {
            "state": "complete",
            "selected": selected,
            "retry_network_failed": bool(args.retry_network_failed),
            "batch_size": batch_size,
            "results": items,
        })

    by_email = {item["email"]: item for item in items if item.get("email")}
    if set(by_email) != selected_emails:
        raise SystemExit("Outscraper journal/results do not match current selected emails")

    updates = [(queue_id, email, _sheet_values(by_email[email])) for queue_id, _, email in pending]
    for offset in range(0, len(updates), MAX_BATCH_SIZE):
        write_updates(token, updates[offset : offset + MAX_BATCH_SIZE], args.spreadsheet_id)

    artifact_dir = Path("runtime/acceptance")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"outscraper-queue-results-{len(pending)}.jsonl"
    artifact_path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in items), encoding="utf-8")

    after_rows = read_queue(token, args.spreadsheet_id)
    if len(after_rows) != EXPECTED_ROWS:
        raise SystemExit("Sheet readback row count changed")
    selected_emails = {email for _, _, email in pending}
    readback = [row for row in after_rows if str(row.get("Email") or "").strip().lower() in selected_emails]
    if len(readback) != len(pending):
        raise SystemExit("Sheet readback selected row count mismatch")
    readback_by_email = {str(row.get("Email") or "").strip().lower(): row for row in readback}
    fields = ("Send_State", "Outscraper_Status", "Outscraper_Verification", "Outscraper_Replacement_Email", "Outscraper_Evidence", "Outscraper_Checked_At")
    for _, _, email in pending:
        expected = _sheet_values(by_email[email])
        actual = readback_by_email.get(email)
        if actual is None or any(str(actual.get(field) or "") != str(expected.get(field) or "") for field in fields):
            raise SystemExit("Sheet readback exact field verification failed")

    atomic_json(JOURNAL_PATH, {
        "state": "applied",
        "selected": selected,
        "retry_network_failed": bool(args.retry_network_failed),
        "batch_size": batch_size,
        "results": items,
    })

    after = balance_snapshot(client)
    if after["status"] != "success" or after["account_status"] != "valid" or not isinstance(after["balance"], (int, float)):
        raise SystemExit("Outscraper post-run balance verification failed")
    balance_delta = round(float(after["balance"]) - float(before["balance"]), 6)

    provider_counts = dict(Counter(item["provider_status"] for item in items))
    state_counts = dict(Counter(str(row.get("Send_State") or "") for row in readback))
    remaining = sum(not str(row.get("Outscraper_Evidence") or "").strip() for row in after_rows)
    summary = {
        "ok": True,
        "selected": len(pending),
        "batch_size_used": batch_size,
        "batch_size_max": MAX_BATCH_SIZE,
        "provider_calls": budget.calls,
        "provider_counts": provider_counts,
        "sheet_state_counts_for_selected": state_counts,
        "remaining_without_outscraper_evidence": remaining,
        "balance_before": before["balance"],
        "balance_after": after["balance"],
        "balance_delta": balance_delta,
        "conservative_cost_bound_usd": estimate,
        "artifact": str(artifact_path),
        "writes": len(updates),
        "sends": 0,
        "secret_values_in_output": False,
    }
    (artifact_dir / f"outscraper-queue-summary-{len(pending)}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
