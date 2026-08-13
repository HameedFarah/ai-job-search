from pathlib import Path

from tools import career_dashboard_assistant as assistant


def sample_context():
    return {
        "packet": {
            "vacancy": {"role": "Senior Design Manager", "full_job_description": "Lead design governance."},
            "fit_evaluation": {"total": 82},
            "application_route": {"route": "portal"},
            "selected_claims": [{"id": "leadership.team.25", "safe_wording": "Managed 25+ staff."}],
            "owner_questions": [],
        },
        "application": {"headline": "Senior Design Management Leader"},
        "tracker": {},
        "artifact_dir": Path("/tmp/example"),
    }


def test_job_key_and_quick_field_prompt():
    assert assistant.job_id_from_role_key("tracker-abcdef1234567890") == "abcdef1234567890"
    assert "Headline" in assistant.field_prompt("headline", "Keep it concise.")


def test_answer_prompt_uses_current_job_and_resume():
    prompt = assistant.build_answer_prompt(sample_context(), field_name="summary", user_prompt="Maximum 300 characters.")
    assert "ONLY the supplied current validated resume/application content" in prompt
    assert "Lead design governance" in prompt
    assert "Senior Design Management Leader" in prompt
