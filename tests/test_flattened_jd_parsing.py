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
