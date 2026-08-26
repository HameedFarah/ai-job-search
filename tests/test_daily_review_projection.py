from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


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


def test_publication_rejects_missing_scan_source_sha() -> None:
    module = _module()
    result = module._publish_review_bundle({"scan": {"scan_source_sha": ""}})
    assert result["status"] == "failed"
    assert result["error_type"] == "scan_source_sha_missing"


def test_projection_keeps_scan_and_current_source_sha_distinct(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "load_config", lambda root: ({"scoring": {"thresholds": {"high_priority": 70}}, "daily_scanner": {}}, SimpleNamespace(tracker_base=root)))
    monkeypatch.setattr(module, "load_bundle", lambda root: {"bundle_hash": "bundle-final"})
    monkeypatch.setattr(module, "_load_tracker", lambda paths: type("Tracker", (), {"list_rows": lambda self: []})())
    monkeypatch.setattr(module, "_git_value", lambda *args, **kwargs: "current-sha")
    report = {
        "scanner_id": "hermes_scanner",
        "scanned_at": "2026-08-26T06:00:00+00:00",
        "scan_source_sha": "scan-sha",
        "statistics": {},
        "results": [],
        "final_run": {"process_all": True, "processed": [{"job_id": "x"}], "dashboard": {"jobs": 1, "counts": {"ready": 1}}},
    }
    bundle = module._build_review_bundle(report)
    assert bundle["scan"]["scan_source_sha"] == "scan-sha"
    assert bundle["scan"]["current_source_sha"] == "current-sha"
    assert bundle["final_run"]["dashboard"]["jobs"] == 1
    assert bundle["privacy"]["contains_urls"] is False
