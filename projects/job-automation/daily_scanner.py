#!/usr/bin/env python3
"""Compatibility entry point for the centralized Career Engine scanners.

Use `--scanner-id hermes_scanner` for Hermes and `--scanner-id chatgpt_scanner`
for the ChatGPT daily scan. All facts, scoring, generation and validation remain in
the central `career_engine` package.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from career_engine.scanner import SCANNER_ACTORS, run_scan, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process discovered jobs through the central Career Engine")
    parser.add_argument("--input", required=True, help="JSON file produced by discovery connectors")
    parser.add_argument("--output", default="", help="Optional structured scan report path")
    parser.add_argument("--scanner-id", choices=tuple(SCANNER_ACTORS), default="hermes_scanner")
    args = parser.parse_args(argv)
    report = run_scan(Path(args.input), root=REPO_ROOT, scanner_id=args.scanner_id)
    print(write_report(report, Path(args.output) if args.output else None), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
