from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_engine.bundle import build_bundle
from career_engine.config import load_config
from career_engine.ops import (
    FADEN_JOB_ID,
    LUXOFT_JOB_ID,
    NAMED_GENERATION_READY_ROLES,
    PARSONS_SDM_APPROVED_JOB_ID,
    ZAWAYA_APPLIED_JOB_ID,
    ZAWAYA_DUPLICATE_JOB_ID,
    reconcile,
)
from career_engine.pipeline import _load_tracker, prepare
from tests.test_career_engine_v1 import engine_root, job_payload  # noqa: F401


def _recommendation(score: int) -> str:
    if score >= 70:
        return "high_priority"
    if score >= 65:
        return "credible"
    if score >= 50:
        return "selective"
    return "weak"


def seed_job(
    engine_root: Path,
    job_id: str,
    *,
    company: str,
    role: str,
    score: int,
    status: str,
    live_status: str = "live",
    route: str = "portal",
    application_url: str = "https://example.org/jobs/123",
    application_status: str = "not_submitted",
) -> None:
    from career_engine.config import load_config
    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    tracker.ensure_layout()
    now = "2026-08-06T00:00:00+00:00"
    job = {
        "job_id": job_id,
        "source": "test",
        "external_job_id": f"EXT-{job_id[:10]}",
        "source_url": "https://example.org/source",
        "company": company,
        "role": role,
        "location": "Riyadh, Saudi Arabia",
        "posting_date": "",
        "closing_date": "",
        "jd_hash": "a" * 64,
        "full_jd_path": f"projects/job-automation/data/jobs/{job_id}.json",
        "first_seen": now,
        "last_seen": now,
        "ingested_by": "test",
        "fit_score": str(score),
        "priority": _recommendation(score),
        "owner": "owner",
        "processing_status": status,
        "resume_status": "not_started",
        "cover_letter_status": "not_started",
        "pdf_status": "not_started",
        "gmail_draft_status": "not_started",
        "application_status": application_status,
        "outcome": "",
        "last_updated": now,
        "next_action": "test",
        "notes": "",
    }
    record = {
        "job": job,
        "full_job_description": (
            "Senior architecture and design management role. Key responsibilities include leading "
            "multidisciplinary design coordination, managing senior client relationships, driving value "
            "engineering and quality assurance, and overseeing project delivery across complex programmes."
        ),
        "normalized_requirements": [],
        "provenance": {"source": "test", "source_url": "https://example.org/source"},
        "scoring": {"total": score, "raw_total": score, "recommendation": _recommendation(score), "rationale": [], "gaps": []},
        "evidence_matches": [],
        "processing_state": {
            "owner": "owner",
            "status": status,
            "bundle_hash": "test-bundle",
            "live_status": live_status,
            "route": {
                "route": route,
                "application_url": application_url,
                "recipient": "" if route == "portal" else "jobs@example.org",
                "recipient_source": "",
                "blocker": "",
            },
            "blockers": [],
            "warnings": [],
        },
        "generated_artifacts": [],
        "gmail_draft_reference": None,
        "history": [],
    }
    tracker._save_job_and_row(record)
    if status == "generation_ready":
        artifact_dir = tracker.artifacts_dir / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "generation_packet.json").write_text(
            json.dumps({"job_id": job_id, "bundle_hash": "test-bundle"}), encoding="utf-8"
        )
        (artifact_dir / "pipeline_state.json").write_text(
            json.dumps({"input_hash": "x", "data": {"stage": "generation_ready", "job_id": job_id}}),
            encoding="utf-8",
        )


def job_status(engine_root: Path, job_id: str) -> tuple[str, dict]:
    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    record = tracker.get_job(job_id)
    return record["job"]["processing_status"], record.get("processing_state") or {}


def set_current_blockers(
    engine_root: Path,
    job_id: str,
    blockers: list[str],
    *,
    pipeline_blockers: list[str] | None = None,
) -> None:
    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    record = tracker.get_job(job_id)
    state = dict(record.get("processing_state") or {})
    state["blockers"] = blockers
    tracker.update_job(
        job_id,
        {"processing_state": state},
        comment="Test fixture: set current blockers",
        actor="system",
    )
    if pipeline_blockers is not None:
        artifact_dir = tracker.artifacts_dir / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "pipeline_state.json").write_text(
            json.dumps({"input_hash": "x", "data": {"stage": record["job"]["processing_status"], "job_id": job_id, "blockers": pipeline_blockers}}),
            encoding="utf-8",
        )


