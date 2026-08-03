from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest

from career_engine.bundle import build_bundle
from career_engine.core import decide_route, normalize_job, validate_live_status
from career_engine.pipeline import prepare
from career_engine.scanner import run_scan
from tests.test_career_engine_v1 import engine_root, job_payload  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests/fixtures/career_engine/sanitized-jobs.json"


@lru_cache(maxsize=None)
def _load_fixture_jobs() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["jobs"]


def fixture_job(name: str) -> dict:
    """Deep-copied fictional vacancy fixture, so tests never share mutable state."""
    return deepcopy(_load_fixture_jobs()[name])


def live_control_job() -> dict:
    """Fictional live vacancy control: verified live with no mandatory domain wording."""
    payload = fixture_job("live_control")
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-03T10:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    return payload


def closed_example_job() -> dict:
    """Fictional closed vacancy example."""
    payload = fixture_job("closed_example")
    payload["live_status"] = "closed"
    return payload


def test_normalize_defaults_missing_status_to_unverified(job_payload: dict, engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    normalized = normalize_job(job_payload, bundle["taxonomy"])
    assert normalized["live_status"] == "unverified"
    assert normalized["live_verified_at"] == ""
    assert normalized["live_verification_source"] == ""


def test_normalize_accepts_live_metadata(job_payload: dict, engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    payload = dict(job_payload)
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-03T10:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    normalized = normalize_job(payload, bundle["taxonomy"])
    assert normalized["live_status"] == "live"
    assert normalized["live_verified_at"] == "2026-08-03T10:00:00+00:00"
    assert normalized["live_verification_source"] == "official employer careers page"


def test_normalize_rejects_invalid_live_status(job_payload: dict, engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    payload = dict(job_payload)
    payload["live_status"] = "expired"
    with pytest.raises(ValueError, match="Invalid live_status"):
        normalize_job(payload, bundle["taxonomy"])


def test_validate_live_status_requires_metadata_for_live(job_payload: dict, engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    payload = dict(job_payload)
    payload["live_status"] = "live"
    normalized = normalize_job(payload, bundle["taxonomy"])
    findings = validate_live_status(normalized)
    assert any(item["code"] == "invalid_live_metadata" for item in findings)
    payload["live_verified_at"] = "2026-08-03T10:00:00+00:00"
    normalized = normalize_job(payload, bundle["taxonomy"])
    findings = validate_live_status(normalized)
    assert any(item["code"] == "invalid_live_metadata" for item in findings)
    payload["live_verification_source"] = "official employer careers page"
    normalized = normalize_job(payload, bundle["taxonomy"])
    assert validate_live_status(normalized) == []


def test_validate_live_status_accepts_closed_and_unverified(job_payload: dict, engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    for status in ("closed", "unverified"):
        payload = dict(job_payload)
        payload["live_status"] = status
        normalized = normalize_job(payload, bundle["taxonomy"])
        assert validate_live_status(normalized) == []


def test_route_gates_non_live_jobs_as_unresolved(job_payload: dict, engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    for status, expected in (("closed", "closed"), ("unverified", "unverified")):
        payload = dict(job_payload)
        payload["live_status"] = status
        normalized = normalize_job(payload, bundle["taxonomy"])
        route = decide_route(normalized, bundle)
        assert route["route"] == "unresolved"
        assert f"live_status={expected}" in route["blocker"]


def test_route_gates_live_without_metadata_as_unresolved(job_payload: dict, engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    payload = dict(job_payload)
    payload["live_status"] = "live"
    normalized = normalize_job(payload, bundle["taxonomy"])
    route = decide_route(normalized, bundle)
    assert route["route"] == "unresolved"
    assert "verification metadata" in route["blocker"]


def test_live_control_receives_generation_packet(engine_root: Path) -> None:
    state = prepare(live_control_job(), root=engine_root, actor="system")
    assert state["stage"] == "generation_ready"
    assert state["live_status"] == "live"
    assert not state["blockers"]
    assert state["outputs"]["generation_packet"]
    assert (engine_root / "projects/job-automation/artifacts" / state["job_id"] / "generation_packet.json").is_file()


def test_closed_example_never_receives_generation_packet(engine_root: Path) -> None:
    state = prepare(closed_example_job(), root=engine_root, actor="system")
    assert state["stage"] == "blocked"
    assert any(item.startswith("not_live:closed") for item in state["blockers"])
    assert state["route"]["route"] == "unresolved"
    assert "generation_packet" not in state["outputs"]
    assert not (engine_root / "projects/job-automation/artifacts" / state["job_id"] / "generation_packet.json").exists()
    job_file = engine_root / "projects/job-automation/data/jobs" / f"{state['job_id']}.json"
    record = json.loads(job_file.read_text(encoding="utf-8"))
    assert record["processing_state"]["live_status"] == "closed"


def test_unverified_job_never_receives_generation_packet(job_payload: dict, engine_root: Path) -> None:
    state = prepare(dict(job_payload), root=engine_root, actor="system")
    assert state["stage"] == "blocked"
    assert any(item.startswith("not_live:unverified") for item in state["blockers"])
    assert state["route"]["route"] == "unresolved"
    assert "generation_packet" not in state["outputs"]


def test_live_job_missing_metadata_is_blocked(job_payload: dict, engine_root: Path) -> None:
    payload = dict(job_payload)
    payload["live_status"] = "live"
    state = prepare(payload, root=engine_root, actor="system")
    assert state["stage"] == "blocked"
    assert any(item.startswith("invalid_live_metadata:") for item in state["blockers"])
    assert "generation_packet" not in state["outputs"]


def test_scanner_counts_only_live_jobs_as_generation_candidates(engine_root: Path) -> None:
    source = engine_root / "mixed-scan-input.json"
    source.write_text(json.dumps({"jobs": [live_control_job(), closed_example_job()]}), encoding="utf-8")
    report = run_scan(source, root=engine_root, scanner_id="chatgpt_scanner")
    assert len(report["results"]) == 2
    live_summary = next(item for item in report["results"] if item["live_status"] == "live")
    closed_summary = next(item for item in report["results"] if item["live_status"] == "closed")
    assert live_summary["generation_packet"]
    assert not closed_summary["generation_packet"]
    assert [item["live_status"] for item in report["generation_candidates"]] == ["live"]
    assert any(item["live_status"] == "closed" for item in report["weak_or_blocked"])


@pytest.mark.parametrize("scanner_id", ["chatgpt_scanner", "hermes_scanner"])
def test_scanner_identity_cannot_bypass_unverified_gate(scanner_id: str, engine_root: Path) -> None:
    live = live_control_job()
    unverified = deepcopy(live)
    unverified["company"] = "Acme Unverified Control"
    unverified["reference"] = "UNVERIFIED-CONTROL"
    unverified["external_job_id"] = "acme-unverified-control"
    unverified["source_url"] = "https://example.com/unverified-control"
    unverified["application_url"] = "https://example.com/unverified-control/apply"
    unverified.pop("live_status", None)
    unverified.pop("live_verified_at", None)
    unverified.pop("live_verification_source", None)
    source = engine_root / f"{scanner_id}-live-unverified-control.json"
    source.write_text(json.dumps({"jobs": [live, unverified]}), encoding="utf-8")

    report = run_scan(source, root=engine_root, scanner_id=scanner_id)

    assert report["scanner_id"] == scanner_id
    assert len(report["results"]) == 2
    assert len(report["generation_candidates"]) == 1
    assert report["generation_candidates"][0]["live_status"] == "live"
    blocked = next(item for item in report["results"] if item["live_status"] == "unverified")
    assert blocked["job_id"] != report["generation_candidates"][0]["job_id"]
    assert blocked["generation_packet"] == ""
    assert "not_live:unverified" in blocked["blockers"]
