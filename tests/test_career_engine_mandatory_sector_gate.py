from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

from career_engine.bundle import build_bundle
from career_engine.core import (
    claim_supports_domain,
    domain_requirement_gate,
    match_evidence,
    normalize_job,
    score_fit,
)
from career_engine.pipeline import prepare
from tests.test_career_engine_live_status_gate import live_control_job  # noqa: F401
from tests.test_career_engine_v1 import engine_root  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests/fixtures/career_engine/sanitized-jobs.json"

HIGH_PRIORITY = 80


@lru_cache(maxsize=None)
def _load_fixture_jobs() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["jobs"]


def fixture_job(name: str) -> dict:
    """Deep-copied fictional vacancy fixture, so tests never share mutable state."""
    return copy.deepcopy(_load_fixture_jobs()[name])


def stadium_job() -> dict:
    return fixture_job("stadium")


def shopping_mall_job() -> dict:
    return fixture_job("shopping_mall")


def hospitality_job() -> dict:
    return fixture_job("hospitality")


def defense_director_job() -> dict:
    return fixture_job("defense")


def sports_stadium_job() -> dict:
    return fixture_job("sports_stadium")


def evaluate(payload: dict, engine_root: Path | None) -> tuple[dict, dict, list[dict], dict]:
    bundle = build_bundle(engine_root)
    normalized = normalize_job(payload, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    score = score_fit(normalized, matches, bundle)
    return bundle, normalized, matches, score


def match_for_text(normalized: dict, matches: list[dict], fragment: str) -> dict:
    for match in matches:
        requirement = next(
            item for item in normalized["requirements"] if item["id"] == match["requirement_id"]
        )
        if fragment.lower() in requirement["text"].lower():
            return match
    raise AssertionError(f"No requirement match found for fragment: {fragment!r}")


def test_stadium_mandatory_domain_is_a_material_gap(engine_root: Path) -> None:
    bundle, normalized, matches, score = evaluate(stadium_job(), engine_root)
    stadium = match_for_text(normalized, matches, "Stadium, Events or Sports venue experience is essential")
    assert stadium["status"] == "gap"
    assert stadium["claim_ids"] == []
    assert "Mandatory domain" in stadium["note"]
    assert any(fragment in score["gaps"][0].lower() for fragment in ("stadium", "sports venue"))
    assert score["total"] < HIGH_PRIORITY
    assert score["recommendation"] != "high_priority"


def test_shopping_malls_requires_domain_claim_not_generic_evidence(engine_root: Path) -> None:
    bundle, normalized, matches, score = evaluate(shopping_mall_job(), engine_root)
    mall = match_for_text(
        normalized, matches, "Proven experience delivering major shopping malls, retail destinations"
    )
    # The sanitized fixture profile holds no approved retail/mall claim, so generic claims
    # must not satisfy the requirement: it is a material mandatory gap.
    assert mall["status"] == "gap"
    assert mall["claim_ids"] == []
    assert "Mandatory domain" in mall["note"]
    assert score["total"] < HIGH_PRIORITY
    assert score["recommendation"] != "high_priority"


def test_hospitality_mandatory_domain_is_a_material_gap(engine_root: Path) -> None:
    bundle, normalized, matches, score = evaluate(hospitality_job(), engine_root)
    hospitality = match_for_text(
        normalized, matches, "Proven experience delivering large-scale hospitality or leisure projects"
    )
    assert hospitality["status"] == "gap"
    assert hospitality["claim_ids"] == []
    assert "Mandatory domain" in hospitality["note"]
    assert score["total"] < HIGH_PRIORITY
    assert score["recommendation"] != "high_priority"


def test_defense_director_mandatory_domain_is_a_material_gap(engine_root: Path) -> None:
    bundle, normalized, matches, score = evaluate(defense_director_job(), engine_root)
    defense = match_for_text(normalized, matches, "government/defense infrastructure")
    assert defense["status"] == "gap"
    assert defense["claim_ids"] == []
    assert "Mandatory domain" in defense["note"]
    assert score["total"] < HIGH_PRIORITY
    assert score["recommendation"] != "high_priority"


def test_sports_stadium_experience_requirement_is_a_material_gap(engine_root: Path) -> None:
    bundle, normalized, matches, score = evaluate(sports_stadium_job(), engine_root)
    stadium = match_for_text(
        normalized, matches, "Minimum 15 years of experience with a focus on large-scale sports stadium"
    )
    assert stadium["status"] == "gap"
    assert stadium["claim_ids"] == []
    assert "Mandatory domain" in stadium["note"]
    assert score["total"] < HIGH_PRIORITY
    assert score["recommendation"] != "high_priority"


def test_live_control_has_no_sector_gate(engine_root: Path) -> None:
    payload = live_control_job()
    _, _, matches, score = evaluate(payload, engine_root)
    gated = [match for match in matches if "Mandatory domain" in match["note"]]
    assert gated == []
    assert score["recommendation"] != "weak"
    state = prepare(payload, root=engine_root, actor="system")
    assert state["stage"] == "generation_ready"
    assert state["outputs"]["generation_packet"]


def test_domain_requirement_gate_requires_both_domain_and_mandatory_signal(engine_root: Path) -> None:
    taxonomy = build_bundle(engine_root)["taxonomy"]
    assert domain_requirement_gate("Stadium, Events or Sports venue experience is essential.", taxonomy) == "stadium_sports_venue"
    assert domain_requirement_gate("Proven experience delivering major shopping malls or retail destinations.", taxonomy) == "shopping_mall_retail"
    assert domain_requirement_gate("Proven experience delivering large-scale hospitality or leisure projects.", taxonomy) == "hospitality_leisure"
    assert domain_requirement_gate("A proven track record on government/defense infrastructure is required.", taxonomy) == "defense_military_security"
    assert domain_requirement_gate("Minimum 15 years of experience with a focus on sports stadium design.", taxonomy) == "stadium_sports_venue"
    # Domain wording without a mandatory signal is not gated.
    assert domain_requirement_gate("Led stadium design coordination across the programme.", taxonomy) is None
    # Mandatory wording without a domain term is not gated.
    assert domain_requirement_gate("At least 15 years of progressive design management experience is essential.", taxonomy) is None
    assert domain_requirement_gate("10+ years of RAMS or reliability engineering experience in rail transportation is required.", taxonomy) == "rams_reliability_systems_assurance"
    assert domain_requirement_gate("Lead design management for rail projects.", taxonomy) is None


def test_rams_role_is_out_of_lane_and_unsupported_requirements_are_gaps(engine_root: Path) -> None:
    payload = {
        "company": "EgisGroup", "role": "RAMS Lead", "location": "Riyadh",
        "full_job_description": (
            "RAMS Lead.\nLead RAMS activities for rail transportation projects.\n"
            "10+ years of RAMS or reliability engineering experience in rail transportation is required.\n"
            "Experience with FMEA, FTA, RBD, EN 50126 and EN 50129 is essential.\n"
            "Experience with rail systems, signalling and rolling stock is mandatory.\n"
            "Knowledge of RAMS tools is required."
        ),
    }
    bundle, normalized, matches, score = evaluate(payload, engine_root)
    assert any(item["priority"] == "mandatory" for item in normalized["requirements"])
    assert any(match["status"] == "gap" for match in matches)
    assert score["calibration"]["out_of_lane"] is True
    assert score["recommendation"] != "high_priority"
    assert score["total"] < HIGH_PRIORITY


def test_claim_supports_domain_only_from_direct_domain_evidence(engine_root: Path) -> None:
    taxonomy = build_bundle(engine_root)["taxonomy"]
    domain_claim = {
        "id": "test.retail.mall",
        "label": "17,000-sqm retail mall",
        "tags": ["design management", "project delivery"],
        "aliases": ["retail", "mixed use"],
        "safe_wording": "Delivered a 17,000-sqm retail mall with mixed-use elements.",
    }
    generic_claim = {
        "id": "test.generic",
        "label": "112+ documented projects",
        "tags": ["architecture", "leadership", "multidisciplinary coordination", "project delivery"],
        "aliases": ["projects", "portfolio"],
        "safe_wording": "Documented projects across architecture and delivery roles.",
    }
    assert claim_supports_domain(domain_claim, "shopping_mall_retail", taxonomy) is True
    assert claim_supports_domain(
        domain_claim,
        "shopping_mall_retail",
        taxonomy,
        "Proven experience delivering major shopping malls.",
    ) is False
    scaled_claim = {
        **domain_claim,
        "label": "Major shopping mall delivery",
        "safe_wording": "Delivered a major shopping mall through design and construction stages.",
    }
    assert claim_supports_domain(
        scaled_claim,
        "shopping_mall_retail",
        taxonomy,
        "Proven experience delivering major shopping malls.",
    ) is True
    assert claim_supports_domain(generic_claim, "shopping_mall_retail", taxonomy) is False
    assert claim_supports_domain(generic_claim, "stadium_sports_venue", taxonomy) is False


def test_domain_supported_claim_satisfies_gated_requirement(engine_root: Path) -> None:
    bundle, normalized, matches, _ = evaluate(shopping_mall_job(), engine_root)
    mall = match_for_text(
        normalized, matches, "Proven experience delivering major shopping malls, retail destinations"
    )
    assert mall["status"] == "gap"
    assert mall["claim_ids"] == []
