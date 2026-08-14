import json
from pathlib import Path

from career_engine.cli import build_parser
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


def test_run_parser_supports_owner_batch_threshold():
    args = build_parser().parse_args(["run", "--min-score", "75", "--all"])
    assert args.min_score == 75
    assert args.process_all is True


def test_global_refresh_request_bypasses_job_key(monkeypatch, tmp_path):
    monkeypatch.setattr(assistant, "run_refresh_jobs", lambda **kwargs: "refresh complete")
    role_key, answer, metadata = assistant.answer_request(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path,
        record={"data": {"role_key": assistant.GLOBAL_ROLE_KEY, "request_type": "refresh_jobs", "prompt": ""}},
    )
    assert role_key == assistant.GLOBAL_ROLE_KEY
    assert answer == "refresh complete"
    assert metadata["validation_status"] == "success"


def test_global_process_request_passes_owner_score_override(monkeypatch, tmp_path):
    captured = {}
    def fake_process(**kwargs):
        captured.update(kwargs)
        return "batch complete"
    monkeypatch.setattr(assistant, "run_process_jobs", fake_process)
    _, answer, _ = assistant.answer_request(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path,
        record={"data": {"role_key": assistant.GLOBAL_ROLE_KEY, "request_type": "process_jobs", "min_score": 65}},
    )
    assert answer == "batch complete"
    assert captured["min_score"] == 65


def test_process_jobs_uses_owner_threshold_below_default(monkeypatch, tmp_path):
    commands = []
    def fake_engine(repo, args, timeout):
        commands.append(args)
        return json.dumps({"processed": [], "eligible": []})
    monkeypatch.setattr(assistant, "_run_engine", fake_engine)
    monkeypatch.setattr(assistant, "_refresh_dashboard_site", lambda repo, website_root: None)

    answer = assistant.run_process_jobs(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "site",
        min_score=65,
    )

    assert commands == [["run", "--min-score", "65", "--all"]]
    assert "Score ≥ 65 processing completed" in answer


def test_dead_direct_claim_owner_is_not_reused(monkeypatch, tmp_path):
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": [{"id": assistant.HERMES_CAREER_CRON_JOB, "fire_claim": {"by": "host:99999999"}}]}), encoding="utf-8")
    monkeypatch.setattr(assistant, "HERMES_CRON_JOBS", jobs)
    assert assistant._hermes_direct_claim_owner_alive() is False


def test_refresh_reuses_existing_running_hermes_scan(monkeypatch, tmp_path):
    executable = tmp_path / "hermes"
    executable.write_text("", encoding="utf-8")
    statuses = iter([("run-1", "running"), ("run-1", "completed")])
    refreshed = []
    monkeypatch.setattr(assistant, "HERMES_EXECUTABLE", executable)
    monkeypatch.setattr(assistant, "_hermes_direct_claim_owner_alive", lambda: True)
    monkeypatch.setattr(assistant, "_latest_hermes_run", lambda repo: next(statuses))
    monkeypatch.setattr(assistant.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(assistant, "_refresh_dashboard_site", lambda repo, website_root: refreshed.append((repo, website_root)))
    monkeypatch.setattr(assistant.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate Hermes scan triggered")))

    answer = assistant.run_refresh_jobs(repo=tmp_path, website_root=tmp_path / "site")

    assert "run-1" in answer
    assert "reused existing" in answer
    assert refreshed == [(tmp_path, tmp_path / "site")]
