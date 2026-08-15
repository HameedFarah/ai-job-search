from tools import career_dashboard_add_job as add_job
from tools import career_dashboard_assistant as assistant


def test_add_job_request_dispatches_to_intake_worker(monkeypatch, tmp_path):
    captured = {}

    def fake_run_add_job(**kwargs):
        captured.update(kwargs)
        return "abcdef1234567890", "Added and packaged."

    monkeypatch.setattr(add_job, "run_add_job", fake_run_add_job)
    role_key, answer, metadata = assistant.answer_request(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "dashboard",
        record={
            "data": {
                "role_key": assistant.ADD_JOB_ROLE_KEY,
                "request_type": "add_job",
                "job_description": "A sufficiently detailed owner supplied vacancy description.",
                "company": "Example",
                "role": "Design Manager",
            }
        },
        progress_callback=lambda progress: None,
    )

    assert role_key == "tracker-abcdef1234567890"
    assert answer == "Added and packaged."
    assert metadata == {"validation_status": "success", "owner_input_needed": False}
    assert captured["repo"] == tmp_path
    assert captured["generate_package"] is assistant._generate_application_package
    assert captured["refresh_dashboard"] is assistant._refresh_dashboard_site
