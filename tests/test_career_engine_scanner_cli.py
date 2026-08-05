from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_engine.bundle import build_bundle
from career_engine.cli import (
    EXIT_OWNER_INPUT,
    EXIT_POLICY,
    EXIT_READY,
    EXIT_ROUTE,
    EXIT_WEAK_FIT,
    main,
)
from career_engine.core import decide_route, match_evidence, normalize_job, score_fit
from career_engine.generation import create_generation_packet, validate_generated_application
from career_engine.pipeline import prepare
from career_engine.scanner import run_scan, write_report
from career_engine.service import get_bundle_info, prepare_job, validate_application
from tests.test_career_engine_v1 import engine_root, valid_application  # noqa: F401


def job_dict() -> dict:
    return {
        "company": "Example Development Company",
        "role": "Senior Design Governance Manager",
        "location": "Riyadh, Saudi Arabia",
        "source": "test",
        "source_url": "https://example.com/jobs/123",
        "application_url": "https://example.com/jobs/123/apply",
        "live_status": "live",
        "live_verified_at": "2026-08-03T10:00:00+00:00",
        "live_verification_source": "official employer careers page",
        "full_job_description": """
Key Responsibilities
- Lead design governance and multidisciplinary design coordination across complex programmes.
- Manage senior client and stakeholder relationships and oversee project delivery.
- Drive value engineering, quality assurance and design controls.

Requirements
- Degree in architecture and strong Saudi project experience.
- Demonstrated team leadership and people management.
- Experience with design management and programme delivery.

Preferred
- Saudi Council of Engineers professional classification.
""",
    }


def test_scanner_uses_central_pipeline_and_bundle(engine_root: Path) -> None:
    jobs = {
        "jobs": [
            {
                "company": "Example Development Company",
                "role": "Senior Design Governance Manager",
                "location": "Riyadh, Saudi Arabia",
                "application_url": "https://example.com/apply",
                "live_status": "live",
                "live_verified_at": "2026-08-03T10:00:00+00:00",
                "live_verification_source": "official employer careers page",
                "full_job_description": job_dict()["full_job_description"],
            }
        ]
    }
    source = engine_root / "scan-input.json"
    source.write_text(json.dumps(jobs), encoding="utf-8")
    report = run_scan(source, root=engine_root, scanner_id="chatgpt_scanner")
    assert report["scanner_id"] == "chatgpt_scanner"
    assert report["actor"] == "chatgpt"
    assert report["bundle_hash"]
    assert report["send_or_submit"] is False
    assert len(report["results"]) == 1
    summary = report["results"][0]
    assert summary["job_id"]
    assert summary["fit_score"] > 0
    assert summary["generation_packet"]
    job_file = engine_root / "projects/job-automation/data/jobs" / f"{summary['job_id']}.json"
    assert job_file.is_file()
    record = json.loads(job_file.read_text())
    assert record["processing_state"]["bundle_hash"] == report["bundle_hash"]


def test_scanner_rejects_unknown_scanner_id(engine_root: Path) -> None:
    source = engine_root / "scan-input.json"
    source.write_text(json.dumps({"jobs": [dict(job_dict())]}), encoding="utf-8")
    with pytest.raises(ValueError):
        run_scan(source, root=engine_root, scanner_id="unknown_scanner")


def test_write_report_to_file(engine_root: Path) -> None:
    source = engine_root / "scan-input.json"
    source.write_text(json.dumps({"jobs": [dict(job_dict())]}), encoding="utf-8")
    report = run_scan(source, root=engine_root, scanner_id="hermes_scanner")
    output = engine_root / "scan-report.json"
    write_report(report, output)
    assert output.is_file()
    assert json.loads(output.read_text())["scanner_id"] == "hermes_scanner"


def test_weak_fit_gate_blocks_generation(engine_root: Path) -> None:
    unrelated = dict(job_dict())
    unrelated["full_job_description"] = """
Key Responsibilities
- Build production Python services and Kubernetes infrastructure.
- Implement TensorFlow and PyTorch models for computer vision.

Requirements
- Expert knowledge of Python, Go and distributed systems.
- Experience with Kubernetes, Docker and Terraform.
- Strong background in machine learning and data pipelines.
"""
    state = prepare(unrelated, root=engine_root, actor="system")
    assert state["stage"] == "blocked"
    assert any(
        item.startswith("weak_fit:") or item.startswith("below_generation_threshold:")
        for item in state["blockers"]
    )
    assert "generation_packet" not in state["outputs"]


