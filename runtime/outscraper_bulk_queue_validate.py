#!/usr/bin/env python3
"""Bounded Outscraper validation for the authoritative live Send Queue.

No email-send path exists here. The runner selects only rows explicitly staged
as PENDING_OUTSCRAPER_VALIDATION (or NETWORK_FAILED when retry is requested),
uses the existing canonical validator, existing rclone Google auth, and the
Infisical-injected Outscraper key, then writes provider evidence back with
immutable Queue_ID metadata and exact readback verification.
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
PENDING_STATE = "PENDING_OUTSCRAPER_VALIDATION"
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


def journal_replay_items(journal: dict, selected: list[dict[str, str]]) -> list[dict]:
    state = str(journal.get("state") or "")
    if state == "inflight":
        raise RuntimeError("prior Outscraper queue call is ambiguous; refusing automatic paid-call repeat")
    if state in {"", "applied"}:
        return []
    if state != "complete":
        raise RuntimeError("Outscraper queue journal has unknown state")

    selected_pairs = {(item["queue_id"], item["email"]) for item in selected}
    journal_selected = [item for item in journal.get("selected", []) if isinstance(item, dict)]
    journal_pairs = {
        (str(item.get("queue_id") or "").strip(), str(item.get("email") or "").strip().lower())
        for item in journal_selected
    }
    if selected_pairs != journal_pairs:
        raise RuntimeError("completed Outscraper journal does not match current pending queue")

    results = [dict(item) for item in journal.get("results", []) if isinstance(item, dict)]
    by_email = {str(item.get("email") or "").strip().lower(): item for item in results}
    selected_emails = {item["email"] for item in selected}
    if set(by_email) != selected_emails:
        raise RuntimeError("completed Outscraper journal results do not match current pending queue")
    return [by_email[item["email"]] for item in selected]


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


def _preflight_queue(rows: list[dict[str, str]]) -> None:
    if not rows:
        raise SystemExit("authoritative Send Queue is empty")
    queue_ids = [str(row.get("Queue_ID") or "").strip() for row in rows]
    emails = [str(row.get("Email") or "").strip().lower() for row in rows]
    if any(not value for value in queue_ids) or any(not value for value in emails):
        raise SystemExit("authoritative Send Queue contains blank immutable identity")
    if len(set(queue_ids)) != len(queue_ids) or len(set(emails)) != len(emails):
        raise SystemExit("authoritative Send Queue row/uniqueness preflight failed")


def _eligible_pending(row: dict[str, str], retry_network_failed: bool) -> bool:
    status = str(row.get("Outscraper_Status") or "").strip().upper()
    evidence = str(row.get("Outscraper_Evidence") or "").strip()
    state = str(row.get("Send_State") or "").strip().upper()
    if retry_network_failed:
        return status == "NETWORK_FAILED"
    return state == PENDING_STATE and not status and not evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Validate at most N selected rows; 0 means all")
    parser.add_argument("--batch-size", type=int, default=10, help="Provider batch size, bounded to 1..25")
    parser.add_argument("--retry-network-failed", action="store_true", help="Select NETWORK_FAILED rows instead of fresh pending rows")
    parser.add_argument(
        "--source-dataset-contains",
        default="",
        help="Optional case-insensitive Source_Dataset filter, e.g. REGA or BALADY",
    )
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
    _preflight_queue(rows)
    before_queue_ids = {str(row.get("Queue_ID") or "").strip() for row in rows}

    source_filter = str(args.source_dataset_contains or "").strip().lower()
    pending = []
    for row in rows:
        if source_filter and source_filter not in str(row.get("Source_Dataset") or "").lower():
            continue
        if not _eligible_pending(row, bool(args.retry_network_failed)):
            continue
        queue_id = str(row.get("Queue_ID") or "").strip()
        email = str(row.get("Email") or "").strip().lower()
        pending.append((queue_id, row, email))

    if args.limit > 0:
        pending = pending[: args.limit]
    if not pending:
        print(json.dumps({
            "ok": True,
            "queue_rows": len(rows),
            "selected": 0,
            "writes": 0,
            "sends": 0,
            "provider_calls": 0,
            "source_dataset_filter": source_filter,
            "secret_values_in_output": False,
        }, sort_keys=True))
        return 0

    selected = [{"queue_id": queue_id, "email": email} for queue_id, _row, email in pending]
    journal = load_journal()
    try:
        replay_items = journal_replay_items(journal, selected)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    client = OutscraperClient(key)
    before = balance_snapshot(client)
    estimate = round(len(pending) * RATE_USD_PER_EMAIL_UPPER_BOUND, 6)
    if before["status"] != "success" or before["account_status"] != "valid" or not isinstance(before["balance"], (int, float)):
        raise SystemExit("Outscraper account/balance preflight failed")
    if float(before["balance"]) < estimate:
        raise SystemExit("existing Outscraper balance is below conservative validation cost bound")

    batch_size = max(1, min(int(args.batch_size), MAX_BATCH_SIZE))
    if replay_items:
        items = replay_items
        budget = ProviderBudget(allow_existing_credit=True, max_calls=0, max_credits=0, max_domains=0)
    else:
        atomic_json(JOURNAL_PATH, {
            "state": "inflight",
            "selected": selected,
            "retry_network_failed": bool(args.retry_network_failed),
            "source_dataset_filter": source_filter,
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
        selected_emails = {item["email"] for item in selected}
        if set(by_email_after_call) != selected_emails:
            raise SystemExit("validator did not return exactly one result per selected email")
        atomic_json(JOURNAL_PATH, {
            "state": "complete",
            "selected": selected,
            "retry_network_failed": bool(args.retry_network_failed),
            "source_dataset_filter": source_filter,
            "batch_size": batch_size,
            "results": items,
        })

    by_email = {item["email"]: item for item in items if item.get("email")}
    selected_emails = {item["email"] for item in selected}
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
    _preflight_queue(after_rows)
    after_queue_ids = {str(row.get("Queue_ID") or "").strip() for row in after_rows}
    if not before_queue_ids.issubset(after_queue_ids):
        raise SystemExit("Sheet readback lost pre-existing queue rows")

    readback_by_id = {
        str(row.get("Queue_ID") or "").strip(): row
        for row in after_rows
    }
    fields = (
        "Send_State",
        "Outscraper_Status",
        "Outscraper_Verification",
        "Outscraper_Replacement_Email",
        "Outscraper_Evidence",
        "Outscraper_Checked_At",
    )
    for queue_id, _, email in pending:
        expected = _sheet_values(by_email[email])
        actual = readback_by_id.get(queue_id)
        if actual is None:
            raise SystemExit("Sheet readback target Queue_ID missing")
        if str(actual.get("Email") or "").strip().lower() != email:
            raise SystemExit("Sheet readback immutable identity mismatch")
        if any(str(actual.get(field) or "") != str(expected.get(field) or "") for field in fields):
            raise SystemExit("Sheet readback exact field verification failed")

    atomic_json(JOURNAL_PATH, {
        "state": "applied",
        "selected": selected,
        "retry_network_failed": bool(args.retry_network_failed),
        "source_dataset_filter": source_filter,
        "batch_size": batch_size,
        "results": items,
    })

    after = balance_snapshot(client)
    if after["status"] != "success" or after["account_status"] != "valid" or not isinstance(after["balance"], (int, float)):
        raise SystemExit("Outscraper post-run balance verification failed")
    balance_delta = round(float(after["balance"]) - float(before["balance"]), 6)

    provider_counts = dict(Counter(item["provider_status"] for item in items))
    selected_ids = {queue_id for queue_id, _, _ in pending}
    readback = [row for row in after_rows if str(row.get("Queue_ID") or "").strip() in selected_ids]
    state_counts = dict(Counter(str(row.get("Send_State") or "") for row in readback))
    remaining = sum(_eligible_pending(row, False) for row in after_rows)
    summary = {
        "ok": True,
        "queue_rows_before": len(rows),
        "queue_rows_after": len(after_rows),
        "selected": len(pending),
        "batch_size_used": batch_size,
        "batch_size_max": MAX_BATCH_SIZE,
        "provider_calls": budget.calls,
        "provider_counts": provider_counts,
        "sheet_state_counts_for_selected": state_counts,
        "remaining_pending_outscraper_validation": remaining,
        "balance_before": before["balance"],
        "balance_after": after["balance"],
        "balance_delta": balance_delta,
        "conservative_cost_bound_usd": estimate,
        "source_dataset_filter": source_filter,
        "artifact": str(artifact_path),
        "writes": len(updates),
        "sends": 0,
        "secret_values_in_output": False,
    }
    (artifact_dir / f"outscraper-queue-summary-{len(pending)}.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
