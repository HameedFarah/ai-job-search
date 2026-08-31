#!/usr/bin/env python3
"""Validate a Career Engine outreach CSV through Outscraper, fail closed.

This runner is intentionally small and runtime-oriented. The Outscraper API key
is read only from the environment (normally injected by the dedicated canonical
Infisical runtime manifest) and is never written to output. It does not send
email and it does not promote a row to send-ready merely because a mailbox is
receiving; company/source identity remains a separate Career Engine gate.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from career_engine.rega_enrichment.outscraper_validation import validate_emails
from career_engine.rega_enrichment.provider_clients import OutscraperClient, ProviderBudget


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalise_email(value: object) -> str:
    return str(value or "").strip().lower()


def read_rows(path: Path, email_column: str) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or email_column not in reader.fieldnames:
            raise SystemExit(f"missing required email column: {email_column}")
        rows = list(reader)
    emails: list[str] = []
    seen: set[str] = set()
    for row in rows:
        email = normalise_email(row.get(email_column))
        if email and email not in seen:
            seen.add(email)
            emails.append(email)
    return rows, emails


def result_status(record: dict) -> str:
    return str(record.get("status") or "provider_failed").upper()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Career Engine outreach emails with Outscraper")
    parser.add_argument("--input", required=True, help="Input CSV")
    parser.add_argument("--email-column", default="Email", help="Email column name; default: Email")
    parser.add_argument("--output-jsonl", required=True, help="Sanitized per-email result JSONL")
    parser.add_argument("--summary", required=True, help="Sanitized summary JSON")
    parser.add_argument("--batch-size", type=int, default=250, help="1-1000; default 250")
    parser.add_argument(
        "--allow-existing-credit",
        action="store_true",
        help="Required to permit existing/trial/prepaid Outscraper credit; this runner never purchases credit",
    )
    args = parser.parse_args()

    if not args.allow_existing_credit:
        raise SystemExit("refusing billable provider call without --allow-existing-credit")

    key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
    if not key:
        raise SystemExit("OUTSCRAPER_API_KEY is not present in the runtime environment")

    client = OutscraperClient(key)
    account_probe = client.balance()
    if account_probe.get("status") != "success":
        raise SystemExit(
            "Outscraper account probe failed closed: "
            + str(account_probe.get("status") or "unknown")
        )

    batch_size = max(1, min(int(args.batch_size), 1000))
    input_path = Path(args.input)
    output_path = Path(args.output_jsonl)
    summary_path = Path(args.summary)
    rows, emails = read_rows(input_path, args.email_column)

    budget = ProviderBudget(
        allow_existing_credit=True,
        max_calls=max(1, math.ceil(len(emails) / batch_size)),
        max_credits=float(len(emails)),
        max_domains=0,
    )
    records = validate_emails(
        client,
        emails,
        budget,
        batch_size=batch_size,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            meta = dict(record.get("metadata") or {})
            safe = {
                "provider": "outscraper",
                "email": normalise_email(meta.get("email")),
                "verification": str(meta.get("verification") or record.get("status") or "provider_failed").upper(),
                "safe_to_send": bool(meta.get("safe_to_send", False)),
                "status_details": str(meta.get("status_details") or ""),
                "provider_status": str(record.get("status") or "provider_failed"),
                "checked_at": str(record.get("retrieved_at") or utc_now()),
                "source_url": str(record.get("source_url") or "https://api.outscraper.com/email-validator"),
            }
            handle.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")

    counts = Counter(result_status(record) for record in records)
    summary = {
        "generated_at": utc_now(),
        "input_rows": len(rows),
        "unique_emails": len(emails),
        "provider": "outscraper",
        "account_probe": "success",
        "counts": dict(sorted(counts.items())),
        "receiving": counts.get("RECEIVING", 0),
        "invalid": counts.get("INVALID", 0),
        "blacklisted": counts.get("BLACKLISTED", 0),
        "unknown": counts.get("UNKNOWN", 0),
        "provider_failed_or_held": len(records)
        - counts.get("RECEIVING", 0)
        - counts.get("INVALID", 0)
        - counts.get("BLACKLISTED", 0)
        - counts.get("UNKNOWN", 0),
        "identity_gate_required_after_receiving": True,
        "email_sending_performed": False,
        "secret_values_in_output": False,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
