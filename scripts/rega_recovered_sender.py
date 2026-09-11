#!/usr/bin/env python3
"""Run the maintained central sender for this owner-authorized REGA cohort only."""
import json
import sys
from datetime import datetime, timezone
from rega_sender_admission import SOURCE, ROOT, runtime_modules, release_time_ok, check_package


def main():
    authorization = json.loads((ROOT / "sender-authorization.json").read_text())
    if authorization.get("id") != "REGA-OWNER-20260912" or authorization.get("source") != SOURCE:
        raise RuntimeError("missing_owner_authorization")
    if not release_time_ok(authorization, datetime.now(timezone.utc)):
        raise RuntimeError("outside_authorized_send_date")
    sender, reconcile, sheet = runtime_modules()
    check_package(sender, sheet, authorization, sheet.rclone_access_token())
    read_all = reconcile._read_queue_sheet
    def read_cohort(*args, **kwargs):
        return [row for row in read_all(*args, **kwargs) if row.get("Source") == SOURCE]
    # Scope only this process's queue reads. Reuse canonical Gmail checks, MIME,
    # ledger, singleton lock, cadence, window and transmission unchanged.
    reconcile._read_queue_sheet = read_cohort
    sender._read_queue_sheet = read_cohort
    sys.argv = [sys.argv[0], "--status", str(ROOT / "scheduled-sender-status.json"),
                "--ready-cache", str(ROOT / "scheduled-ready-queue.json")]
    return sender.main()


if __name__ == "__main__":
    raise SystemExit(main())
