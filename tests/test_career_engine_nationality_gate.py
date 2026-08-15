from __future__ import annotations

import copy
import json
from pathlib import Path

from career_engine.core import match_evidence, nationality_requirement_gate, normalize_job, score_fit


REPO = Path(__file__).resolve().parents[1]
RUNTIME_BUNDLE = json.loads(
    (REPO / "projects/job-automation/config/runtime-bundle.v1.json").read_text(encoding="utf-8")
)


def evaluate(role: str, description: str, *, nationalities=None):
    bundle = copy.deepcopy(RUNTIME_BUNDLE)
    if nationalities is not None:
        bundle["identity"] = {**bundle.get("identity", {}), "nationalities": nationalities}
    normalized = normalize_job({
        "company": "TestCo",
        "role": role,
        "location": "Riyadh, Saudi Arabia",
        "full_job_description": description,
    }, bundle["taxonomy"])
    return score_fit(normalized, match_evidence(normalized, bundle), bundle)


def test_explicit_nationality_mandate_blocks_nonmatching_candidate():
    score = evaluate(
        "Construction Manager (Saudi National)",
        "We're looking for an experienced Construction Manager (Saudi National). Lead project delivery, construction coordination, client interfaces, quality controls and multidisciplinary site teams across major developments.",
    )
    assert score["total"] < 70
    assert score["calibration"]["eligibility_blocker"] == "mandatory_nationality_mismatch"
    assert score["gaps"][0].startswith("mandatory_nationality_mismatch:")


def test_explicit_mandate_matches_candidate_nationality():
    score = evaluate(
        "Design Manager - Infrastructure (Saudi National)",
        "Saudi nationals only. Lead infrastructure design management, multidisciplinary consultant coordination, client interfaces, technical assurance, project delivery and construction-stage design reviews across major programmes.",
        nationalities=["Saudi"],
    )
    assert "mandatory_nationality_mismatch" not in score["calibration"]
    assert score["calibration"]["target_management"] is True


def test_controls_do_not_trigger_nationality_gate():
    for text in (
        "Construction Manager in Saudi Arabia.",
        "Experience with Saudi projects and Saudi building code.",
        "Saudi Council of Engineers registration preferred.",
        "Saudi experience required.",
    ):
        assert nationality_requirement_gate(text) is None
    score = evaluate(
        "Construction Manager",
        "Construction Manager in Saudi Arabia. Lead construction delivery and multidisciplinary coordination with strong Saudi project experience, Saudi building-code knowledge, client management, quality assurance and site-stage technical reviews.",
    )
    assert "mandatory_nationality_mismatch" not in score["calibration"]


def test_general_management_roles_keep_existing_classification():
    score = evaluate(
        "Construction Manager",
        "Lead construction delivery and multidisciplinary design coordination across complex projects, managing consultant interfaces, client stakeholders, programme controls, quality reviews, risk management and construction-stage issue resolution.",
    )
    assert "mandatory_nationality_mismatch" not in score["calibration"]
    assert score["calibration"]["target_management"] is True
    assert score["calibration"]["mismatch_multiplier"] == 1.0
