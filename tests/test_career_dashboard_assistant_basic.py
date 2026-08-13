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


def test_request_type_field_mapping_and_owner_input_detection():
    assert assistant.REQUEST_TYPE_FIELDS["application_question"] == "screening_question"
    assert assistant.field_prompt("screening_question", "")
    assert assistant.owner_input_needed("Owner input is required for the notice period.") is True
    assert assistant.owner_input_needed("I cannot verify that claim from the resume.") is False


def test_field_prefix_remains_compatible():
    prompt = assistant.field_prompt("cover_letter", "Keep it concise.")
    assert "cover letter" in prompt.lower()


def test_load_api_key_falls_back_to_local_credentials(tmp_path, monkeypatch):
    credentials = tmp_path / ".herenow" / "credentials"
    credentials.parent.mkdir()
    credentials.write_text("test-local-key\n", encoding="utf-8")
    monkeypatch.delenv("HERENOW_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert assistant.load_api_key("HERENOW_API_KEY") == "test-local-key"
