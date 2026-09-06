#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical portal-first REGA scanner."""

from runtime.rega_priority_scan import main


if __name__ == "__main__":
    raise SystemExit(main())
