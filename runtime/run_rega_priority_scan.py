#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical portal-first REGA scanner."""
from __future__ import annotations

from pathlib import Path
import os
import sys

from runtime.rega_priority_scan import (
    DEFAULT_ROOT,
    MASTER_SHEET,
    atomic_jsonl,
    load_jsonl,
    main,
    persist_no_contact,
    read_master,
    rclone_access_token,
    utc_now,
)


def _runtime_root(argv: list[str]) -> Path:
    for index, value in enumerate(argv):
        if value.startswith("--root="):
            return Path(value.split("=", 1)[1])
        if value == "--root" and index + 1 < len(argv):
            return Path(argv[index + 1])
    return Path(DEFAULT_ROOT)


def _persist_reserve_blocked_terminal(root: Path) -> int:
    """Record paid-lookup/no-validation stops in Master Tracker without retrying.

    The provider domain-contact call may already have consumed credit when the
    post-call balance guard decides that mailbox validation would cross the
    configured reserve. The scanner checkpoints that stop instead of repeating
    the paid lookup. This compatibility layer makes the corresponding live
    Master Tracker classification explicit so the company is never silently
    dropped or made sendable.
    """
    checkpoint_path = root / "checkpoint.jsonl"
    checkpoints = load_jsonl(checkpoint_path)
    pending_ids = {
        master_id
        for master_id, item in checkpoints.items()
        if str(item.get("result") or "") == "reserve_reached_before_validation"
    }
    if not pending_ids:
        return 0

    token = rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
    headers, master_rows = read_master(token)
    by_id = {str(row.get("Master_ID") or "").strip(): row for row in master_rows}
    changed = 0
    for master_id in sorted(pending_ids):
        item = checkpoints[master_id]
        row = by_id.get(master_id)
        if row is None:
            raise RuntimeError(f"reserve-blocked checkpoint has no {MASTER_SHEET} row: {master_id}")
        domain = str(item.get("domain") or "").strip()
        persist_no_contact(
            token,
            headers,
            row,
            domain,
            "paid domain-contact lookup completed, but the configured Outscraper reserve blocked mailbox validation; no email was queued",
        )
        item.update(
            state="no_route",
            result="reserve_reached_before_validation_persisted",
            master_tracker_persisted=True,
            at=utc_now(),
        )
        changed += 1
    if changed:
        atomic_jsonl(checkpoint_path, checkpoints)
    return changed


if __name__ == "__main__":
    exit_code = main()
    if exit_code == 0:
        _persist_reserve_blocked_terminal(_runtime_root(sys.argv[1:]))
    raise SystemExit(exit_code)
