import json
from pathlib import Path

from career_engine.core import _role_title_signals, domain_requirement_gate, score_fit
from career_engine.sources.dedupe import stable_vacancy_identity


def taxonomy():
    path = Path(__file__).parents[1] / "projects/job-automation/config/requirements-taxonomy.v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_functional_title_calibration_and_positive_controls():
    tx = taxonomy()
    assert _role_title_signals("Fraud Investigator", tx)["out_of_lane"]
    assert _role_title_signals("Revit Draftsperson", tx)["production"]
    assert _role_title_signals("Lighting SME", tx)["out_of_lane"]
    assert _role_title_signals("Contract Adiministrator", tx)["out_of_lane"]
    assert _role_title_signals("Business Development Manager", tx)["out_of_lane"]
    mep = _role_title_signals("Construction Manager (MEP)", tx)
    assert mep["out_of_lane"] and not mep["target_management"]
    for title in ("Architectural Design Manager", "Design Manager - Interior Design", "Project Manager", "Director of Projects", "Construction Manager", "Construction Lead"):
        signals = _role_title_signals(title, tx)
        assert not signals["out_of_lane"], title
        assert signals["has_management"], title
    assistant_director = _role_title_signals("Assistant Construction Director", tx)
    assert assistant_director["has_management"]
    assert not assistant_director["junior"]
    assert _role_title_signals("Assistant Architect", tx)["junior"]


def _minimal_bundle(tx):
    return {
        "config": {
            "scoring": {
                "weights": {
                    "mandatory_coverage": 42,
                    "preferred_coverage": 13,
                    "leadership": 15,
                    "seniority": 12,
                    "sector": 8,
                    "geography": 5,
                    "credentials": 5,
                },
                "thresholds": {"high_priority": 70, "credible": 65, "selective": 50, "weak": 0},
                "llm_adjustment_ceiling": 5,
            }
        },
        "taxonomy": tx,
        "identity": {"nationalities": ["Jordanian", "Brazilian"]},
        "claims": [],
    }


def _score(role, jd):
    tx = taxonomy()
    job = {
        "role": role,
        "full_job_description": jd,
        "location": "Riyadh, Saudi Arabia",
        "requirements": [],
    }
    return score_fit(job, [], _minimal_bundle(tx))


def test_software_engineering_manager_is_not_built_environment_high_priority():
    score = _score(
        "Engineering Manager - I",
        "Lead a software engineering team building Java microservices on Kubernetes with CI/CD and AWS cloud platform delivery.",
    )
    assert score["calibration"]["out_of_lane"] is True
    assert score["calibration"]["jd_out_of_lane"] == "specialist_delivery_domain"
    assert score["total"] < 50


def test_rail_construction_management_is_specialist_without_rail_evidence():
    tx = taxonomy()
    assert domain_requirement_gate("Minimum 15 years of experience in rail construction", tx) == "rail_transit"
    score = _score(
        "Construction Lead",
        "Lead rail construction delivery for a major metro programme and coordinate rail infrastructure contractors.",
    )
    assert score["calibration"]["out_of_lane"] is True
    assert score["total"] < 50


def test_stable_requisition_identity_is_cross_source_but_not_title_only():
    official = stable_vacancy_identity(company="Parsons Corporation", external_job_id="synthetic-1", source_url="https://parsons.wd1.myworkdayjobs.com/job/Architectural-Design-Manager_R184427")
    linkedin = stable_vacancy_identity(company="Parsons", external_job_id="965160b663c7355c132c", source_url="https://www.linkedin.com/jobs/view/4451585539")
    assert official == "parsons|r184427"
    assert linkedin == ""
    assert stable_vacancy_identity(company="WSP in Africa", external_job_id="93327", source_url="https://wsp.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs/93327") == "wsp|93327"
    assert stable_vacancy_identity(company="WSP", external_job_id="93327", source_url="https://wsp.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs/93327") == "wsp|93327"
    assert stable_vacancy_identity(company="WSP", external_job_id="93320", source_url="https://wsp.oraclecloud.com/jobs/93320") != stable_vacancy_identity(company="WSP", external_job_id="93332", source_url="https://wsp.oraclecloud.com/jobs/93332")
