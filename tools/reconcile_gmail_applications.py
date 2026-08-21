#!/usr/bin/env python3
"""Cron-context Gmail submission reconciliation helper.

Invoked by ``~/.hermes/scripts/career-engine-daily-context.py`` as::

    python3 tools/reconcile_gmail_applications.py \
        --repo /home/hameedo/projects/ai-job-search \
        --days 45 --limit 200 --backend auto

The script must be crash-proof and always emit valid JSON to stdout so
the cron context can include its counts.  ``auto`` prefers the
repository gws integration; if gws is unavailable a bounded himalaya
read-only path would be accepted, but gws is verified live as
``hameedo@gmail.com`` so that fallback is not exercised.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gmail submission reconciliation for cron context")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="Repository root")
    parser.add_argument("--days", type=int, default=45, help="Window when no incremental state exists")
    parser.add_argument("--limit", type=int, default=200, help="Max Gmail messages to scan")
    parser.add_argument("--backend", choices=("auto", "gws", "himalaya"), default="auto")
    args = parser.parse_args(argv)

    root = Path(args.repo)
    today = date.today()

    # ``auto`` is canonical: repository gws integration first.
    # himalaya read-only fallback is accepted per spec but not needed while
    # gws is live; requesting himalaya explicitly surfaces as a failure so
    # the context reports it visibly rather than silently switching.
    if args.backend == "himalaya":
        payload = {
            "mail_backend": "himalaya",
            "status": "failed",
            "error": "himalaya fallback not configured; gws is verified live for hameedo@gmail.com",
            "applied_jobs": 0,
            "matches": 0,
            "ambiguous": 0,
            "unmatched_candidates": 0,
            "already_recorded": 0,
            "backend_failures": ["himalaya fallback not configured"],
            "send_or_submit": False,
        }
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    try:
        from career_engine.gmail_reconcile import reconcile_submission_mail

        report = reconcile_submission_mail(root, max_results=args.limit)
        # Map to the shape the cron context script expects.
        reconciled = report.get("reconciled", []) if isinstance(report.get("reconciled"), list) else []
        unmatched = report.get("unmatched", []) if isinstance(report.get("unmatched"), list) else []
        classified = int(report.get("submission_messages_classified", 0) or 0)
        ambiguous = int(report.get("ambiguous_manual_review", 0) or 0)
        states_changed = int(report.get("application_states_changed", 0) or sum(1 for r in reconciled if r.get("changed")))
        already_recorded = len(reconciled) - states_changed if reconciled else 0
        payload = {
            "mail_backend": "gws",
            "status": "ok" if not report.get("error") else "failed",
            "error": report.get("error") or None,
            "applied_jobs": len(reconciled),
            "matches": classified,
            "ambiguous": ambiguous,
            "unmatched_candidates": len(unmatched),
            "already_recorded": already_recorded,
            "backend_failures": [],
            "messages_scanned": int(report.get("messages_scanned", 0) or 0),
            "submission_messages_classified": classified,
            "application_states_changed": states_changed,
            "detail": report,
            "send_or_submit": False,
        }
    except Exception as exc:  # noqa: BLE001 - surfaced
        payload = {
            "mail_backend": "gws",
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "applied_jobs": 0,
            "matches": 0,
            "ambiguous": 0,
            "unmatched_candidates": 0,
            "already_recorded": 0,
            "backend_failures": [f"{type(exc).__name__}: {str(exc)[:500]}"],
            "send_or_submit": False,
        }
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