def test_below_threshold_generation_ready_is_reconciled(engine_root: Path) -> None:
    seed_job(engine_root, "9c85bd0d9661c2f34978", company="Gensler", role="Architect - Senior", score=51, status="generation_ready")
    result = reconcile(engine_root)
    status, processing_state = job_status(engine_root, "9c85bd0d9661c2f34978")
    assert status == "selective"
    assert processing_state["status"] == "selective"
    assert any(str(b).startswith("below_generation_threshold:51") for b in processing_state["blockers"])
    assert processing_state["external_action_allowed"] is False
    packet = engine_root / "projects/job-automation/artifacts/9c85bd0d9661c2f34978/generation_packet.json"
    assert not packet.exists()
    assert "9c85bd0d9661c2f34978" in result["changed_job_ids"]


def test_weak_band_generation_ready_becomes_blocked(engine_root: Path) -> None:
    seed_job(engine_root, "053bd41b6432bd0f9586", company="Bechtel Corporation", role="Planner", score=16, status="generation_ready")
    seed_job(engine_root, "0a2613ccd22835f9239d", company="Dar Al Bina", role="Architect", score=23, status="generation_ready")
    result = reconcile(engine_root)
    assert job_status(engine_root, "053bd41b6432bd0f9586")[0] == "blocked"
    assert job_status(engine_root, "0a2613ccd22835f9239d")[0] == "blocked"
    assert result["changed_count"] == 2


def test_above_threshold_generation_ready_is_preserved(engine_root: Path) -> None:
    seed_job(engine_root, "fd6675da1bb6de6f40a1", company="Parsons", role="Senior Project Manager (Design)", score=73, status="generation_ready")
    result = reconcile(engine_root)
    status, _ = job_status(engine_root, "fd6675da1bb6de6f40a1")
    assert status == "generation_ready"
    assert "fd6675da1bb6de6f40a1" not in result["changed_job_ids"]


def test_stale_threshold_blocker_removed_and_genuine_blocker_preserved(engine_root: Path) -> None:
    job_id = "11111111111111111111"
    seed_job(engine_root, job_id, company="Example", role="Design Manager", score=74, status="blocked", route="unresolved", application_url="")
    set_current_blockers(
        engine_root,
        job_id,
        ["below_generation_threshold:74", "application_route_unresolved"],
        pipeline_blockers=["below_generation_threshold:74", "application_route_unresolved"],
    )
    result = reconcile(engine_root)
    status, state = job_status(engine_root, job_id)
    assert status == "blocked"
    assert state["blockers"] == ["application_route_unresolved"]
    assert result["stale_threshold_cleanup_count"] == 1
    assert result["stale_threshold_blockers_remaining"]["job_count"] == 0
    pipeline = json.loads(
        (engine_root / f"projects/job-automation/artifacts/{job_id}/pipeline_state.json").read_text(encoding="utf-8")
    )["data"]
    assert pipeline["blockers"] == ["application_route_unresolved"]


def test_sole_stale_blocker_is_not_blindly_promoted(engine_root: Path) -> None:
    job_id = "22222222222222222222"
    seed_job(engine_root, job_id, company="Example", role="Senior Project Manager", score=76, status="blocked")
    set_current_blockers(engine_root, job_id, ["below_generation_threshold:76"])
    first = reconcile(engine_root)
    status, state = job_status(engine_root, job_id)
    assert status == "blocked"
    assert state["blockers"] == ["legacy_blocked_pending_owner_reassessment"]
    assert first["stale_threshold_cleanup_count"] == 1
    second = reconcile(engine_root)
    assert second["stale_threshold_cleanup_count"] == 0
    assert second["changed_count"] == 0


def test_below_current_threshold_blocker_is_retained(engine_root: Path) -> None:
    job_id = "33333333333333333333"
    seed_job(engine_root, job_id, company="Example", role="Architect", score=55, status="selective")
    set_current_blockers(engine_root, job_id, ["below_generation_threshold:55"])
    result = reconcile(engine_root)
    status, state = job_status(engine_root, job_id)
    assert status == "selective"
    assert state["blockers"] == ["below_generation_threshold:55"]
    assert result["stale_threshold_cleanup_count"] == 0


def test_luxoft_rejected_despite_score(engine_root: Path) -> None:
    seed_job(engine_root, LUXOFT_JOB_ID, company="Luxoft", role="Project Manager/Scrum Master", score=72, status="generation_ready")
    result = reconcile(engine_root)
    status, processing_state = job_status(engine_root, LUXOFT_JOB_ID)
    assert status == "rejected"
    assert processing_state["status"] == "rejected"
    assert processing_state["external_action_allowed"] is False
    assert any(str(b).startswith("owner_rejected:") for b in processing_state["blockers"])
    decision = processing_state.get("owner_decision") or {}
    assert decision.get("decision") == "rejected"
    packet = engine_root / "projects/job-automation/artifacts" / LUXOFT_JOB_ID / "generation_packet.json"
    assert not packet.exists()
    # append-only history contains the rejection event
    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    record = tracker.get_job(LUXOFT_JOB_ID)
    assert any(event["action"] == "rejected" and event["actor"] == "owner" for event in record["history"])


