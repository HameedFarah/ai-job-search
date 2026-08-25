from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from career_engine.bundle import build_bundle, bundle_status
from career_engine.cli import doctor
from career_engine.config import load_config
from career_engine.core import decide_route, match_evidence, normalize_job, normalize_text, outward_filename, score_fit, validate_text
from career_engine.generation import create_generation_packet, validate_generated_application
from career_engine.pipeline import prepare
from career_engine.service import get_bundle_info, prepare_job


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def engine_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    config_dir = root / "projects/job-automation/config"
    config_dir.mkdir(parents=True)
    tracker_dir = root / "projects/job-automation"
    shutil.copy2(REPO / "projects/job-automation/tracker.py", tracker_dir / "tracker.py")
    for name in (
        "career-engine.v1.json",
        "requirements-taxonomy.v1.json",
        "generated_application.schema.json",
        "runtime-bundle.schema.json",
        "evidence-index.v1.json",
        "ats-linear-template.v1.json",
        "hermes-review-diff.schema.json",
    ):
        shutil.copy2(REPO / "projects/job-automation/config" / name, config_dir / name)
    config_path = config_dir / "career-engine.v1.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    vault = tmp_path / "vault"
    config["vault"]["root"] = str(vault)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    profile = {
        "schema_version": 1,
        "identity": {
            "professional_name": "Abdelhamid Farah",
            "outward_email": "hameedfarah@gmail.com",
            "current_role": "District Manager",
        },
        "career_chronology": [
            {"employer": "TTW", "title": "District Manager", "start": "2022-12", "end": "present"}
        ],
        "claims": [
            {
                "id": "career.experience.20plus",
                "label": "20+ years of experience",
                "value": "20+ years",
                "tags": ["career tenure", "architecture", "design management", "project delivery"],
                "aliases": ["years of experience", "engineering experience", "construction experience"],
                "confidence": "high",
                "attribution": "career chronology",
                "safe_wording": "More than 20 years of architecture and design-delivery experience."
            },
            {
                "id": "education.bsc.architectural_engineering",
                "label": "Architectural Engineering degree",
                "tags": ["architectural degree", "bachelor", "architecture", "engineering"],
                "aliases": ["bachelor degree", "architectural engineering", "related technical field"],
                "confidence": "high",
                "safe_wording": "Bachelor of Science in Architectural Engineering."
            },
            {
                "id": "capability.codes.regulatory",
                "label": "Codes and regulatory approvals",
                "tags": ["codes and standards", "regulatory compliance", "authority approvals", "quality assurance"],
                "aliases": ["industry standards", "codes", "regulatory approval processes"],
                "confidence": "high",
                "safe_wording": "Directed compliance reviews against codes, standards and authority requirements."
            },
            {
                "id": "capability.communication.executive",
                "label": "Technical communication",
                "tags": ["technical communication", "reports", "presentations", "stakeholder management"],
                "aliases": ["written communication", "oral communication", "technical reports"],
                "confidence": "high",
                "safe_wording": "Prepared technical reports and presentations for senior stakeholders."
            },
            {
                "id": "capability.multidisciplinary.leadership",
                "label": "Multidisciplinary leadership",
                "tags": ["leadership", "multidisciplinary coordination", "design management", "project delivery"],
                "aliases": ["team leadership", "consultant coordination"],
                "confidence": "high",
                "safe_wording": "Led multidisciplinary teams and consultant coordination across concurrent assignments."
            },
            {
                "id": "claim.design",
                "label": "Design governance",
                "value": "42%+",
                "tags": ["design governance", "design management", "leadership", "quality assurance"],
                "aliases": ["design assurance", "design controls"],
                "confidence": "high",
                "safe_wording": "Improved design turnaround by more than 42% through structured design controls."
            },
            {
                "id": "claim.team",
                "label": "Team leadership",
                "value": "26+",
                "tags": ["leadership", "people management", "multidisciplinary coordination"],
                "aliases": ["team", "professionals"],
                "confidence": "high",
                "safe_wording": "Led more than 26 KSA professionals at peak."
            },
            {
                "id": "claim.portfolio",
                "label": "Saudi portfolio",
                "value": "SAR 1.163B",
                "tags": ["project delivery", "portfolio", "saudi arabia"],
                "aliases": ["programme delivery"],
                "confidence": "high",
                "safe_wording": "Directed responsibilities across a SAR 1.163 billion Saudi portfolio."
            },
            {
                "id": "claim.cost",
                "label": "Value engineering",
                "value": "SAR 21.94M",
                "tags": ["value engineering", "commercial", "cost engineering"],
                "aliases": ["value optimisation"],
                "confidence": "high",
                "safe_wording": "Delivered SAR 21.94 million in verified design-stage savings."
            },
            {
                "id": "claim.client",
                "label": "Client leadership",
                "value": "27",
                "tags": ["client management", "stakeholder management", "project delivery"],
                "aliases": ["client engagement"],
                "confidence": "high",
                "safe_wording": "Supported delivery across 27 major-client assignments."
            },
            {
                "id": "claim.sce",
                "label": "SCE Consultant",
                "value": "Consultant (مستشار)",
                "tags": ["credential", "architecture", "saudi council of engineers"],
                "aliases": ["sce"],
                "confidence": "high",
                "safe_wording": "Saudi Council of Engineers - Consultant (مستشار), Architectural Engineering."
            },
            {"id": "cube.projects.25", "label": "Cube developments", "value": "25", "tags": ["design management"], "aliases": [], "confidence": "high", "attribution": "Cube Architects", "safe_wording": "Managed 25 Cube Architects developments."},
            {"id": "cube.team.10plus", "label": "Cube team", "value": "10+", "tags": ["leadership"], "aliases": [], "confidence": "high", "attribution": "Cube Architects", "safe_wording": "Managed more than 10 designers and engineers."},
            {"id": "cube.assets.office_scale", "label": "Office scale", "tags": ["office"], "aliases": [], "confidence": "high", "attribution": "Cube Architects", "safe_wording": "Managed major office developments."},
            {"id": "cube.agreements.30plus", "label": "Cube agreements", "value": "30+", "tags": ["contracts"], "aliases": [], "confidence": "high", "attribution": "Cube Architects", "safe_wording": "Formalized more than 30 agreements."},
            {"id": "cube.bim.workflow.50", "label": "Cube BIM", "value": "50%", "tags": ["bim"], "aliases": [], "confidence": "medium", "attribution": "Cube Architects", "safe_wording": "Improved design duration by up to 50%."},
            {"id": "cube.value_engineering.15plus", "label": "Cube value engineering", "value": "15%+", "tags": ["value engineering"], "aliases": [], "confidence": "medium", "attribution": "Cube Architects", "safe_wording": "Reduced construction costs by more than 15% on relevant work."},
            {"id": "cube.procurement.tender", "label": "Cube procurement", "tags": ["procurement"], "aliases": [], "confidence": "high", "attribution": "Cube Architects", "safe_wording": "Managed tender and procurement documentation."},
            {"id": "earlier.cube_project_architect.delivery", "label": "Cube Project Architect", "tags": ["architecture"], "aliases": [], "confidence": "high", "attribution": "earlier Cube role", "safe_wording": "Coordinated design documentation and consultant integration."},
            {"id": "earlier.cud.smartbuy.750", "label": "CUD retail", "value": "750 sqm", "tags": ["retail"], "aliases": [], "confidence": "high", "attribution": "earlier CUD role", "safe_wording": "Managed a 750-sqm retail branch through handover."},
            {"id": "earlier.procurement.20plus", "label": "Al-Mehanya procurement", "value": "20+", "tags": ["procurement"], "aliases": [], "confidence": "high", "attribution": "earlier Al-Mehanya role", "safe_wording": "Prepared more than 20 RFP and scope packages."},
            {"id": "earlier.sigma.design_packages", "label": "Sigma packages", "tags": ["architecture"], "aliases": [], "confidence": "high", "attribution": "earlier Sigma role", "safe_wording": "Developed coordinated design and tender packages."}
        ],
        "writing_rules": ["Write original persuasive prose and cite claim IDs."],
        "policy_overrides": {"never_self_address_review_draft": True}
    }
    files = {
        "projects/job-automation/career-engine-profile.v1.json": json.dumps(profile, indent=2),
        "projects/job-automation/playbooks/career-engine-application-playbook.md": "# Playbook\nCentral rules.",
        "projects/job-automation/verified-career-profile-2026-08-01.md": "# Verified profile\nEvidence.",
        "projects/job-automation/career-metrics-bank-2026-08-03.md": "# Metrics\nMetrics.",
        "governance/north-star.md": "# North Star\nCareer direction."
    }
    for relative, content in files.items():
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    template = root / config["template"]["repository_path"]
    template.parent.mkdir(parents=True, exist_ok=True)
    source_template = REPO / config["template"]["repository_path"]
    shutil.copy2(source_template, template)
    source_manifest = source_template.with_suffix(".manifest.json")
    if source_manifest.is_file():
        shutil.copy2(source_manifest, template.with_suffix(".manifest.json"))
    return root


