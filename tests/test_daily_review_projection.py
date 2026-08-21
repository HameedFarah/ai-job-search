from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    root = Path(__file__).resolve().parents[1]
    path = root / "projects/job-automation/daily_scanner.py"
    spec = importlib.util.spec_from_file_location("career_daily_scanner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review_projection_score_distribution_and_privacy_summary() -> None:
    module = _module()
    results = [
        {"fit_score": None},
        {"fit_score": 49},
        {"fit_score": 50},
        {"fit_score": 64},
        {"fit_score": 65},
        {"fit_score": 69},
        {"fit_score": 70},
        {"fit_score": 97},
    ]
    assert module._score_distribution(results, 70) == {
        "unscored": 1,
        "below_50": 1,
        "selective_50_64": 2,
        "credible_65_69": 2,
        "eligible_70_plus": 2,
    }
    # Only numeric/bool evidence and an error-presence flag may escape this
    # helper; strings such as Gmail subjects, URLs or message IDs do not.
    safe = module._safe_numeric_summary(
        {
            "messages_scanned": 22,
            "reconciled": 4,
            "send_or_submit": False,
            "subject": "private subject",
            "message_id": "private-id",
            "error": "private transport detail",
        }
    )
    assert safe == {
        "messages_scanned": 22,
        "reconciled": 4,
        "send_or_submit": False,
        "error_present": True,
    }


def test_review_projection_uses_riyadh_operation_date() -> None:
    module = _module()
    # 21:30 UTC is already the next day in Riyadh (+03:00).
    assert module._review_date("2026-08-20T21:30:00+00:00") == "2026-08-21"