def test_faden_persisted_decision(engine_root: Path) -> None:
    seed_job(engine_root, FADEN_JOB_ID, company="FADEN CONTRACTING LTD", role="Architecture Project Manager", score=78, status="generation_ready")
    result = reconcile(engine_root)
    status, processing_state = job_status(engine_root, FADEN_JOB_ID)
    assert status == "generation_ready"
    assert processing_state["selected_resume_variant"] == "ats-linear"
    assert processing_state["no_gmail_draft"] is True
    assert processing_state["external_action_allowed"] is False
    assert processing_state["auto_submit"] is False
    assert processing_state["owner_decision"]["decision"] == "accepted"
    assert FADEN_JOB_ID not in result["changed_job_ids"] or any(
        item["job_id"] == FADEN_JOB_ID and item["reason"] == "owner_decision" for item in result["changed_jobs"]
    )


@pytest.mark.parametrize("application_status", ["submitted", "sent", "applied"])
def test_faden_submitted_application_repairs_processing_lifecycle(
    engine_root: Path,
    application_status: str,
) -> None:
    seed_job(
        engine_root,
        FADEN_JOB_ID,
        company="FADEN CONTRACTING LTD",
        role="Architecture Project Manager",
        score=78,
        status="generation_ready",
        application_status=application_status,
    )
    result = reconcile(engine_root)
    status, processing_state = job_status(engine_root, FADEN_JOB_ID)
    assert status == "applied"
    assert processing_state["status"] == "applied"
    assert processing_state["external_action_allowed"] is False
    assert processing_state["send_or_submit"] is False
    assert any(
        item["job_id"] == FADEN_JOB_ID
        and item["reason"] == "application_submitted_lifecycle_repair"
        and item["to"] == "applied"
        for item in result["changed_jobs"]
    )
    named = {item["job_id"]: item for item in result["named_generation_ready_checks"]}
    assert named[FADEN_JOB_ID]["eligible"] is False
    second = reconcile(engine_root)
    assert second["changed_count"] == 0
    assert any(
        item["job_id"] == FADEN_JOB_ID and item.get("reason") == "application_submitted"
        for item in second["preserved"]
    )


def test_reconcile_is_idempotent(engine_root: Path) -> None:
    seed_job(engine_root, "9c85bd0d9661c2f34978", company="Gensler", role="Architect - Senior", score=51, status="generation_ready")
    seed_job(engine_root, LUXOFT_JOB_ID, company="Luxoft", role="Project Manager/Scrum Master", score=72, status="generation_ready")
    seed_job(engine_root, FADEN_JOB_ID, company="FADEN CONTRACTING LTD", role="Architecture Project Manager", score=78, status="generation_ready")
    first = reconcile(engine_root)
    assert first["changed_count"] >= 2
    second = reconcile(engine_root)
    assert second["changed_count"] == 0
    assert second["changed_job_ids"] == []
    status, _ = job_status(engine_root, LUXOFT_JOB_ID)
    assert status == "rejected"


def test_preserves_applied_approved_and_supersedes_duplicate(engine_root: Path) -> None:
    seed_job(engine_root, ZAWAYA_APPLIED_JOB_ID, company="Zawaya Albina", role="Design Manager", score=93, status="applied")
    seed_job(engine_root, ZAWAYA_DUPLICATE_JOB_ID, company="Zawaya Albina", role="Design Manager", score=93, status="blocked")
    seed_job(engine_root, PARSONS_SDM_APPROVED_JOB_ID, company="Parsons Corporation", role="Senior Design Manager", score=93, status="package_approved_pending_submission")
    result = reconcile(engine_root)
    assert job_status(engine_root, ZAWAYA_APPLIED_JOB_ID)[0] == "applied"
    assert job_status(engine_root, PARSONS_SDM_APPROVED_JOB_ID)[0] == "package_approved_pending_submission"
    status, processing_state = job_status(engine_root, ZAWAYA_DUPLICATE_JOB_ID)
    assert status == "superseded"
    assert processing_state.get("canonical_job_id") == ZAWAYA_APPLIED_JOB_ID
    assert ZAWAYA_APPLIED_JOB_ID in result["protected_job_ids"]
    assert PARSONS_SDM_APPROVED_JOB_ID in result["protected_job_ids"]


