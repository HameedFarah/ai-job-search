"""Focused tests for deterministic generation-quality gates.

Covers two production defects with sanitized synthetic data:
1. materially redundant bullets that restate the same evidence, figures, subject
   and outcome without a distinct responsibility or result;
2. role-attribution errors where career-wide or current-role claims appear as
   earlier-role bullets.

It also guards against false positives for legitimate claim reuse across the
profile, metric boxes, a role bullet and the cover email.
"""

from __future__ import annotations

from career_engine.generation import (
    GENERATION_GUIDANCE,
    claim_role_scope,
    create_generation_packet,
    validate_generated_application,
)


def synthetic_claim(claim_id: str, *, attribution: str, label: str = "") -> dict:
    return {
        "id": claim_id,
        "label": label or claim_id,
        "attribution": attribution,
        "confidence": "high",
        "safe_wording": f"Safe wording for {claim_id}.",
    }


def synthetic_bundle(claims: list[dict]) -> dict:
    return {
        "bundle_hash": "test-bundle-hash",
        "config": {
            "identity": {},
            "policy": {
                "metric_box_count": 6,
                "max_numbers_per_bullet": 2,
                "prohibited_experience_names": [],
                "prohibited_terms": [],
                "availability_patterns": [],
                "forbidden_characters": [],
            },
            "generation": {"second_review_conditions": []},
        },
        "claims": claims,
        "career_chronology": [],
    }


def synthetic_packet(bundle: dict, *, selected: list[dict], job_id: str = "test-job") -> dict:
    return {
        "job_id": job_id,
        "bundle_hash": bundle["bundle_hash"],
        "selected_claims": selected,
        "selected_metric_claim_ids": [claim["id"] for claim in selected if claim.get("value")][:6],
        "vacancy": {"requirements": []},
        "email_draft_policy": {
            "account": "hameedo@gmail.com",
            "recipient": "",
            "recipient_source": "",
            "expected_subject": "Abdelhamid Farah - Senior Design Manager",
            "subject_source": "fallback",
            "attachment_count": 1,
            "default_resume_variant": "ats-linear",
            "preview_override_allowed": True,
            "attach_only_selected_resume_variant": True,
        },
    }


def minimal_application(packet: dict, *, current: list[dict], earlier: list[dict]) -> dict:
    cited = [
        claim_id
        for item in [*current, *earlier]
        for claim_id in item.get("claim_ids", [])
    ]
    primary = cited[0] if cited else packet["selected_claims"][0]["id"]
    return {
        "schema_version": 1,
        "job_id": packet["job_id"],
        "bundle_hash": packet["bundle_hash"],
        "headline": "Design and Delivery Leadership for Complex Programmes",
        "leadership_profile": {
            "text": "Senior architecture and delivery leader combining design governance, multidisciplinary coordination and commercial awareness across complex Saudi programmes.",
            "claim_ids": [primary],
        },
        "metric_claim_ids": packet["selected_metric_claim_ids"],
        "current_role_bullets": current,
        "earlier_role_bullets": earlier,
        "credential_claim_ids": [],
        "cover_email": {
            "subject": packet["email_draft_policy"]["expected_subject"],
            "body": "Dear Hiring Manager,\n\nPlease find attached my CV for the Senior Design Manager position. My background combines design governance, multidisciplinary delivery and commercial oversight across complex Saudi programmes.\n\nKind regards,\nAbdelhamid Farah",
            "claim_ids": [primary],
        },
        "tailoring_rationale": ["Prioritized design governance and delivery leadership."],
        "acknowledged_gaps": [],
    }


# ---------------------------------------------------------------------------
# Claim role-scope classification
# ---------------------------------------------------------------------------

def test_claim_role_scope_classification() -> None:
    cases = [
        (synthetic_claim("career.projects.112plus", attribution="career portfolio"), "career"),
        (synthetic_claim("portfolio.saudi.74", attribution="portfolio context"), "career"),
        (synthetic_claim("career.saudi_value.1163b", attribution="portfolio context"), "career"),
        (synthetic_claim("governance.turnaround.42plus", attribution="owner-confirmed conservative estimate"), "current"),
        (synthetic_claim("cost.ve.21_94m", attribution="led programme analysis"), "current"),
        (synthetic_claim("stations.concurrent.10", attribution="led programme delivery"), "current"),
        (synthetic_claim("leadership.ksa_team.26plus", attribution="led workforce, not necessarily all direct reports"), "current"),
        (synthetic_claim("cube.projects.25", attribution="managed or contributed according to role"), "earlier"),
        (synthetic_claim("earlier.procurement.20plus", attribution="prepared"), "earlier"),
        (synthetic_claim("credential.sce.consultant", attribution="current professional classification"), "credential"),
        (synthetic_claim("capability.multidisciplinary.leadership", attribution="personally led or managed through team"), "general"),
    ]
    for claim, expected in cases:
        assert claim_role_scope(claim) == expected, claim["id"]