@pytest.fixture()
def job_payload() -> dict[str, str]:
    return {
        "company": "Example Development Company",
        "role": "Senior Design Governance Manager",
        "location": "Riyadh, Saudi Arabia",
        "source": "test",
        "source_url": "https://example.com/jobs/123",
        "application_url": "https://example.com/jobs/123/apply",
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
"""
    }


def test_tracker_base_override_binds_clean_source_to_live_authority(
    engine_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = (tmp_path / "live-career-tracker").resolve()
    monkeypatch.setenv("CAREER_ENGINE_TRACKER_BASE", str(authority))
    _, paths = load_config(engine_root)
    assert paths.tracker_base == authority


def test_normalization_is_deterministic(job_payload: dict[str, str], engine_root: Path) -> None:
    taxonomy = json.loads((engine_root / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text())
    first = normalize_job(job_payload, taxonomy)
    second = normalize_job(copy.deepcopy(job_payload), taxonomy)
    assert first == second
    assert normalize_text("A\r\n\r\n  B\t C") == "A\n\nB C"
    assert any(item["priority"] == "mandatory" for item in first["requirements"])


def test_required_and_desired_skills_headings_are_classified(engine_root: Path) -> None:
    taxonomy = json.loads((engine_root / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text())
    payload = {
        "company": "Example",
        "role": "Senior Design Manager",
        "full_job_description": """
What You Will Be Doing
- Lead multidisciplinary design delivery.

Required Skills
- At least 15 years of design management experience.
- Bachelor degree in Architecture.

Desired Skills
- Professional registration such as RIBA.
""",
    }
    normalized = normalize_job(payload, taxonomy)
    priorities = {item["text"]: item["priority"] for item in normalized["requirements"]}
    assert priorities["At least 15 years of design management experience."] == "mandatory"
    assert priorities["Bachelor degree in Architecture."] == "mandatory"
    assert priorities["Professional registration such as RIBA."] == "preferred"


def test_semicolon_required_skills_heading_is_recognized_in_flattened_jd(engine_root: Path) -> None:
    taxonomy = json.loads((engine_root / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text())
    payload = {
        "company": "Parsons",
        "role": "Design Manager - Infrastructure",
        "full_job_description": (
            "The Design Manager will manage and coordinate multidisciplinary design across assigned projects, packages and asset scopes. "
            "Support design delivery across all project stages and coordinate design consultants and technical teams. "
            "Review design submissions, resolve constructability issues and liaise with stakeholders. "
            "What Required Skills You'll Bring; Bachelor’s degree in engineering, or related discipline. "
            "10 years’ experience in design coordination or design management roles. "
            "Experience working with multidisciplinary design consultants. "
            "Good understanding of design development stages and coordination requirements. "
            "Strong organizational, communication and stakeholder coordination skills. "
            "Parsons equally employs representation at all job levels no matter the race, color, religion or sex. "
            "Parsons is aware of fraudulent recruitment practices."
        ),
    }
    normalized = normalize_job(payload, taxonomy)
    priorities = {item["text"]: item["priority"] for item in normalized["requirements"]}
    assert "What Required Skills You'll Bring" not in priorities
    assert priorities["Bachelor’s degree in engineering, or related discipline."] == "mandatory"
    assert priorities["10 years’ experience in design coordination or design management roles."] == "mandatory"
    assert priorities["Experience working with multidisciplinary design consultants."] == "mandatory"
    assert not any(text.startswith("Parsons equally employs") for text in priorities)
    assert not any(text.startswith("Parsons is aware of fraudulent recruitment") for text in priorities)


def test_markdown_decorated_requirement_and_desired_headings_are_recognized(engine_root: Path) -> None:
    taxonomy = json.loads((engine_root / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text())
    payload = {
        "company": "Example",
        "role": "Senior Design Manager",
        "full_job_description": """
### Responsibilities
- Lead multidisciplinary architectural delivery.

**Requirements**
- 15+ years of architectural design management experience.
- Bachelor's degree in Architecture.

### What Desired Skills You'll Bring
- LEED accreditation.
""",
    }
    normalized = normalize_job(payload, taxonomy)
    priorities = {item["text"]: item["priority"] for item in normalized["requirements"]}
    assert priorities["15+ years of architectural design management experience."] == "mandatory"
    assert priorities["Bachelor's degree in Architecture."] == "mandatory"
    assert priorities["LEED accreditation."] == "preferred"


def test_emoji_decorated_requirements_heading_is_recognized(engine_root: Path) -> None:
    taxonomy = json.loads((engine_root / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text())
    payload = {
        "company": "Example",
        "role": "Architecture Project Manager",
        "full_job_description": """
Architecture Project Manager - Riyadh
🌟 Requirements:
🔹 Bachelor degree in Architectural Engineering.
🔹 Minimum 15 years of relevant professional experience.
🔹 Proven experience managing large-scale construction projects.
""",
    }
    normalized = normalize_job(payload, taxonomy)
    priorities = {item["text"]: item["priority"] for item in normalized["requirements"]}
    assert priorities["🔹 Bachelor degree in Architectural Engineering."] == "mandatory"
    assert priorities["🔹 Minimum 15 years of relevant professional experience."] == "mandatory"
    assert priorities["🔹 Proven experience managing large-scale construction projects."] == "mandatory"
    assert "🌟 Requirements:" not in normalized["ambiguous_clauses"]


def test_bundle_rebuilds_when_vault_source_changes(engine_root: Path) -> None:
    first = build_bundle(engine_root)
    reused = build_bundle(engine_root)
    assert reused["bundle_hash"] == first["bundle_hash"]
    assert reused["cache_reused"] is True
    source = engine_root.parent / "vault/projects/job-automation/playbooks/career-engine-application-playbook.md"
    source.write_text(source.read_text() + "\nUpdated rule.", encoding="utf-8")
    status = bundle_status(engine_root)
    assert status["current"] is False
    rebuilt = build_bundle(engine_root)
    assert rebuilt["bundle_hash"] != first["bundle_hash"]


def test_daily_scanner_policy_does_not_define_inference_routing(engine_root: Path) -> None:
    config = json.loads(
        (engine_root / "projects/job-automation/config/career-engine.v1.json").read_text(encoding="utf-8")
    )
    scanner = config["daily_scanner"]
    assert scanner["minimum_score_for_generation"] == 70
    assert scanner["send_or_submit"] is False
    assert "provider" not in scanner
    assert "model" not in scanner


def test_it_infrastructure_jd_is_suppressed_as_out_of_lane(engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    payload = {
        "company": "Example",
        "role": "Manager - Asset Infrastructure Design",
        "location": "Riyadh, Saudi Arabia",
        "application_url": "https://example.com/apply",
        "full_job_description": """
Responsibilities
- Lead enterprise architecture and technology assets across the organization.
Requirements
- Bachelor's degree in Information Technology, Computer Science, Information Systems, or Network Engineering.
- Strong experience with IT infrastructure, cybersecurity, and network architecture.
""",
    }
    normalized = normalize_job(payload, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    score = score_fit(normalized, matches, bundle)
    assert score["total"] < 70
    assert score["calibration"]["jd_out_of_lane"] == "technology_infrastructure"


def test_alias_matching_and_scoring(job_payload: dict[str, str], engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    normalized = normalize_job(job_payload, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    assert any(match["status"] == "matched" and "claim.design" in match["claim_ids"] for match in matches)
    score = score_fit(normalized, matches, bundle)
    assert 0 <= score["total"] <= 100
    assert score["recommendation"] in {"high_priority", "credible", "selective", "weak"}


def test_route_requires_verified_real_recipient_or_portal(engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    live_fields = {
        "live_status": "live",
        "live_verified_at": "2026-08-03T10:00:00+00:00",
        "live_verification_source": "official vacancy page",
    }
    base = {"recipient": "", "recipient_source": "", "application_url": "https://example.com/apply", **live_fields}
    assert decide_route(base, bundle)["route"] == "portal"
    aggregator = {"recipient": "", "recipient_source": "", "application_url": "https://4dayweek.io/job/example", **live_fields}
    assert decide_route(aggregator, bundle)["route"] == "unresolved"
    unverified = {"recipient": "", "recipient_source": "", "application_url": "https://example.com/apply", "live_status": "unverified"}
    assert decide_route(unverified, bundle)["route"] == "portal"
    self_address = {"recipient": "hameedo@gmail.com", "recipient_source": "vacancy", "application_url": "", **live_fields}
    assert decide_route(self_address, bundle)["route"] == "unresolved"
    missing_source = {"recipient": "jobs@example.com", "recipient_source": "", "application_url": "", **live_fields}
    assert decide_route(missing_source, bundle)["route"] == "unresolved"
    verified = {"recipient": "jobs@example.com", "recipient_source": "official vacancy", "application_url": "", **live_fields}
    assert decide_route(verified, bundle)["route"] == "email"


def test_policy_and_filename_rules(engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    text = "National Bank of Iraq — available to join. H&S. CMP 50 hours. hameedo@gmail.com"
    codes = {item["code"] for item in validate_text(text, bundle)}
    assert {"prohibited_name", "availability", "forbidden_character", "prohibited_term", "cmp_hours", "internal_email_exposed"} <= codes
    pattern = bundle["config"]["policy"]["external_filename_pattern"]
    assert outward_filename("Manager - Design Governance", pattern) == "Abdelhamid_Farah_CV_Manager_Design_Governance.pdf"
    assert outward_filename("Senior Design Manager (Architect - Site/Delivery Experience)", pattern) == "Abdelhamid_Farah_CV_Senior_Design_Manager.pdf"


def test_email_subject_policy_uses_job_instruction_then_fallback(job_payload: dict[str, str], engine_root: Path) -> None:
    bundle = build_bundle(engine_root)

    instructed = dict(job_payload)
    instructed["required_email_subject"] = "REF-204 - Senior Design Manager"
    normalized = normalize_job(instructed, bundle["taxonomy"])
    packet = create_generation_packet(
        job_id="subject-policy-instructed",
        normalized_job=normalized,
        matches=match_evidence(normalized, bundle),
        score=score_fit(normalized, match_evidence(normalized, bundle), bundle),
        route=decide_route(normalized, bundle),
        bundle=bundle,
    )
    assert packet["email_draft_policy"]["expected_subject"] == "REF-204 - Senior Design Manager"
    assert packet["email_draft_policy"]["subject_source"] == "job_description"
    assert packet["email_draft_policy"]["account"] == "hameedo@gmail.com"
    assert packet["email_draft_policy"]["sender"] == "hameedfarah@gmail.com"
    assert packet["email_draft_policy"]["attachment_count"] == 1

    fallback = dict(job_payload)
    fallback.pop("required_email_subject", None)
    fallback.pop("email_subject", None)
    normalized = normalize_job(fallback, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    packet = create_generation_packet(
        job_id="subject-policy-fallback",
        normalized_job=normalized,
        matches=matches,
        score=score_fit(normalized, matches, bundle),
        route=decide_route(normalized, bundle),
        bundle=bundle,
    )
    assert packet["email_draft_policy"]["expected_subject"] == f"Abdelhamid Farah - {normalized['role']}"
    assert packet["email_draft_policy"]["subject_source"] == "fallback"


def valid_application(packet: dict) -> dict:
    claims = [item["id"] for item in packet["selected_claims"]]
    while len(claims) < 6:
        claims.append(packet["selected_metric_claim_ids"][len(claims) % len(packet["selected_metric_claim_ids"])])
    metrics = list(dict.fromkeys(packet["selected_metric_claim_ids"] + claims))[:6]
    return {
        "schema_version": 1,
        "job_id": packet["job_id"],
        "bundle_hash": packet["bundle_hash"],
        "headline": "Design Governance and Technical Delivery Leader",
        "leadership_profile": {
            "text": "Senior architecture and delivery leader combining design governance, multidisciplinary coordination, client leadership and commercial awareness across complex Saudi programmes.",
            "claim_ids": claims[:2]
        },
        "metric_claim_ids": metrics,
        "current_role_bullets": [
            {"text": "Led multidisciplinary design governance and project delivery across complex programmes.", "claim_ids": ["claim.design"]},
            {"text": "Strengthened technical quality and cross-discipline coordination through structured controls.", "claim_ids": ["claim.design"]},
            {"text": "Directed client and stakeholder engagement across concurrent assignments.", "claim_ids": ["claim.client"]},
            {"text": "Aligned design, commercial and programme priorities across the delivery lifecycle.", "claim_ids": ["claim.portfolio"]},
            {"text": "Led multidisciplinary teams across concurrent design and delivery priorities.", "claim_ids": ["claim.team"]},
            {"text": "Applied value engineering and commercial discipline across project delivery decisions.", "claim_ids": ["claim.cost"]},
            {"text": "Maintained accountable governance through measurable delivery controls and reporting.", "claim_ids": ["claim.design"]}
        ],
        "earlier_role_bullets": [
            {"text": "Managed multidisciplinary delivery across documented Cube Architects developments.", "claim_ids": ["cube.projects.25"]},
            {"text": "Directed a multidisciplinary Cube Architects design and engineering team.", "claim_ids": ["cube.team.10plus"]},
            {"text": "Managed technical delivery for major Cube Architects office developments.", "claim_ids": ["cube.assets.office_scale"]},
            {"text": "Negotiated and formalized client and contractor agreements at Cube Architects.", "claim_ids": ["cube.agreements.30plus"]},
            {"text": "Led BIM and internal quality-workflow adoption on relevant Cube work.", "claim_ids": ["cube.bim.workflow.50"]},
            {"text": "Applied value engineering on relevant Cube Architects assignments.", "claim_ids": ["cube.value_engineering.15plus"]},
            {"text": "Managed procurement and tender documentation at Cube Architects.", "claim_ids": ["cube.procurement.tender"]},
            {"text": "Coordinated design documentation and consultant integration as Project Architect.", "claim_ids": ["earlier.cube_project_architect.delivery"]},
            {"text": "Managed a retail branch through design, site supervision and handover.", "claim_ids": ["earlier.cud.smartbuy.750"]},
            {"text": "Prepared RFP and scope packages during the Al-Mehanya role.", "claim_ids": ["earlier.procurement.20plus"]},
            {"text": "Developed coordinated design and tender packages during the Sigma role.", "claim_ids": ["earlier.sigma.design_packages"]}
        ],
        "credential_claim_ids": [item for item in claims if "sce" in item],
        "cover_email": {
            "subject": packet["email_draft_policy"]["expected_subject"],
            "body": "Dear Hiring Manager,\n\nPlease find attached my CV for the Senior Design Governance Manager position. My background combines senior design governance, multidisciplinary delivery, client leadership and commercial oversight across complex Saudi programmes. The attached CV highlights the most relevant evidence and outcomes for the role.\n\nKind regards,\nAbdelhamid Farah",
            "claim_ids": claims[:3]
        },
        "tailoring_rationale": ["Prioritized design governance and delivery leadership."],
        "acknowledged_gaps": []
    }


def test_free_prose_generation_contract_rejects_unsupported_claims(job_payload: dict[str, str], engine_root: Path) -> None:
    bundle = build_bundle(engine_root)
    normalized = normalize_job(job_payload, bundle["taxonomy"])
    matches = match_evidence(normalized, bundle)
    score = score_fit(normalized, matches, bundle)
    route = decide_route(normalized, bundle)
    packet = create_generation_packet(job_id="test-job-123", normalized_job=normalized, matches=matches, score=score, route=route, bundle=bundle)
    application = valid_application(packet)
    assert validate_generated_application(application, packet, bundle) == []
    application["current_role_bullets"][0]["claim_ids"] = ["fabricated.claim"]
    findings = validate_generated_application(application, packet, bundle)
    assert any(item["code"] == "unsupported_claim" for item in findings)

    application = valid_application(packet)
    application["cover_email"]["subject"] = "Application - Wrong Subject"
    findings = validate_generated_application(application, packet, bundle)
    assert any(item["code"] == "email_subject_mismatch" for item in findings)


def test_prepare_is_idempotent_and_uses_tracker(job_payload: dict[str, str], engine_root: Path) -> None:
    first = prepare(job_payload, root=engine_root, actor="system")
    second = prepare_job(job_payload, root=engine_root, actor="system")
    assert first["job_id"] == second["job_id"]
    assert second["cache_reused"]["normalized_job"] is True
    jobs = json.loads((engine_root / "projects/job-automation/data/jobs" / f"{first['job_id']}.json").read_text())
    assert jobs["processing_state"]["bundle_hash"] == first["bundle_hash"]
    assert get_bundle_info(root=engine_root)["bundle_hash"] == first["bundle_hash"]


def test_blocked_role_does_not_create_generation_packet(job_payload: dict[str, str], engine_root: Path) -> None:
    blocked = dict(job_payload)
    blocked["application_url"] = ""
    blocked["recipient"] = ""
    state = prepare(blocked, root=engine_root, actor="system")
    assert state["stage"] == "blocked"
    assert "generation_packet" not in state["outputs"]
    artifact = engine_root / "projects/job-automation/artifacts" / state["job_id"] / "generation_packet.json"
    assert not artifact.exists()


def test_doctor_reports_template_and_bundle(engine_root: Path) -> None:
    build_bundle(engine_root)
    result = doctor(engine_root)
    assert result["valid"] is True, result
    assert result["template"]["present"] is True
    assert result["tracker"]["present"] is True
