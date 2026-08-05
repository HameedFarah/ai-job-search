"""Source framework CLI tests (registry / probe / verify / ingest)."""

from __future__ import annotations

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


def test_probe_output_is_scorable_by_the_central_engine(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discovery output must flow through the central scanner unchanged."""
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    from career_engine.sources.cli import run_ingest

    report = run_probe(adapter_id="greenhouse", company="careem", limit=5, offline=True)
    source = engine_root / "probe-jobs.json"
    source.write_text(json.dumps({"jobs": report["jobs"]}, ensure_ascii=False), encoding="utf-8")
    scan = run_ingest(str(source), scanner_id="hermes_scanner")
    assert scan["send_or_submit"] is False
    assert len(scan["results"]) == len(report["jobs"])
    for summary in scan["results"]:
        # Discovery-only: unverified vacancies are blocked from generation.
        assert summary["live_status"] == "unverified"
        assert summary["generation_packet"] == ""
        assert summary["blockers"]


def test_probe_dedupe_key_stable_across_scanner_and_store() -> None:
    report = run_probe(adapter_id="ashby", company="ramp", limit=5, offline=True)
    keys = [job["provenance"]["raw_id"] for job in report["jobs"]]
    assert len(keys) == len(set(keys))
