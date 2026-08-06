from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from career_engine.bundle import build_bundle
from career_engine.cli import EXIT_OWNER_INPUT, EXIT_POLICY, EXIT_READY, EXIT_SYSTEM, main
from career_engine.config import load_config
from career_engine.pipeline import _load_tracker, prepare
from tests.test_career_engine_reconcile import seed_job
from tests.test_career_engine_v1 import engine_root, job_payload  # noqa: F401


@pytest.fixture()
def cli_root(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at the isolated engine root via CAREER_ENGINE_REPO_ROOT."""
    build_bundle(engine_root)
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    return engine_root


def test_validate_config_command(cli_root: Path) -> None:
    assert main(["validate-config"]) == EXIT_READY
    assert main(["validate-config", "--human"]) == EXIT_READY


def test_validate_config_fails_on_missing_bundle(tmp_path: Path) -> None:
    # engine_root without a built bundle must fail bundle-currency validation
    import shutil
    from tests.test_career_engine_v1 import engine_root as _engine_root  # noqa: F401

    root = tmp_path / "bare"
    config_dir = root / "projects/job-automation/config"
    config_dir.mkdir(parents=True)
    (root / "projects/job-automation").mkdir(parents=True, exist_ok=True)
    from career_engine.config import repo_root as repo_root_fn
    import tests.test_career_engine_v1 as t
    source_repo = t.REPO
    shutil.copy2(source_repo / "projects/job-automation/tracker.py", root / "projects/job-automation/tracker.py")
    for name in ("career-engine.v1.json", "requirements-taxonomy.v1.json", "generated_application.schema.json",
                 "runtime-bundle.schema.json", "evidence-index.v1.json", "ats-linear-template.v1.json",
                 "hermes-review-diff.schema.json"):
        shutil.copy2(source_repo / "projects/job-automation/config" / name, config_dir / name)
    os.environ["CAREER_ENGINE_REPO_ROOT"] = str(root)
    try:
        code = main(["validate-config"])
    finally:
        os.environ.pop("CAREER_ENGINE_REPO_ROOT", None)
    assert code == EXIT_SYSTEM


def test_list_jobs_filters(cli_root: Path) -> None:
    seed_job(cli_root, "9c85bd0d9661c2f34978", company="Gensler", role="Architect - Senior", score=51, status="generation_ready")
    seed_job(cli_root, "fd6675da1bb6de6f40a1", company="Parsons", role="Senior Project Manager (Design)", score=73, status="generation_ready")
    result = json.loads(capture_main(["list-jobs"]))
    assert result["count"] == 2
    result = json.loads(capture_main(["list-jobs", "--min-score", "70"]))
    assert result["count"] == 1
    assert result["jobs"][0]["company"] == "Parsons"
    result = json.loads(capture_main(["list-jobs", "--status", "generation_ready", "--company", "parsons"]))
    assert result["count"] == 1
    assert result["jobs"][0]["role"] == "Senior Project Manager (Design)"


def capture_main(argv: list[str]) -> str:
    import io
    import contextlib
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    assert code == EXIT_READY, buffer.getvalue()
    return buffer.getvalue()


def test_show_job_and_status_commands(cli_root: Path) -> None:
    seed_job(cli_root, "fd6675da1bb6de6f40a1", company="Parsons", role="Senior Project Manager (Design)", score=73, status="generation_ready")
    detail = json.loads(capture_main(["show-job", "--job-id", "fd6675da1bb6de6f40a1"]))
    assert detail["job_id"] == "fd6675da1bb6de6f40a1"
    assert detail["job"]["company"] == "Parsons"
    assert detail["scoring"]["total"] == 73
    assert detail["generation_packet"]["exists"] is True
    aggregate = json.loads(capture_main(["status"]))
    assert aggregate["aggregate"] is True
    assert aggregate["count"] >= 1
    per_job = json.loads(capture_main(["status", "--job-id", "fd6675da1bb6de6f40a1"]))
    assert "job" in per_job


def test_show_job_missing_returns_owner_input(cli_root: Path) -> None:
    assert main(["show-job", "--job-id", "0" * 20]) == EXIT_OWNER_INPUT


def test_dashboard_readonly_and_sync(cli_root: Path) -> None:
    seed_job(cli_root, "fd6675da1bb6de6f40a1", company="Parsons", role="Senior Project Manager (Design)", score=73, status="generation_ready")
    readonly = json.loads(capture_main(["dashboard"]))
    assert readonly["mode"] == "readonly"
    assert readonly["jobs"] >= 1
    assert readonly["publisher"]["deployed"] is False
    synced = json.loads(capture_main(["dashboard", "--sync"]))
    assert synced["mode"] == "synced"
    target = cli_root / "projects/job-automation/runtime/dashboard-data.json"
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["counts"]["generation_ready"] >= 1


def test_review_summary_command(cli_root: Path) -> None:
    review = json.loads(capture_main(["review"]))
    # No review diff exists in the fresh root: the read-only command reports it
    assert review["valid"] is False
    assert review["reason"] == "no review diff recorded yet"


def test_reconcile_command(cli_root: Path) -> None:
    seed_job(cli_root, "053bd41b6432bd0f9586", company="Bechtel Corporation", role="Planner", score=16, status="generation_ready")
    result = json.loads(capture_main(["reconcile"]))
    assert result["changed_count"] == 1
    assert "053bd41b6432bd0f9586" in result["changed_job_ids"]
    assert result["send_or_submit"] is False
    second = json.loads(capture_main(["reconcile"]))
    assert second["changed_count"] == 0


def test_run_command(cli_root: Path, job_payload: dict) -> None:
    payload = dict(job_payload)
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-06T00:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    payload["application_url"] = "https://example.com/jobs/123/apply"
    state = prepare(payload, root=cli_root, actor="hermes")
    _, paths = load_config(cli_root)
    tracker = _load_tracker(paths)
    tracker.update_job(
        state["job_id"],
        {"fit_score": 78, "priority": "high_priority",
         "scoring": {"total": 78, "raw_total": 78, "recommendation": "high_priority", "rationale": [], "gaps": []}},
        comment="Test fixture: promote job to high-priority eligible",
        actor="system",
    )
    result = json.loads(capture_main(["run"]))
    assert result["send_or_submit"] is False
    assert result["drafts_created"] == 0
    processed_ids = [item["job_id"] for item in result["processed"]]
    assert state["job_id"] in processed_ids
    assert result["report_path"]


def test_validate_aggregate_and_per_job(cli_root: Path) -> None:
    seed_job(cli_root, "fd6675da1bb6de6f40a1", company="Parsons", role="Senior Project Manager (Design)", score=73, status="generation_ready")
    aggregate = json.loads(capture_main(["validate"]))
    assert aggregate["valid"] is True
    assert aggregate["config"]["valid"] is True
    # Per-job validate with no generated application yet -> owner input (exit 10)
    assert main(["validate", "--job-id", "fd6675da1bb6de6f40a1"]) == EXIT_OWNER_INPUT


def test_validate_per_job_without_application_exits_owner_input(cli_root: Path) -> None:
    seed_job(cli_root, "fd6675da1bb6de6f40a1", company="Parsons", role="Senior Project Manager (Design)", score=73, status="generation_ready")
    assert main(["validate", "--job-id", "fd6675da1bb6de6f40a1"]) == EXIT_OWNER_INPUT


def test_record_review_default_latest(cli_root: Path) -> None:
    # No review diff yet -> default source missing -> policy exit
    assert main(["record-review"]) == EXIT_POLICY
    # Write a valid review diff, then record-review with no --file must use latest.json
    from career_engine.review import record_review_diff
    review_id = "chatgpt-review-20260806-ops-test"
    payload = {
        "schema_version": 1,
        "review_id": review_id,
        "reviewed_at": "2026-08-06T12:00:00+00:00",
        "hermes_run_id": "career-engine-ops-2026-08-06",
        "hermes_report": "projects/job-automation/runtime/run-report-2026-08-06.json",
        "reviewer": "chatgpt",
        "verdict": "corrected",
        "job_diffs": [
            {
                "job_id": "fd6675da1bb6de6f40a1",
                "verdict": "accepted",
                "differences": [
                    {
                        "area": "workflow",
                        "before": {"threshold": 80},
                        "after": {"threshold": 70},
                        "reason": "Centralized threshold is 70/100.",
                        "evidence_refs": ["projects/job-automation/config/career-engine.v1.json"],
                        "reusable_rule": "Use the centralized 70/100 generation threshold."
                    }
                ]
            }
        ],
        "improvement_rules": ["Apply the centralized 70/100 generation threshold."],
        "notes": [],
        "send_or_submit": False,
    }
    result = record_review_diff(payload, root=cli_root)
    assert result["valid"] is True
    assert main(["record-review"]) == EXIT_READY
    latest = cli_root / "projects/job-automation/runtime/review-diffs/latest.json"
    assert json.loads(latest.read_text(encoding="utf-8"))["review_id"] == review_id
    # Re-recording the same review is idempotent (no duplicate events)
    events_before = (cli_root / "projects/job-automation/logs/events.jsonl").read_text(encoding="utf-8")
    again = json.loads(capture_main(["record-review"]))
    assert again["already_recorded"] is True
    assert again["job_events"] == 0
    events_after = (cli_root / "projects/job-automation/logs/events.jsonl").read_text(encoding="utf-8")
    assert events_after == events_before


def test_scan_alias(cli_root: Path, job_payload: dict) -> None:
    source = cli_root / "scan-input.json"
    source.write_text(json.dumps({"jobs": [dict(job_payload, source="chatgpt")]}), encoding="utf-8")
    output = cli_root / "scan-report.json"
    report = json.loads(capture_main(["scan", "--file", str(source), "--scanner-id", "chatgpt_scanner", "--output", str(output)]))
    assert report["scanner_id"] == "chatgpt_scanner"
    assert report["send_or_submit"] is False
    assert output.is_file()


def test_bundle_rebuild_alias(cli_root: Path) -> None:
    assert main(["bundle", "rebuild"]) == EXIT_READY
    result = json.loads(capture_main(["bundle", "status"]))
    assert result["current"] is True
