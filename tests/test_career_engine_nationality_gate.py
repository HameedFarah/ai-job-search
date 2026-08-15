from career_engine.bundle import build_bundle
from career_engine.core import match_evidence, nationality_requirement_gate, normalize_job, score_fit


def evaluate(engine_root, role, description, *, nationalities=None):
    bundle = build_bundle(engine_root)
    if nationalities is not None:
        bundle["identity"] = {**bundle.get("identity", {}), "nationalities": nationalities}
    normalized = normalize_job({
        "company": "TestCo", "role": role, "location": "Riyadh",
        "full_job_description": description,
    }, bundle["taxonomy"])
    return score_fit(normalized, match_evidence(normalized, bundle), bundle)


def test_explicit_nationality_mandate_blocks_nonmatching_candidate(engine_root):
    score = evaluate(engine_root, "Construction Manager (Saudi National)",
                     "We're looking for an experienced Construction Manager (Saudi National).")
    assert score["total"] < 70
    assert score["calibration"]["eligibility_blocker"] == "mandatory_nationality_mismatch"
    assert score["gaps"][0].startswith("mandatory_nationality_mismatch:")


def test_explicit_mandate_matches_candidate_nationality(engine_root):
    score = evaluate(engine_root, "Design Manager - Infrastructure (Saudi National)",
                     "Saudi nationals only. Lead infrastructure design management.",
                     nationalities=["Saudi"])
    assert score["total"] >= 70
    assert "mandatory_nationality_mismatch" not in score["calibration"]


def test_controls_do_not_trigger_nationality_gate(engine_root):
    for text in (
        "Construction Manager in Saudi Arabia.",
        "Experience with Saudi projects and Saudi building code.",
        "Saudi Council of Engineers registration preferred.",
        "Saudi experience required.",
    ):
        assert nationality_requirement_gate(text) is None
    score = evaluate(engine_root, "Construction Manager", "Construction Manager in Saudi Arabia. Experience with Saudi building code.")
    assert "mandatory_nationality_mismatch" not in score["calibration"]


def test_general_management_roles_keep_existing_scoring(engine_root):
    score = evaluate(engine_root, "Construction Manager", "Lead construction delivery and multidisciplinary design coordination.")
    assert score["total"] >= 70
