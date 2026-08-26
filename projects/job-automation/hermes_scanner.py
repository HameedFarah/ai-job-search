#!/usr/bin/env python3
"""Hermes discovery wrapper for the central Career Engine."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from career_engine.bundle import load_bundle
from career_engine.config import load_config
from career_engine.pipeline import _load_tracker
from career_engine.scanner import run_scan, write_report
from daily_scanner import _build_review_bundle, _publish_review_bundle
from career_engine.targeting import reconcile_existing_non_target_jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Hermes Career Engine scanner")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    bundle = load_bundle(ROOT)
    _, paths = load_config(ROOT)
    target_lane_reconciliation = reconcile_existing_non_target_jobs(
        _load_tracker(paths), bundle.get("taxonomy", {}), actor="hermes"
    )
    try:
        scan_source_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        scan_source_sha = ""
    report = run_scan(Path(args.input), root=ROOT, scanner_id="hermes_scanner")
    report["scan_source_sha"] = scan_source_sha
    report["target_lane_reconciliation"] = target_lane_reconciliation
    # Keep the long-standing Hermes entry point on the same sanitized review
    # publication contract as daily_scanner.py.  This is derived evidence only;
    # CareerTracker remains authoritative and publication never sends/submits.
    report["review_bundle_publication"] = _publish_review_bundle(_build_review_bundle(report))
    print(write_report(report, Path(args.output) if args.output else None), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
