from __future__ import annotations

import json
from pathlib import Path

from career_engine.scanner import add_path_scan_statistics, run_scan
from tests.test_career_engine_scanner_cli import job_dict
from tests.test_career_engine_v1 import engine_root  # noqa: F401


def test_scanner_reports_new_existing_and_path_statistics(engine_root: Path) -> None:
    first = dict(job_dict())
    first["source_path"] = "gmail/job-alerts"
    source = engine_root / "scan-stats.json"
    source.write_text(
        json.dumps(
            {
                "jobs": [first],
                "paths": [
                    {"source_name": "gmail/job-alerts", "attempted": True, "status": "ok", "jobs_fetched": 1},
                    {"source_name": "official/parsons", "attempted": True, "status": "empty", "jobs_fetched": 0},
                    {"source_name": "official/example-failed", "attempted": True, "status": "unavailable", "jobs_fetched": 0, "error": "dns"},
                ],
            }
        ),
        encoding="utf-8",
    )

    first_report = run_scan(source, root=engine_root, scanner_id="hermes_scanner")
    stats = first_report["statistics"]
    assert stats["jobs_discovered"] == 1
    assert stats["new_jobs"] == 1
    assert stats["existing_jobs"] == 0
    assert stats["paths_total"] == 3
    assert stats["paths_scanned"] == 3
    assert stats["paths_failed"] == 1
    assert stats["by_path"]["official/parsons"]["jobs_discovered"] == 0
    assert stats["by_path"]["official/example-failed"]["error"] == "dns"
    assert first_report["results"][0]["source_path"] == "gmail/job-alerts"
    assert first_report["results"][0]["is_new"] is True

    second_report = run_scan(source, root=engine_root, scanner_id="hermes_scanner")
    assert second_report["statistics"]["new_jobs"] == 0
    assert second_report["statistics"]["existing_jobs"] == 1
    assert second_report["results"][0]["is_new"] is False


def test_path_statistics_can_merge_zero_result_sources(engine_root: Path) -> None:
    source = engine_root / "scan-empty.json"
    source.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    report = run_scan(source, root=engine_root, scanner_id="chatgpt_scanner")
    add_path_scan_statistics(
        report,
        [
            {"source_name": "consultant-a", "attempted": True, "status": "empty", "jobs_fetched": 0},
            {"source_name": "consultant-b", "attempted": False, "status": "skipped", "jobs_fetched": 0},
        ],
    )
    stats = report["statistics"]
    assert stats["paths_total"] == 2
    assert stats["paths_scanned"] == 1
    assert stats["by_path"]["consultant-a"]["jobs_discovered"] == 0