def test_named_roles_checked_not_forced(engine_root: Path) -> None:
    seed_job(engine_root, FADEN_JOB_ID, company="FADEN CONTRACTING LTD", role="Architecture Project Manager", score=78, status="generation_ready")
    seed_job(engine_root, "fd6675da1bb6de6f40a1", company="Parsons", role="Senior Project Manager (Design)", score=73, status="generation_ready")
    seed_job(engine_root, "d40d3f3f10bb470875a0", company="Parsons", role="Project Manager (Infrastructure Design)", score=71, status="generation_ready")
    seed_job(engine_root, "48166c0b35b4d62b8489", company="AECOM", role="Senior Project Manager", score=71, status="generation_ready")
    # A named role whose canonical current state is blocked is reported, not forced
    seed_job(engine_root, "4509528885e013220587", company="TurnerTownsend", role="MEP Project Manager", score=71, status="blocked")
    result = reconcile(engine_root)
    checks = {item["job_id"]: item for item in result["named_generation_ready_checks"]}
    assert len(checks) == len(NAMED_GENERATION_READY_ROLES)
    for job_id, _, _ in NAMED_GENERATION_READY_ROLES:
        assert job_id in checks
    assert checks["fd6675da1bb6de6f40a1"]["eligible"] is True
    assert checks["4509528885e013220587"]["eligible"] is False
    assert "not forced" in checks["4509528885e013220587"]["reason"]
    assert job_status(engine_root, "4509528885e013220587")[0] == "blocked"


def test_owner_force_keeps_below_threshold_eligible(engine_root: Path) -> None:
    seed_job(engine_root, "aaa11111111111111111", company="Example", role="Design Manager", score=55, status="generation_ready")
    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    tracker.update_job(
        "aaa11111111111111111",
        {"scoring": {"total": 55, "raw_total": 55, "recommendation": "selective", "human_override": {"score": 75, "raw_score": 55, "reason": "owner forced generation", "actor": "owner", "at": "2026-08-06T00:00:00+00:00"}}},
        comment="Owner force decision persisted",
        actor="owner",
        action="approved",
    )
    result = reconcile(engine_root)
    assert job_status(engine_root, "aaa11111111111111111")[0] == "generation_ready"
    assert "aaa11111111111111111" not in result["changed_job_ids"]


def test_run_never_sends_or_submits(engine_root: Path, job_payload: dict) -> None:
    from career_engine.ops import run
    build_bundle(engine_root)
    payload = dict(job_payload)
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-06T00:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    payload["application_url"] = "https://example.com/jobs/123/apply"
    state = prepare(payload, root=engine_root, actor="hermes")
    # force the job to be a high-priority eligible job deterministically
    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    tracker.update_job(
        state["job_id"],
        {
            "fit_score": 78,
            "priority": "high_priority",
            "scoring": {"total": 78, "raw_total": 78, "recommendation": "high_priority", "rationale": [], "gaps": []},
        },
        comment="Test fixture: promote job to high-priority eligible",
        actor="system",
    )
    report = run(engine_root)
    assert report["send_or_submit"] is False
    assert report["drafts_created"] == 0
    assert report["submissions"] == 0
    assert report["bundle"]["valid"] is True
    assert report["reconciliation"]["send_or_submit"] is False
    processed_ids = [item["job_id"] for item in report["processed"]]
    assert state["job_id"] in processed_ids
    assert any(item["generation_packet"] for item in report["processed"])
    dashboard_path = engine_root / "projects/job-automation/runtime/dashboard-data.json"
    assert dashboard_path.is_file()
    assert report["report_path"]
    assert Path(report["report_path"]).is_file()


def test_run_excludes_submitted_generation_ready_even_if_reconcile_is_noop(
    engine_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import career_engine.ops as ops_module

    build_bundle(engine_root)
    seed_job(
        engine_root,
        FADEN_JOB_ID,
        company="FADEN CONTRACTING LTD",
        role="Architecture Project Manager",
        score=86,
        status="generation_ready",
        application_status="submitted",
    )
    monkeypatch.setattr(
        ops_module,
        "reconcile",
        lambda root=None: {"send_or_submit": False, "changed_count": 0, "changed_job_ids": []},
    )
    report = ops_module.run(engine_root, process_all=True)
    processed_ids = [item["job_id"] for item in report["processed"]]
    assert FADEN_JOB_ID not in processed_ids
    assert report["send_or_submit"] is False
    assert report["drafts_created"] == 0
    assert report["submissions"] == 0
