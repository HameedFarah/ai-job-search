#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from career_engine.gmail_outcomes import reconcile_outcome_mail


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile Gmail application outcomes into canonical CareerTracker")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--start", type=_parse_date)
    parser.add_argument("--end", dest="end_inclusive", type=_parse_date)
    parser.add_argument("--max-results", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = reconcile_outcome_mail(
        args.root,
        start=args.start,
        end_inclusive=args.end_inclusive,
        max_results=args.max_results,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
