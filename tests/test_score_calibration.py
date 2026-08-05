"""Scoring calibration tests.

Covers the specialization/seniority mismatch calibration:
- materially mismatched specialization roles (planner, project engineering,
  machine-learning engineer) and production individual-contributor roles
  (architect, senior architect, landscape architect, design architect) must
  not score 80+;
- adjacent senior design-management roles (Senior Design Manager, Design
  Director, Design Governance, Technical Design Director, Delivery Project
  Director) must never be suppressed by the mismatch gate (multiplier 1.0);
- raw engine scores and owner score overrides are preserved with evidence;
- threshold 80 means credible generation eligibility only after live
  verification.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_engine.bundle import build_bundle
from career_engine.cli import _apply_score_override
from career_engine.core import match_evidence, normalize_job, score_fit
from career_engine.pipeline import prepare
from tests.test_career_engine_v1 import engine_root, job_payload  # noqa: F401


def _score(payload: dict, engine_root) -> dict:
    bundle = build_bundle(engine_root)
    normalized = normalize_job(payload, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    return score_fit(normalized, matches, bundle)


DESIGN_MANAGEMENT_JD = """
Key Responsibilities
- Lead design governance and multidisciplinary design coordination across complex programmes.
- Manage senior client and stakeholder relationships and oversee project delivery.
- Drive value engineering, quality assurance and design controls.
- Direct the design management of large-scale projects from concept through construction.

Requirements
- Degree in architecture and strong Saudi project experience.
- Demonstrated team leadership and people management.
- Experience with design management and programme delivery.
- Strong technical design coordination and design compliance background.

