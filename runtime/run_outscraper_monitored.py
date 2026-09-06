#!/usr/bin/env python3
"""Compatibility entrypoint for the current REGA-priority Outscraper workflow.

The prior implementation was frozen to the obsolete 1,236-row Send Queue and
757-company / 728-unresolved REGA snapshot. Keeping that implementation reachable
from an old timer/service could waste provider credit or fail closed as the live
Career Engine grows.

This compatibility entrypoint therefore delegates all live paid execution to
``runtime/run_rega_priority_scan.py``. The delegated workflow is dynamically
sourced from the current Master Tracker, spends Outscraper credit on REGA only,
uses free discovery before paid lookup, validates only one best mailbox per
company, preserves a provider balance reserve, and has no Gmail send path.

``mailbox_route_kind`` remains here for legacy read-only/preparation imports.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIRECT_LOCALS = {"hr", "career", "careers", "job", "jobs", "recruit", "recruitment", "talent", "hiring"}
GENERAL_LOCALS = {"info", "contact", "contactus", "hello", "admin", "office", "enquiry", "enquiries", "inquiry", "inquiries"}
EXCLUDED_LOCALS = {
    "support", "privacy", "legal", "abuse", "finance", "financial", "investor", "investors",
    "ir", "billing", "accounts", "accounting", "sales", "security", "webmaster",
}


def mailbox_route_kind(email: str) -> str:
    local = email.split("@", 1)[0].lower() if "@" in email else ""
    if local in EXCLUDED_LOCALS:
        return "excluded"
    if local in DIRECT_LOCALS:
        return "recruitment"
    if local in GENERAL_LOCALS:
        return "general"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Required for live provider/Sheet execution")
    # Legacy arguments are accepted so an older service/timer remains compatible.
    parser.add_argument("--monitor-dir", default="")
    parser.add_argument("--rega-input", default="")
    parser.add_argument("--rega-workers", type=int, default=4)
    parser.add_argument("--rega-delay", type=float, default=0.2)
    # Current useful controls.
    parser.add_argument("--reserve-usd", type=float, default=1.00)
    parser.add_argument("--max-companies", type=int, default=0)
    args, _unknown = parser.parse_known_args()

    if not args.apply:
        raise SystemExit("refusing live REGA priority scan without --apply")

    target = REPO_ROOT / "runtime" / "run_rega_priority_scan.py"
    cmd = [
        sys.executable,
        str(target),
        "--apply",
        "--reserve-usd",
        str(max(0.0, float(args.reserve_usd))),
    ]
    if int(args.max_companies) > 0:
        cmd += ["--max-companies", str(int(args.max_companies))]

    os.execv(sys.executable, cmd)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
