from __future__ import annotations

import json
from pathlib import Path

from career_engine.core import normalize_job

REPO = Path(__file__).resolve().parents[1]
TAXONOMY = json.loads(
    (REPO / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text(encoding="utf-8")
)


def _normalize(description: str) -> dict:
    return normalize_job(
        {
            "company": "Example Consultancy",
            "role": "Senior Design Manager",
            "location": "Riyadh, Saudi Arabia",
            "full_job_description": description,
        },
        TAXONOMY,
    )


def test_flattened_jd_restores_sections_and_excludes_marketing() -> None:
    text = (
        "Senior Design Manager Example Consultancy. Key Responsibilities "
        "Lead multidisciplinary design coordination across all project stages. "
        "Review consultant submissions and manage authority approvals. "
        "Prepare design risk reports for the project director. "
        "Requirements Bachelor's degree in Architecture or Engineering. "
        "Minimum 15 years of design management experience is required. "
        "Strong client and stakeholder leadership is essential. "
        "Preferred Qualifications Experience with BIM coordination is preferred. "
        "What We Offer Competitive benefits and an inclusive workplace. Apply today."
    )
    normalized = _normalize(text)
    requirements = normalized["requirements"]

    assert len(requirements) >= 7
    assert any(item["category"] == "responsibilities" and "Lead multidisciplinary" in item["text"] for item in requirements)
    assert any(item["category"] == "mandatory" and "15 years" in item["text"] for item in requirements)
    assert any(item["category"] == "preferred" and "BIM coordination" in item["text"] for item in requirements)
    assert not any("Competitive benefits" in item["text"] for item in requirements)
    assert normalized["full_job_description"] == text


def test_structured_jd_keeps_existing_requirement_structure() -> None:
    text = """Key Responsibilities
- Lead architectural design coordination.
- Review consultant submissions.

Requirements
- Bachelor's degree in Architecture.
- 15 years of relevant experience.

Preferred
- BIM coordination experience.
"""
    normalized = _normalize(text)
    extracted = [(item["category"], item["text"]) for item in normalized["requirements"]]

    assert extracted == [
        ("mandatory", "Bachelor's degree in Architecture."),
        ("mandatory", "15 years of relevant experience."),
        ("preferred", "BIM coordination experience."),
        ("responsibilities", "Lead architectural design coordination."),
        ("responsibilities", "Review consultant submissions."),
    ]


def test_buro_like_flattened_jd_is_not_one_giant_requirement() -> None:
    text = (
        "Senior Design Manager\n"
        "Example Consultancy · Riyadh, Saudi Arabia\n"
        "Employment: Full-time\n"
        "Your key duties Oversee design execution for major building projects in the Middle East. "
        "Lead BIM coordination and review gateway submissions. "
        "Manage design risks, changes, schedules, and client reporting. "
        "Support value engineering and consultant procurement. "
        "Your Skills And Experience Bachelor's degree in Architecture or Engineering. "
        "Proven design management experience in an engineering consultancy is required. "
        "Demonstrable leadership and stakeholder communication skills are essential. "
        "What We Offer Attractive benefits and continuous development.\n"
        "URL: https://example.com/jobs/123"
    )
    normalized = _normalize(text)
    requirements = normalized["requirements"]

    assert len(requirements) >= 7
    assert max(len(item["text"]) for item in requirements) < 280
    assert sum(item["category"] == "mandatory" for item in requirements) >= 3
    assert sum(item["category"] == "responsibilities" for item in requirements) >= 4


def test_headingless_rams_qualifications_are_mandatory_and_not_context() -> None:
    text = (
        "RAMS Lead EgisGroup.\nLead RAMS activities for rail transportation projects.\n"
        "10+ years of RAMS or reliability engineering experience in rail transportation is required.\n"
        "Experience with FMEA, FTA, RBD, EN 50126 and EN 50129 is essential.\n"
        "Experience with rail systems, signalling and rolling stock is mandatory.\n"
        "Knowledge of RAMS tools is required."
    )
    normalized = _normalize(text)
    mandatory = [item for item in normalized["requirements"] if item["priority"] == "mandatory"]
    assert len(mandatory) >= 4
    assert any("FMEA" in item["text"] for item in mandatory)
    assert not any(
        item["priority"] == "context" and any(term in item["text"] for term in ("10+ years", "FMEA", "RAMS tools"))
        for item in normalized["requirements"]
    )


def test_explicit_responsibilities_heading_keeps_requirement_like_line_as_context() -> None:
    normalized = _normalize(
        "RAMS Lead Example Consultancy.\n"
        "Responsibilities\n"
        "- Experience with clients and contractors is required.\n"
        "- Lead RAMS activities for rail transportation projects.\n"
        "Requirements\n"
        "- 10+ years of RAMS experience in rail transportation is required.\n"
    )

    client_line = next(
        item for item in normalized["requirements"]
        if "clients and contractors" in item["text"]
    )
    assert client_line["priority"] == "context"
    assert client_line["category"] == "responsibilities"


def test_normalization_preserves_exact_tracker_posting_date() -> None:
    normalized = normalize_job(
        {"company": "Example", "role": "Design Manager", "posting_date": "2026-08-12 (exact, from SmartRecruiters releasedDate)", "full_job_description": "A sufficiently long description " * 8},
        TAXONOMY,
    )
    assert normalized["posting_date"] == "2026-08-12 (exact, from SmartRecruiters releasedDate)"
