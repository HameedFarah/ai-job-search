"""Direct, fail-closed Outscraper email-validation command.

This command is intentionally small and provider-specific. The canonical runtime
injects OUTSCRAPER_API_KEY from Infisical; this module never reads Infisical
itself and never prints the key. It validates existing addresses only and does
not send email or promote company identity by itself.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter
from pathlib import Path

from .outscraper_validation import MAX_BATCH_SIZE, validate_emails
from .provider_clients import OutscraperClient, ProviderBudget


def _read_emails(path: Path, column: str) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() in {".txt", ".list"}:
        return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or column not in reader.fieldnames:
            raise ValueError(f"missing email column: {column}")
        return [str(row.get(column) or "").strip() for row in reader if str(row.get(column) or "").strip()]


def _safe_record(item: dict) -> dict:
    metadata = dict(item.get("metadata") or {})
    return {
        "provider": "outscraper",
        "email": metadata.get("email", ""),
        "verification": metadata.get("verification") or item.get("status", ""),
        "status": item.get("status", ""),
        "safe_to_send": bool(metadata.get("safe_to_send", False)),
        "status_details": metadata.get("status_details", ""),
        "source_url": item.get("source_url", ""),
        "retrieved_at": item.get("retrieved_at", ""),
        "cost_status": item.get("cost_status", ""),
    }


def run(
    input_path: Path,
    output_path: Path,
    *,
    column: str = "Email",
    batch_size: int = MAX_BATCH_SIZE,
    allow_existing_credit: bool = False,
) -> dict:
    emails = _read_emails(input_path, column)
    unique = list(dict.fromkeys(email.strip().lower() for email in emails if email.strip()))
    batch_size = max(1, min(int(batch_size), MAX_BATCH_SIZE))
    key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
    client = OutscraperClient(key)
    calls = max(1, math.ceil(len(unique) / batch_size)) if unique else 1
    budget = ProviderBudget(
        allow_existing_credit=allow_existing_credit,
        max_calls=calls,
        max_credits=len(unique),
        max_domains=0,
    )
    results = validate_emails(client, unique, budget, batch_size=batch_size)
    safe_results = [_safe_record(item) for item in results]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in safe_results:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(output_path)

    counts = Counter(str(record.get("verification") or record.get("status") or "UNKNOWN").upper() for record in safe_results)
    provider_failures = sum(
        1
        for record in safe_results
        if str(record.get("status") or "").lower()
        in {"missing_credential", "budget_exhausted", "auth_failed", "quota_required", "network_failed", "failed"}
    )
    return {
        "valid": True,
        "input": str(input_path),
        "output": str(output_path),
        "input_rows": len(emails),
        "unique_emails": len(unique),
        "result_rows": len(safe_results),
        "receiving": counts.get("RECEIVING", 0),
        "invalid": counts.get("INVALID", 0),
        "blacklisted": counts.get("BLACKLISTED", 0),
        "unknown": counts.get("UNKNOWN", 0),
        "provider_failed_or_gated": provider_failures,
        "safe_to_send": sum(1 for record in safe_results if record.get("safe_to_send")),
        "allow_existing_credit": allow_existing_credit,
        "batch_size": batch_size,
        "purchase_or_topup_performed": False,
        "secret_values_emitted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an email CSV/list with Outscraper; fail closed")
    parser.add_argument("--input", required=True, help="CSV or newline-delimited email file")
    parser.add_argument("--output", required=True, help="Sanitized JSONL validation output")
    parser.add_argument("--column", default="Email", help="CSV email column name; default Email")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=MAX_BATCH_SIZE,
        help=f"1-{MAX_BATCH_SIZE}; default {MAX_BATCH_SIZE}",
    )
    parser.add_argument(
        "--allow-existing-credit",
        action="store_true",
        help="Permit existing/trial/prepaid Outscraper credit. Never purchases or tops up.",
    )
    args = parser.parse_args()
    summary = run(
        Path(args.input),
        Path(args.output),
        column=args.column,
        batch_size=args.batch_size,
        allow_existing_credit=args.allow_existing_credit,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
