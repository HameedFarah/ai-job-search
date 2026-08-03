#!/usr/bin/env python3
"""Hermes discovery wrapper for the central Career Engine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from career_engine.scanner import run_scan, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Hermes Career Engine scanner")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    report = run_scan(Path(args.input), root=ROOT, scanner_id="hermes_scanner")
    print(write_report(report, Path(args.output) if args.output else None), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
