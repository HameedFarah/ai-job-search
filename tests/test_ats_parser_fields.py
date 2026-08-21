from __future__ import annotations

from pathlib import Path

from docx import Document

from career_engine.renderer import ats_template_status, render_ats_docx
from tests.test_ats_linear_renderer import _application, _packet
from tests.test_career_engine_v1 import engine_root, job_payload  # noqa: F401


def _rendered_text(path: Path) -> str:
    document = Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def test_ats_template_exposes_parser_oriented_profile_skills_and_tools(engine_root: Path) -> None:
    spec = ats_template_status(root=engine_root)["spec"]
    assert spec["version"] == "1.1"
    credentials = "\n".join(spec["verified_sections"]["credentials"])
    for required in (
        "PROFILE DETAILS",
        "Current Title: District Manager",
        "Current Employer: Arab Sustainable Architecture / Tubaila Team Workshop (TTW)",
        "Experience: 22+ years",
        "Current Location: Riyadh, Saudi Arabia",
        "CORE SKILLS",
        "Design Management",
        "Contract Administration",
        "BIM/Revit",
        "SOFTWARE & TOOLS",
        "Autodesk Revit",
        "AutoCAD",
        "Microsoft Project",
        "Microsoft Office",
        "Adobe Photoshop",
    ):
        assert required in credentials


def test_ats_education_uses_explicit_oracle_friendly_labels(engine_root: Path) -> None:
    education = ats_template_status(root=engine_root)["spec"]["verified_sections"]["education"]
    rendered = "\n".join(f"{entry['degree']} | {entry['institution']} | {entry['year']}" for entry in education)
    for required in (
        "Master's Degree",
        "Bachelor's Degree",
        "Degree: Global Master's in Construction Project Management",
        "Field of Study: Construction Project Management / Construction Management",
        "School: Zigurat Global Institute of Technology",
        "Program Partner: Universitat de Barcelona",
        "Location: Barcelona, Spain",
        "Dates: Nov 2021 - Nov 2022",
        "Completion Date: Nov 2022",
        "Graduated: Yes",
        "Degree: Master of Business Administration (MBA)",
        "School: New York Institute of Technology",
        "Completion Date: Aug 2010",
        "GPA: 3.93/4.00, with honors",
        "Degree: Bachelor of Science in Architectural Engineering",
        "School: University of Jordan",
        "Completion Date: Aug 2004",
        "GPA: 3.14/4.00",
    ):
        assert required in rendered


def test_every_future_ats_render_contains_parser_fields(job_payload: dict, engine_root: Path, tmp_path: Path) -> None:
    packet = _packet(job_payload, engine_root)
    application = _application(packet)
    result = render_ats_docx(
        "ats-parser-fields-test-job",
        application,
        packet,
        root=engine_root,
        out_dir=tmp_path,
    )
    rendered = _rendered_text(Path(result["docx"]))
    for required in (
        "PROFILE DETAILS",
        "Current Title: District Manager",
        "CORE SKILLS",
        "SOFTWARE & TOOLS",
        "Education",
        "Field of Study: Construction Project Management / Construction Management",
        "School: Zigurat Global Institute of Technology",
        "Program Partner: Universitat de Barcelona",
        "Graduated: Yes",
        "Completion Date: Nov 2022",
    ):
        assert required.lower() in rendered.lower()