# ---------------------------------------------------------------------------
# Redundant-bullet gate
# ---------------------------------------------------------------------------

def test_rejects_materially_redundant_bullets() -> None:
    """Same evidence, shared figure and same subject must be rejected."""
    bundle = synthetic_bundle([
        synthetic_claim("cost.ve.21_94m", attribution="led programme analysis"),
        synthetic_claim("stations.concurrent.10", attribution="led programme delivery"),
    ])
    packet = synthetic_packet(bundle, selected=bundle["claims"])
    application = minimal_application(packet, current=[
        {
            "text": "Delivered SAR 21.94 million in verified design-stage savings through value engineering across 10 concurrent service-station packages.",
            "claim_ids": ["cost.ve.21_94m", "stations.concurrent.10"],
        },
        {
            "text": "Administered value management and value engineering across 10 concurrent service-station packages, incorporating constructability, environmental and sustainability considerations.",
            "claim_ids": ["stations.concurrent.10", "cost.ve.21_94m"],
        },
    ], earlier=[])
    findings = validate_generated_application(application, packet, bundle)
    redundant = [item for item in findings if item["code"] == "redundant_bullet"]
    assert redundant, findings
    assert all(item["severity"] == "error" for item in redundant)


def test_same_claim_in_distinct_bullets_without_shared_figures_is_allowed() -> None:
    """Two bullets may cite the same claim when they describe distinct
    responsibilities and share no figures."""
    bundle = synthetic_bundle([
        synthetic_claim("capability.multidisciplinary.leadership", attribution="personally led or managed through team"),
    ])
    packet = synthetic_packet(bundle, selected=bundle["claims"])
    application = minimal_application(packet, current=[
        {"text": "Led multidisciplinary design governance and project delivery across complex programmes.", "claim_ids": ["capability.multidisciplinary.leadership"]},
        {"text": "Strengthened technical quality and cross-discipline coordination through structured controls.", "claim_ids": ["capability.multidisciplinary.leadership"]},
        {"text": "Directed client and stakeholder engagement across concurrent assignments.", "claim_ids": ["capability.multidisciplinary.leadership"]},
    ], earlier=[])
    findings = validate_generated_application(application, packet, bundle)
    assert not [item for item in findings if item["code"] == "redundant_bullet"], findings


# ---------------------------------------------------------------------------
# Role-attribution gate
# ---------------------------------------------------------------------------

def test_rejects_career_wide_claim_in_earlier_role_bullets() -> None:
    """112+ career total must not appear as an earlier-role bullet."""
    bundle = synthetic_bundle([
        synthetic_claim("career.projects.112plus", attribution="career portfolio", label="112+ documented projects and assignments"),
    ])
    packet = synthetic_packet(bundle, selected=bundle["claims"])
    application = minimal_application(packet, current=[], earlier=[
        {
            "text": "Delivered 112+ documented projects and assignments across architecture, design management, consultancy and delivery roles.",
            "claim_ids": ["career.projects.112plus"],
        },
    ])
    findings = validate_generated_application(application, packet, bundle)
    attribution = [item for item in findings if item["code"] == "role_attribution"]
    assert attribution, findings
    assert all(item["severity"] == "error" for item in attribution)


def test_rejects_current_role_claim_in_earlier_role_bullets() -> None:
    bundle = synthetic_bundle([
        synthetic_claim("governance.turnaround.42plus", attribution="owner-confirmed conservative estimate", label="42%+ faster design turnaround"),
    ])
    packet = synthetic_packet(bundle, selected=bundle["claims"])
    application = minimal_application(packet, current=[], earlier=[
        {
            "text": "Improved design turnaround by more than 42% through live work-allocation and issue-control systems.",
            "claim_ids": ["governance.turnaround.42plus"],
        },
    ])
    findings = validate_generated_application(application, packet, bundle)
    assert any(item["code"] == "role_attribution" for item in findings), findings


