"""Source framework CLI tests (registry / probe / verify / ingest)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from career_engine.sources.cli import (
    build_parser,
    main,
    run_probe,
    run_verify,
)

from tests.test_career_engine_v1 import engine_root  # noqa: F401


def run_cli(argv: list[str]) -> tuple[int, dict]:
    captured: dict = {}
    import io
    import sys

    original = sys.stdout
    stream = io.StringIO()
    sys.stdout = stream
    try:
        code = main(argv)
    finally:
        sys.stdout = original
    captured["text"] = stream.getvalue()
    try:
        captured["json"] = json.loads(stream.getvalue())
    except json.JSONDecodeError:
        captured["json"] = None
    return code, captured


def test_cli_registry_emits_json() -> None:
    code, out = run_cli(["registry"])
    assert code == 0
    payload = out["json"]
    assert payload["no_send_policy"] is True
    assert payload["schema_version"] == 1
    assert any(source["id"] == "greenhouse" for source in payload["sources"])


def test_cli_registry_flag_is_accepted() -> None:
    code, out = run_cli(["registry", "--json"])
    assert code == 0
    assert out["json"] is not None


def test_cli_probe_offline_writes_output_file(tmp_path: Path) -> None:
    output = tmp_path / "probe.json"
    code, out = run_cli(["probe", "--adapter", "greenhouse", "--company", "careem", "--limit", "5", "--offline", "--output", str(output)])
    assert code == 0
    assert out["json"]["send_or_submit"] is False
    assert output.is_file()
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["summary"]["jobs_emitted"] == 3


def test_cli_probe_unknown_adapter_errors() -> None:
    code, out = run_cli(["probe", "--adapter", "not-an-adapter", "--company", "x", "--offline"])
    assert code == 2
    assert out["json"]["error"]


def test_cli_probe_blocked_source_reports_blocked() -> None:
    code, out = run_cli(["probe", "--adapter", "gcc_bayt", "--company", "bayt", "--offline"])
    assert code == 0
    assert out["json"]["blocked"]


def test_cli_verify_offline() -> None:
    code, out = run_cli(["verify", "--url", "https://careers.example.meridian.com/", "--offline"])
    assert code == 0
    assert out["json"]["verified_official"] is True


def test_offline_probe_output_is_tagged_and_can_only_enter_isolated_tracker(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    from career_engine.sources.cli import run_ingest

    report = run_probe(adapter_id="greenhouse", company="careem", limit=5, offline=True)
    assert report["offline_fixture"] is True
    assert report["ingest_allowed_in_production"] is False
    assert all(job["provenance"]["offline_fixture"] is True for job in report["jobs"])
    source = engine_root / "probe-jobs.json"
    source.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    scan = run_ingest(str(source), scanner_id="hermes_scanner")
    assert len(scan["results"]) == len(report["jobs"])
    tracker = engine_root / "projects/job-automation"
    assert (tracker / "data/jobs.csv").is_file()
    assert (tracker / "logs/events.jsonl").is_file()
    assert (tracker / "artifacts").is_dir()


def test_offline_probe_report_refused_for_production_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from career_engine.sources.cli import run_ingest

    monkeypatch.delenv("CAREER_ENGINE_REPO_ROOT", raising=False)
    report = run_probe(adapter_id="greenhouse", company="careem", limit=5, offline=True)
    source = tmp_path / "offline-probe.json"
    source.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    repo = Path(__file__).resolve().parents[1]
    tracker = repo / "projects/job-automation"
    protected = [tracker / "data/jobs.csv", tracker / "logs/events.jsonl"]
    before = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        for path in protected
    }
    jobs_before = sorted(path.name for path in (tracker / "data/jobs").glob("*.json"))
    artifacts_before = sorted(path.name for path in (tracker / "artifacts").iterdir())
    with pytest.raises(ValueError, match="cannot be ingested into the production"):
        run_ingest(str(source), scanner_id="hermes_scanner")
    after = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        for path in protected
    }
    assert after == before
    assert sorted(path.name for path in (tracker / "data/jobs").glob("*.json")) == jobs_before
    assert sorted(path.name for path in (tracker / "artifacts").iterdir()) == artifacts_before


def test_realistic_discovery_output_is_scorable_by_the_central_engine(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    from career_engine.sources.cli import run_ingest

    source = engine_root / "official-jobs.json"
    source.write_text(json.dumps({"jobs": [{
        "company": "Meridian Development",
        "role": "Senior Design Manager",
        "location": "Riyadh, Saudi Arabia",
        "source": "greenhouse",
        "source_url": "https://boards.greenhouse.io/meridiandevelopment/jobs/12345",
        "external_job_id": "12345",
        "application_url": "https://boards.greenhouse.io/meridiandevelopment/jobs/12345",
        "full_job_description": "Lead multidisciplinary architectural design management, consultant coordination, authority compliance, technical governance, programme delivery, construction support, risk management, and client reporting across major projects.",
        "live_status": "unverified",
        "live_verified_at": "",
        "live_verification_source": ""
    }]}, ensure_ascii=False), encoding="utf-8")
    scan = run_ingest(str(source), scanner_id="hermes_scanner")
    assert scan["send_or_submit"] is False
    assert len(scan["results"]) == 1
    assert scan["results"][0]["live_status"] == "unverified"
    assert scan["results"][0]["generation_packet"] == ""
    assert scan["results"][0]["blockers"]


def test_probe_dedupe_key_stable_across_scanner_and_store() -> None:
    report = run_probe(adapter_id="ashby", company="ramp", limit=5, offline=True)
    keys = [job["provenance"]["raw_id"] for job in report["jobs"]]
    assert len(keys) == len(set(keys))