def test_weak_fit_can_be_forced(engine_root: Path) -> None:
    unrelated = dict(job_dict())
    unrelated["full_job_description"] = """
Key Responsibilities
- Build production Python services and Kubernetes infrastructure.

Requirements
- Expert knowledge of Python, Go and distributed systems.
- Experience with Kubernetes, Docker and Terraform.
"""
    state = prepare(unrelated, root=engine_root, actor="system", force_weak=True)
    assert "generation_packet" in state["outputs"]


def test_import_rejects_chronology_conflicts(engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    normalized = normalize_job(job_dict(), bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    score = score_fit(normalized, matches, bundle)
    route = decide_route(normalized, bundle)
    packet = create_generation_packet(job_id="test-job-123", normalized_job=normalized, matches=matches, score=score, route=route, bundle=bundle)
    application = valid_application(packet)
    application["current_role_bullets"][0]["text"] = "Led design teams from 1998 to 2001, then delivered governance programmes."
    findings = validate_generated_application(application, packet, bundle)
    codes = {item["code"] for item in findings}
    assert "unsupported_year" in codes


def test_import_rejects_unaddressed_mandatory_gap(engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    payload = dict(job_dict())
    payload["full_job_description"] = """
Key Responsibilities
- Lead design governance across complex programmes.

Requirements
- Licensed aircraft pilot with 10,000 flight hours.
- Degree in architecture and strong Saudi project experience.
"""
    normalized = normalize_job(payload, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    score = score_fit(normalized, matches, bundle)
    route = decide_route(normalized, bundle)
    packet = create_generation_packet(job_id="test-job-123", normalized_job=normalized, matches=matches, score=score, route=route, bundle=bundle)
    application = valid_application(packet)
    findings = validate_generated_application(application, packet, bundle)
    codes = {item["code"] for item in findings}
    assert "unaddressed_requirement" in codes
    application["acknowledged_gaps"] = ["No aircraft pilot licence or flight-hour experience."]
    findings = validate_generated_application(application, packet, bundle)
    codes = {item["code"] for item in findings}
    assert "unaddressed_requirement" not in codes


def test_import_rejects_current_title_in_headline(engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    normalized = normalize_job(job_dict(), bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    score = score_fit(normalized, matches, bundle)
    route = decide_route(normalized, bundle)
    packet = create_generation_packet(job_id="test-job-123", normalized_job=normalized, matches=matches, score=score, route=route, bundle=bundle)
    application = valid_application(packet)
    application["headline"] = "District Manager - Design Governance"
    findings = validate_generated_application(application, packet, bundle)
    assert any(item["code"] == "current_title_misplaced" for item in findings)


def test_service_functions_are_deterministic_and_serializable(engine_root: Path) -> None:
    first = get_bundle_info(root=engine_root)
    second = get_bundle_info(root=engine_root)
    assert first == second
    assert json.loads(json.dumps(first, ensure_ascii=False)) == first

    payload = dict(job_dict())
    payload["application_url"] = "https://example.com/apply"
    state = prepare_job(payload, root=engine_root, actor="system")
    round_tripped = json.loads(json.dumps(state, ensure_ascii=False))
    assert round_tripped["job_id"] == state["job_id"]

    from career_engine.generation import create_generation_packet
    bundle = build_bundle(engine_root)
    normalized = normalize_job(payload, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    score = score_fit(normalized, matches, bundle)
    route = decide_route(normalized, bundle)
    packet = create_generation_packet(job_id="test-job-123", normalized_job=normalized, matches=matches, score=score, route=route, bundle=bundle)
    result = validate_application(valid_application(packet), packet, root=engine_root)
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    assert result["valid"] is True


def test_cli_score_route_render_package_exit_codes(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    jd = engine_root / "job.txt"
    jd.write_text(job_dict()["full_job_description"], encoding="utf-8")
    argv = [
        "prepare", "--jd-file", str(jd), "--company", "Example Development Company",
        "--role", "Senior Design Governance Manager", "--source", "test",
        "--application-url", "https://example.com/apply", "--actor", "system",
        "--live-status", "live", "--live-verified-at", "2026-08-03T10:00:00+00:00",
        "--live-verification-source", "official employer careers page",
    ]
    assert main(argv) == EXIT_READY

    # find the newest job id from the tracker data
    jobs_dir = engine_root / "projects/job-automation/data/jobs"
    job_files = sorted(jobs_dir.glob("*.json"))
    assert job_files
    job_id = job_files[-1].stem

    assert main(["score", "--job-id", job_id]) == EXIT_READY
    assert main(["route", "--job-id", job_id]) == EXIT_READY

    # generate import flow: export packet, write valid application, import
    assert main(["generate", "export", "--job-id", job_id]) == EXIT_READY
    bundle = build_bundle(engine_root)
    artifact_dir = engine_root / "projects/job-automation/artifacts" / job_id
    packet = json.loads((artifact_dir / "generation_packet.json").read_text())
    application = valid_application(packet)
    generated = artifact_dir / "generated.json"
    generated.write_text(json.dumps(application), encoding="utf-8")
    assert main(["generate", "import", "--job-id", job_id, "--file", str(generated), "--actor", "system"]) == EXIT_READY

    assert main(["validate", "--job-id", job_id]) == EXIT_READY
    # render returns system failure when libreoffice is missing, otherwise ready
    render_code = main(["render", "--job-id", job_id])
    assert render_code in (EXIT_READY, 70)
    assert main(["package", "--job-id", job_id]) in (EXIT_READY, EXIT_ROUTE)


def test_cli_scanner_ingest(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    source = engine_root / "scan.json"
    source.write_text(json.dumps({"jobs": [dict(job_dict())]}), encoding="utf-8")
    output = engine_root / "report.json"
    assert main(["scanner", "ingest", "--file", str(source), "--scanner-id", "chatgpt_scanner", "--output", str(output)]) == EXIT_READY
    assert output.is_file()
    assert json.loads(output.read_text())["scanner_id"] == "chatgpt_scanner"


def test_cli_render_missing_application_returns_owner_input(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    assert main(["render", "--job-id", "missing-job-0001"]) == EXIT_OWNER_INPUT


def test_cli_score_route_missing_job_returns_owner_input(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    assert main(["score", "--job-id", "missing-job-0001"]) == EXIT_OWNER_INPUT
    assert main(["route", "--job-id", "missing-job-0001"]) == EXIT_OWNER_INPUT


def test_cli_score_weak_fit_exit_code(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    jd = engine_root / "weak.txt"
    jd.write_text(
        """
Key Responsibilities
- Build production Python services and Kubernetes infrastructure.

Requirements
- Expert knowledge of Python, Go and distributed systems.
- Experience with Kubernetes, Docker and Terraform.
""",
        encoding="utf-8",
    )
    argv = [
        "prepare", "--jd-file", str(jd), "--company", "TechNova",
        "--role", "Machine Learning Engineer", "--source", "test",
        "--application-url", "https://example.com/apply", "--actor", "system",
        "--live-status", "live", "--live-verified-at", "2026-08-03T10:00:00+00:00",
        "--live-verification-source", "official employer careers page",
    ]
    assert main(argv) == EXIT_WEAK_FIT
    jobs_dir = engine_root / "projects/job-automation/data/jobs"
    job_id = sorted(jobs_dir.glob("*.json"))[-1].stem
    assert main(["score", "--job-id", job_id]) == EXIT_WEAK_FIT
    assert main(["route", "--job-id", job_id]) == EXIT_READY


def test_cli_package_unresolved_route_returns_route_exit(engine_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    jd = engine_root / "job.txt"
    payload = dict(job_dict())
    payload["application_url"] = ""
    payload["recipient"] = ""
    jd.write_text(payload["full_job_description"], encoding="utf-8")
    argv = [
        "prepare", "--jd-file", str(jd), "--company", "Example Development Company",
        "--role", "Senior Design Governance Manager", "--source", "test", "--actor", "system",
    ]
    code = main(argv)
    assert code in (EXIT_ROUTE, EXIT_READY)
    if code == EXIT_READY:
        return
    jobs_dir = engine_root / "projects/job-automation/data/jobs"
    job_id = sorted(jobs_dir.glob("*.json"))[-1].stem
    assert main(["package", "--job-id", job_id]) == EXIT_ROUTE