def test_earlier_role_claims_are_allowed_in_earlier_role_bullets() -> None:
    bundle = synthetic_bundle([
        synthetic_claim("cube.projects.25", attribution="managed or contributed according to role", label="25 Cube Architects developments"),
        synthetic_claim("earlier.procurement.20plus", attribution="prepared", label="20+ earlier RFP, TOR and scope packages"),
    ])
    packet = synthetic_packet(bundle, selected=bundle["claims"])
    application = minimal_application(packet, current=[], earlier=[
        {"text": "Managed architectural and multidisciplinary delivery across 25 documented developments at Cube Architects.", "claim_ids": ["cube.projects.25"]},
        {"text": "Prepared more than 20 RFP, TOR and scope packages during earlier project-architecture roles.", "claim_ids": ["earlier.procurement.20plus"]},
    ])
    findings = validate_generated_application(application, packet, bundle)
    assert not [item for item in findings if item["code"] == "role_attribution"], findings


def test_general_capability_claim_is_allowed_in_earlier_role_bullets() -> None:
    """Role-neutral capability claims keep the evidence source and attribution
    open, so they must not be flagged in earlier-role bullets."""
    bundle = synthetic_bundle([
        synthetic_claim("capability.delivery.end_to_end", attribution="personally led or managed through team depending on assignment"),
    ])
    packet = synthetic_packet(bundle, selected=bundle["claims"])
    application = minimal_application(packet, current=[], earlier=[
        {"text": "Directed end-to-end design and project delivery from briefing through approvals and close-out.", "claim_ids": ["capability.delivery.end_to_end"]},
    ])
    findings = validate_generated_application(application, packet, bundle)
    assert not [item for item in findings if item["code"] == "role_attribution"], findings


# ---------------------------------------------------------------------------
# No false positives for legitimate reuse across sections
# ---------------------------------------------------------------------------

def test_legitimate_reuse_of_one_claim_across_sections_is_not_flagged() -> None:
    """One claim may legitimately appear in the profile, metric boxes, a role
    bullet and the cover email; the quality gates must not flag that."""
    bundle = synthetic_bundle([
        synthetic_claim("governance.turnaround.42plus", attribution="owner-confirmed conservative estimate", label="42%+ faster design turnaround"),
    ])
    packet = synthetic_packet(bundle, selected=bundle["claims"])
    application = minimal_application(packet, current=[
        {"text": "Improved design turnaround by more than 42% through live work-allocation and issue-control systems.", "claim_ids": ["governance.turnaround.42plus"]},
    ], earlier=[])
    application["leadership_profile"]["text"] = "Senior design-governance leader who improved design turnaround by more than 42% through structured live controls."
    application["metric_claim_ids"] = ["governance.turnaround.42plus"]
    findings = validate_generated_application(application, packet, bundle)
    assert not [item for item in findings if item["code"] in {"redundant_bullet", "role_attribution"}], findings


# ---------------------------------------------------------------------------
# Complete sanitized application regression
# ---------------------------------------------------------------------------

def test_sanitized_application_has_no_redundancy_or_attribution_findings() -> None:
    claims = [
        synthetic_claim("capability.multidisciplinary.leadership", attribution="personally led or managed through team"),
        synthetic_claim("cube.projects.25", attribution="managed or contributed according to role"),
    ]
    bundle = synthetic_bundle(claims)
    packet = synthetic_packet(bundle, selected=claims)
    application = minimal_application(
        packet,
        current=[
            {
                "text": "Led multidisciplinary design governance and project delivery across complex programmes.",
                "claim_ids": ["capability.multidisciplinary.leadership"],
            },
            {
                "text": "Strengthened technical quality and cross-discipline coordination through structured controls.",
                "claim_ids": ["capability.multidisciplinary.leadership"],
            },
        ],
        earlier=[
            {
                "text": "Managed architectural and multidisciplinary delivery across 25 documented developments.",
                "claim_ids": ["cube.projects.25"],
            }
        ],
    )
    findings = validate_generated_application(application, packet, bundle)
    assert not [
        item for item in findings
        if item["code"] in {"redundant_bullet", "role_attribution"}
    ], findings


# ---------------------------------------------------------------------------
# Generation guidance wiring
# ---------------------------------------------------------------------------

def test_generation_guidance_is_exposed_in_packet_and_instruction() -> None:
    assert any("Never restate the same evidence" in item for item in GENERATION_GUIDANCE)
    assert any("earlier-role bullets must cite only cube architects" in item.lower() for item in GENERATION_GUIDANCE)
    bundle = synthetic_bundle([])
    bundle["config"]["policy"]["external_filename_pattern"] = "Abdelhamid_Farah_CV_{target_role}.pdf"
    packet = create_generation_packet(
        job_id="job-guidance",
        normalized_job={"role": "Design Manager"},
        matches=[],
        score={"total": 70},
        route={"route": "portal"},
        bundle=bundle,
    )
    assert packet["generation_guidance"] == GENERATION_GUIDANCE
    assert "Avoid repetition" in packet["system_instruction"]
    assert "Respect attribution" in packet["system_instruction"]