Preferred
- Saudi Council of Engineers professional classification.
"""


def _jd(role: str, responsibilities: str, requirements: str, preferred: str = "") -> dict:
    return {
        "company": "Example Co",
        "role": role,
        "location": "Riyadh, Saudi Arabia",
        "source": "test",
        "source_url": "https://example.com/jobs/1",
        "application_url": "https://example.com/jobs/1/apply",
        "full_job_description": (
            "Key Responsibilities\n" + responsibilities +
            "\nRequirements\n" + requirements +
            ("\nPreferred\n" + preferred if preferred else "")
        ),
    }


# ---------------------------------------------------------------------------
# Materially mismatched specialization roles must not reach 80
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role,jd", [
    ("Planner", _jd("Planner",
        "- Develop and maintain project schedules and progress reporting.\n- Coordinate planning deliverables.",
        "- Experience in project scheduling and planning software.\n- Knowledge of Primavera P6.")),
    ("Project Engineering Manager", _jd("Project Engineering Manager",
        "- Coordinate with the Project Planning Manager on schedule adherence.\n- Review vendor engineering and BOQ submissions.",
        "- Engineering degree and vendor engineering experience.\n- BIM implementation in projects.")),
    ("Machine Learning Engineer", _jd("Machine Learning Engineer",
        "- Build production Python services and ML pipelines.",
        "- Expert knowledge of Python, Go and distributed systems.\n- Experience with Kubernetes and TensorFlow.")),
])
def test_mismatched_specialization_never_scores_80(engine_root: Path, role: str, jd: dict) -> None:
    score = _score(jd, engine_root)
    assert score["total"] < 80, f"{role} scored {score['total']}: {score}"
    assert score["calibration"]["out_of_lane"] is True or score["calibration"]["production"] is True
    assert score["calibration"]["mismatch_multiplier"] < 1.0


@pytest.mark.parametrize("role", ["Architect", "Senior Architect", "Senior Design Architect", "Landscape Architect"])
def test_production_architect_roles_do_not_score_80(engine_root: Path, role: str) -> None:
    jd = _jd(role,
        "- Produce design documentation and coordinate technical drawings.\n- Support design reviews and BIM coordination.",
        "- Bachelor degree in Architecture.\n- Strong design and documentation skills.")
    score = _score(jd, engine_root)
    assert score["total"] < 80, f"{role} scored {score['total']}"
    assert score["calibration"]["production"] is True
    assert score["calibration"]["mismatch_multiplier"] < 1.0


# ---------------------------------------------------------------------------
# Adjacent senior design-management roles are never suppressed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [
    "Senior Design Manager",
    "Design Director",
    "Manager - Design Governance",
    "Technical Design Director",
    "Utilities Design Director",
    "Delivery Project Director (DEL 1)",
    "Director of Projects",
])
def test_adjacent_design_management_roles_keep_multiplier_one(engine_root: Path, role: str) -> None:
    jd = _jd(role, DESIGN_MANAGEMENT_JD, DESIGN_MANAGEMENT_JD)
    score = _score(jd, engine_root)
    # The mismatch gate never suppresses adjacent senior design-management
    # roles: multiplier stays 1.0 regardless of JD evidence.
    assert score["calibration"]["mismatch_multiplier"] == 1.0
    assert score["calibration"]["adjacent_design_management"] or score["calibration"]["has_management"]


def test_adjacent_design_management_with_strong_evidence_scores_80(engine_root: Path) -> None:
    jd = _jd("Senior Design Manager", DESIGN_MANAGEMENT_JD, DESIGN_MANAGEMENT_JD,
             "Saudi Council of Engineers professional classification.")
    score = _score(jd, engine_root)
    assert score["total"] >= 80, f"Senior Design Manager scored {score['total']}: {score}"
    assert score["recommendation"] == "high_priority"


# ---------------------------------------------------------------------------
# Raw score + human override preservation
# ---------------------------------------------------------------------------


def test_fit_score_carries_raw_total(engine_root: Path) -> None:
    jd = _jd("Senior Design Manager", DESIGN_MANAGEMENT_JD, DESIGN_MANAGEMENT_JD)
    score = _score(jd, engine_root)
    assert score["raw_total"] == score["total"]
    assert score["human_override"] == {}


def test_owner_override_preserves_raw_score_and_records_evidence(engine_root: Path, job_payload: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    # Prepare a live-verified high-fit job so a fit_score stage exists.
    payload = dict(job_payload)
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-03T10:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    state = prepare(payload, root=engine_root, actor="system")
    assert state["stage"] == "generation_ready"
    job_id = state["job_id"]
    raw = state["fit_score"]["total"]
    assert raw >= 80

    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    result = _apply_score_override(job_id, state["fit_score"], raw - 5, "Owner reviewed live JD and reduced priority", "owner")
    assert result["override_recorded"] is True

    record_path = engine_root / "projects/job-automation/data/jobs" / f"{job_id}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    scoring = record["scoring"]
    # Raw engine score is preserved; override is recorded with reason/actor/time.
    assert scoring["raw_total"] == raw
    assert scoring["total"] == raw - 5
    assert scoring["human_override"]["raw_score"] == raw
    assert scoring["human_override"]["score"] == raw - 5
    assert scoring["human_override"]["reason"]
    assert scoring["human_override"]["actor"] == "owner"
    assert scoring["human_override"]["at"]
    # CSV reflects the effective override score.
    import csv
    rows = list(csv.DictReader((engine_root / "projects/job-automation/data/jobs.csv").open(encoding="utf-8")))
    row = next(item for item in rows if item["job_id"] == job_id)
    assert row["fit_score"] == str(raw - 5)
    # Append-only event log carries before/after evidence.
    events = record["history"]
    override_event = next(item for item in events if item.get("action") == "reviewed" and "override" in item.get("comment", ""))
    assert int(override_event["before"]["fit_score"]) == raw
    assert int(override_event["after"]["fit_score"]) == raw - 5


def test_override_requires_reason(engine_root: Path, job_payload: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = dict(job_payload)
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-03T10:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    state = prepare(payload, root=engine_root, actor="system")
    monkeypatch.setenv("CAREER_ENGINE_REPO_ROOT", str(engine_root))
    with pytest.raises(ValueError, match="requires a reason"):
        _apply_score_override(state["job_id"], state["fit_score"], 75, "", "owner")


# ---------------------------------------------------------------------------
# Threshold 80: generation eligibility requires live verification
# ---------------------------------------------------------------------------


def test_live_verified_high_priority_is_generation_ready(engine_root: Path) -> None:
    payload = _jd("Senior Design Manager", DESIGN_MANAGEMENT_JD, DESIGN_MANAGEMENT_JD)
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-03T10:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    state = prepare(payload, root=engine_root, actor="system")
    assert state["stage"] == "generation_ready"
    assert state["fit_score"]["total"] >= 80
    assert "generation_packet" in state["outputs"]


def test_credible_score_without_80_is_blocked_below_threshold(engine_root: Path) -> None:
    # A credible but sub-80 JD (weaker requirement match) must not generate.
    payload = _jd("Design Coordinator",
                  "- Support design reviews and coordinate documentation.\n- Assist BIM coordination.",
                  "- Bachelor degree in Architecture.\n- Basic design management awareness.")
    payload["live_status"] = "live"
    payload["live_verified_at"] = "2026-08-03T10:00:00+00:00"
    payload["live_verification_source"] = "official employer careers page"
    state = prepare(payload, root=engine_root, actor="system")
    assert state["stage"] == "blocked"
    assert any(item.startswith("below_generation_threshold:") for item in state["blockers"])
    assert "generation_packet" not in state["outputs"]


def test_unverified_high_priority_generates_with_warning(engine_root: Path) -> None:
    payload = _jd("Senior Design Manager", DESIGN_MANAGEMENT_JD, DESIGN_MANAGEMENT_JD)
    payload["live_status"] = "unverified"
    state = prepare(payload, root=engine_root, actor="system")
    assert state["stage"] == "generation_ready"
    assert not state["blockers"]
    assert "live_status_unverified:unverified" in state["warnings"]
    assert state["outputs"]["generation_packet"]
