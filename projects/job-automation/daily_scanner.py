#!/usr/bin/env python3
"""Compatibility entry point for the centralized Career Engine scanners.

Use `--scanner-id hermes_scanner` for Hermes and `--scanner-id chatgpt_scanner`
for the ChatGPT daily scan. All facts, scoring, generation and validation remain in
the central `career_engine` package.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from career_engine.bundle import load_bundle
from career_engine.config import load_config
from career_engine.pipeline import _load_tracker
from career_engine.scanner import SCANNER_ACTORS, add_path_scan_statistics, run_scan, write_report
from career_engine.targeting import reconcile_existing_non_target_jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process discovered jobs through the central Career Engine")
    parser.add_argument("--input", required=True, help="JSON file produced by discovery connectors")
    parser.add_argument("--output", default="", help="Optional structured scan report path")
    parser.add_argument("--scanner-id", choices=tuple(SCANNER_ACTORS), default="hermes_scanner")
    parser.add_argument("--consultants", action="store_true", help="Include active consultant bookmarks via official JSON-LD probes")
    args = parser.parse_args(argv)
    source_path = Path(args.input)
    consultant_report = None
    if args.consultants:
        from career_engine.sources.consultants import scan_consultants
        consultant_report = scan_consultants(root=REPO_ROOT)
        base = json.loads(source_path.read_text(encoding="utf-8"))
        jobs = base if isinstance(base, list) else base.get("jobs", [])
        scan_paths = [] if isinstance(base, list) else base.get("paths", base.get("scan_paths", []))
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump({"jobs": jobs + consultant_report["jobs"], "paths": scan_paths}, handle, ensure_ascii=False)
            source_path = Path(handle.name)

    bundle = load_bundle(REPO_ROOT)
    _, paths = load_config(REPO_ROOT)
    target_lane_reconciliation = reconcile_existing_non_target_jobs(
        _load_tracker(paths),
        bundle.get("taxonomy", {}),
        actor=SCANNER_ACTORS[args.scanner_id],
    )
    report = run_scan(source_path, root=REPO_ROOT, scanner_id=args.scanner_id)
    report["target_lane_reconciliation"] = target_lane_reconciliation
    if consultant_report is not None:
        report["consultant_sources"] = consultant_report["sources"]
        report["consultant_summary"] = consultant_report["summary"]
        add_path_scan_statistics(report, consultant_report["sources"])
    print(write_report(report, Path(args.output) if args.output else None), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
