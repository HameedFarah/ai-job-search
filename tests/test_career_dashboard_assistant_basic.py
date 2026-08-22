import json
import subprocess
from pathlib import Path

import pytest

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
    args = build_parser().parse_args(["run", "--min-score", "75", "--all", "--reprocess-existing"])
    assert args.min_score == 75
    assert args.process_all is True
    assert args.reprocess_existing is True


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

    assert commands == [["run", "--min-score", "65", "--all", "--reprocess-existing"]]
    assert "Score ≥ 65 processing completed" in answer


def test_process_jobs_regenerates_existing_packages_and_reports_progress(monkeypatch, tmp_path):
    generated = []
    progress = []

    monkeypatch.setattr(
        assistant,
        "_run_engine",
        lambda repo, args, timeout: json.dumps({
            "processed": [{"job_id": "abcdef1234567890", "generation_packet": True}],
            "eligible": [{"job_id": "abcdef1234567890", "score": 88}],
        }),
    )
    monkeypatch.setattr(
        assistant,
        "_generate_application_package",
        lambda **kwargs: generated.append(kwargs) or "generated_and_rendered",
    )
    monkeypatch.setattr(
        assistant,
        "load_job_context",
        lambda repo, job_id: {"packet": {"vacancy": {"role": "Senior Design Manager", "company": "Parsons"}}},
    )
    monkeypatch.setattr(assistant, "_refresh_dashboard_site", lambda repo, website_root: None)

    answer = assistant.run_process_jobs(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "site",
        min_score=70,
        progress_callback=progress.append,
    )

    assert generated[0]["force_regenerate"] is True
    assert progress[0]["kind"] == "batch_progress"
    assert any(item["current_role_key"] == "tracker-abcdef1234567890" for item in progress)
    assert progress[-1]["phase"] == "publishing"
    assert "1 package(s) freshly regenerated/rendered" in answer
    assert "0 existing validated package(s) were preserved" in answer


def test_workspace_write_candidate_survives_empty_completion_router_error(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "artifacts" / "abcdef1234567890"
    artifact_dir.mkdir(parents=True)
    monkeypatch.setattr(
        assistant,
        "load_job_context",
        lambda repo, job_id: {"packet": {"job_id": job_id}, "application": {"headline": "Old"}, "artifact_dir": artifact_dir},
    )

    def fake_dispatcher(**kwargs):
        output = kwargs["prompt"].split("OUTPUT PATH:\n", 1)[1].splitlines()[0]
        Path(output).write_text("{}\n", encoding="utf-8")
        raise assistant.AssistantError("model route failed after empty completion fallback")

    monkeypatch.setattr(assistant, "run_dispatcher", fake_dispatcher)
    monkeypatch.setattr(
        assistant,
        "_run_engine",
        lambda repo, args, timeout: json.dumps({"valid": True}),
    )

    action = assistant._generate_application_package(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        job_id="abcdef1234567890",
        force_regenerate=True,
    )
    assert action == "generated_and_rendered"
    assert not list(artifact_dir.glob("dashboard-batch-*.json"))


def test_workspace_write_prompts_request_terminal_acknowledgement(tmp_path):
    prompt = assistant.build_cv_edit_prompt(sample_context(), "Tighten headline", tmp_path / "candidate.json")
    assert "reply exactly DONE" in prompt


def test_generated_contract_metadata_is_stamped_from_packet(tmp_path):
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({
        "schema_version": 99,
        "job_id": "wrong",
        "bundle_hash": "stale",
        "cover_email": {"subject": "Decorated subject", "body": "Body", "claim_ids": []},
    }), encoding="utf-8")
    packet = {
        "schema_version": 1,
        "job_id": "abcdef1234567890",
        "bundle_hash": "current-bundle",
        "email_draft_policy": {"expected_subject": "Abdelhamid Farah - Senior Design Manager"},
    }
    assistant._stamp_generated_application_contract(candidate, packet)
    stamped = json.loads(candidate.read_text(encoding="utf-8"))
    assert stamped["schema_version"] == 1
    assert stamped["job_id"] == "abcdef1234567890"
    assert stamped["bundle_hash"] == "current-bundle"
    assert stamped["cover_email"]["subject"] == "Abdelhamid Farah - Senior Design Manager"
    assert stamped["cover_email"]["body"] == "Body"


def test_generation_gets_one_bounded_repair_after_deterministic_rejection(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "artifacts" / "abcdef1234567890"
    artifact_dir.mkdir(parents=True)
    packet = {
        "schema_version": 1,
        "job_id": "abcdef1234567890",
        "bundle_hash": "bundle-current",
        "email_draft_policy": {"expected_subject": "Abdelhamid Farah - Senior Design Manager"},
    }
    monkeypatch.setattr(
        assistant,
        "load_job_context",
        lambda repo, job_id: {"packet": packet, "application": {}, "artifact_dir": artifact_dir},
    )
    dispatch_prompts = []

    def fake_dispatcher(**kwargs):
        dispatch_prompts.append(kwargs["prompt"])
        output = kwargs["prompt"].split("OUTPUT PATH:\n", 1)[1].splitlines()[0]
        Path(output).write_text(json.dumps({
            "schema_version": 1,
            "job_id": "abcdef1234567890",
            "bundle_hash": "bundle-current",
            "cover_email": {"subject": "Abdelhamid Farah - Senior Design Manager", "body": "Body", "claim_ids": []},
        }), encoding="utf-8")
        return {"ok": True}

    monkeypatch.setattr(assistant, "run_dispatcher", fake_dispatcher)
    import_count = 0

    def fake_engine(repo, args, timeout):
        nonlocal import_count
        if args[:2] == ["generate", "import"]:
            import_count += 1
            if import_count == 1:
                return json.dumps({"valid": False, "findings": [{"code": "redundancy", "message": "Remove repeated claim"}]})
            return json.dumps({"valid": True, "findings": []})
        if args[0] == "render":
            return json.dumps({"valid": True})
        raise AssertionError(args)

    monkeypatch.setattr(assistant, "_run_engine", fake_engine)
    action = assistant._generate_application_package(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        job_id="abcdef1234567890",
        force_regenerate=True,
    )
    assert action == "generated_and_rendered"
    assert import_count == 2
    assert len(dispatch_prompts) == 2
    assert "VALIDATION FINDINGS" in dispatch_prompts[1]
    assert not list(artifact_dir.glob("dashboard-batch-*.json"))


def test_render_qa_failure_uses_the_same_single_bounded_repair(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "artifacts" / "abcdef1234567890"
    artifact_dir.mkdir(parents=True)
    packet = {
        "schema_version": 1,
        "job_id": "abcdef1234567890",
        "bundle_hash": "bundle-current",
        "email_draft_policy": {"expected_subject": "Abdelhamid Farah - Senior Design Manager"},
    }
    monkeypatch.setattr(
        assistant,
        "load_job_context",
        lambda repo, job_id: {"packet": packet, "application": {}, "artifact_dir": artifact_dir},
    )
    dispatch_prompts = []

    def fake_dispatcher(**kwargs):
        dispatch_prompts.append(kwargs["prompt"])
        output = kwargs["prompt"].split("OUTPUT PATH:\n", 1)[1].splitlines()[0]
        Path(output).write_text(json.dumps({
            "schema_version": 1,
            "job_id": "abcdef1234567890",
            "bundle_hash": "bundle-current",
            "cover_email": {"subject": "Abdelhamid Farah - Senior Design Manager", "body": "Body", "claim_ids": []},
        }), encoding="utf-8")
        return {"ok": True}

    monkeypatch.setattr(assistant, "run_dispatcher", fake_dispatcher)
    imports = 0
    renders = 0

    def fake_engine(repo, args, timeout):
        nonlocal imports, renders
        if args[:2] == ["generate", "import"]:
            imports += 1
            return json.dumps({"valid": True, "findings": []})
        if args[0] == "render":
            renders += 1
            if renders == 1:
                raise assistant.AssistantError("Expected exactly one earlier-role bullet citing cube.projects.25, got 2")
            return json.dumps({"valid": True})
        raise AssertionError(args)

    monkeypatch.setattr(assistant, "_run_engine", fake_engine)
    action = assistant._generate_application_package(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        job_id="abcdef1234567890",
        force_regenerate=True,
    )
    assert action == "generated_and_rendered"
    assert imports == 2
    assert renders == 2
    assert len(dispatch_prompts) == 2
    assert "RENDER/QA FAILURE" in dispatch_prompts[1]
    assert not list(artifact_dir.glob("dashboard-batch-*.json"))


def test_failed_fresh_regeneration_preserves_existing_validated_package(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "artifacts" / "abcdef1234567890"
    artifact_dir.mkdir(parents=True)
    packet = {
        "schema_version": 1,
        "job_id": "abcdef1234567890",
        "bundle_hash": "bundle-current",
        "email_draft_policy": {"expected_subject": "Abdelhamid Farah - Senior Design Manager"},
    }
    existing = {
        "schema_version": 1,
        "job_id": "abcdef1234567890",
        "bundle_hash": "stale",
        "cover_email": {"subject": "Old subject", "body": "Existing tailored body", "claim_ids": []},
    }
    monkeypatch.setattr(
        assistant,
        "load_job_context",
        lambda repo, job_id: {"packet": packet, "application": existing, "artifact_dir": artifact_dir},
    )
    monkeypatch.setattr(assistant, "run_dispatcher", lambda **kwargs: {"ok": True})
    calls = []

    def fake_engine(repo, args, timeout):
        calls.append(args)
        if args[:2] == ["generate", "import"]:
            candidate = Path(args[-1])
            saved = json.loads(candidate.read_text(encoding="utf-8"))
            assert saved["bundle_hash"] == "bundle-current"
            assert saved["cover_email"]["subject"] == "Abdelhamid Farah - Senior Design Manager"
            return json.dumps({"valid": True, "findings": []})
        if args[0] == "render":
            return json.dumps({"valid": True})
        raise AssertionError(args)

    monkeypatch.setattr(assistant, "_run_engine", fake_engine)
    action = assistant._generate_application_package(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        job_id="abcdef1234567890",
        force_regenerate=True,
    )
    assert action == "preserved_existing_after_regeneration_failure"
    assert any(args[:2] == ["generate", "import"] for args in calls)
    assert any(args[0] == "render" for args in calls)
    assert not list(artifact_dir.glob("dashboard-batch-*.json"))


def test_dead_direct_claim_owner_is_not_reused(monkeypatch, tmp_path):
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": [{"id": assistant.HERMES_CAREER_CRON_JOB, "fire_claim": {"by": "host:99999999"}}]}), encoding="utf-8")
    monkeypatch.setattr(assistant, "HERMES_CRON_JOBS", jobs)
    assert assistant._hermes_direct_claim_owner_alive() is False


def test_refresh_dashboard_republishes_here_now_and_private_cloudflare_worker(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    website_root = repo / "dashboard" / "career-review"
    website_root.mkdir(parents=True)
    engine_calls = []
    command_calls = []

    monkeypatch.setattr(
        assistant,
        "_run_engine",
        lambda call_repo, args, timeout=240: engine_calls.append((call_repo, args, timeout)),
    )
    monkeypatch.setattr(
        assistant,
        "_run_command",
        lambda command, cwd, timeout=240: command_calls.append((command, cwd, timeout)),
    )

    assistant._refresh_dashboard_site(repo, website_root)

    assert engine_calls == [(repo, ["dashboard", "--sync"], 180)]
    assert command_calls[0] == (["/usr/bin/node", "scripts/build_site.js"], website_root, 300)
    assert command_calls[1] == (["/usr/bin/node", "scripts/publish_here_now.js"], website_root, assistant.PUBLISH_TIMEOUT_SECONDS)
    assert command_calls[2] == (
        [
            "/home/hameedo/vps-infra-dev/scripts/operations/cloudflare-with-infisical-runtime.sh",
            "node",
            str(website_root / "scripts" / "deploy_cloudflare_access.js"),
            "--deploy",
        ],
        repo,
        900,
    )


def test_refresh_dashboard_publish_timeout_remains_terminal(monkeypatch, tmp_path):
    def fail_publish(command, cwd, timeout=240):
        if command[-1].endswith("publish_here_now.js"):
            raise subprocess.TimeoutExpired(command, timeout)
        return None

    monkeypatch.setattr(assistant, "_run_engine", lambda *args, **kwargs: None)
    monkeypatch.setattr(assistant, "_run_command", fail_publish)

    with pytest.raises(subprocess.TimeoutExpired):
        assistant._refresh_dashboard_site(tmp_path, tmp_path / "site")


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


def test_submission_confirmation_archives_exact_resume_and_cover(monkeypatch, tmp_path):
    job_id = "abcdef1234567890"
    artifact_dir = tmp_path / "projects/job-automation/artifacts" / job_id
    artifact_dir.mkdir(parents=True)
    resume_pdf = artifact_dir / "Abdelhamid_Farah_CV_Test_ATS.pdf"
    resume_docx = artifact_dir / "Abdelhamid_Farah_CV_Test_ATS.docx"
    cover_pdf = artifact_dir / "Abdelhamid_Farah_Cover_Letter_Test.pdf"
    cover_docx = artifact_dir / "Abdelhamid_Farah_Cover_Letter_Test.docx"
    resume_pdf.write_bytes(b"resume-pdf-exact")
    resume_docx.write_bytes(b"resume-docx-exact")
    cover_pdf.write_bytes(b"cover-pdf-exact")
    cover_docx.write_bytes(b"cover-docx-exact")
    resume_sha = assistant._sha256_path(resume_pdf)
    cover_sha = assistant._sha256_path(cover_pdf)
    monkeypatch.setattr(assistant, "_pdf_text", lambda path: f"TEXT::{path.name}")
    record = {
        "id": "history-123",
        "createdAt": "2026-08-14T08:00:00+00:00",
        "data": {
            "role_key": f"tracker-{job_id}",
            "event": "application_submitted",
            "submitted_at": "2026-08-14T08:00:00+00:00",
            "template_id": "ats-classic",
            "document_sha256": resume_sha,
            "note": json.dumps({
                "job_id": job_id,
                "company": "Example Co",
                "role": "Senior Design Manager",
                "route": "portal",
                "application_url": "https://example.com/apply",
                "submitted_at": "2026-08-14T08:00:00+00:00",
                "template_id": "ats-classic",
                "document_sha256": resume_sha,
                "cover_letter_sha256": cover_sha,
            }),
        },
    }

    status, manifest_path = assistant._archive_submission_record(repo=tmp_path, record=record)
    assert status == "archived"
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert manifest["resume"]["sha256"] == resume_sha
    assert manifest["resume"]["text"] == "TEXT::submitted_resume.pdf"
    assert manifest["cover_letter"]["sha256"] == cover_sha
    assert manifest["cover_letter"]["text"] == "TEXT::submitted_cover_letter.pdf"
    assert (Path(manifest_path).parent / "submitted_resume.pdf").read_bytes() == b"resume-pdf-exact"
    assert (Path(manifest_path).parent / "submitted_resume.docx").read_bytes() == b"resume-docx-exact"
    assert (Path(manifest_path).parent / "submitted_cover_letter.pdf").read_bytes() == b"cover-pdf-exact"

    second_status, second_manifest = assistant._archive_submission_record(repo=tmp_path, record=record)
    assert second_status == "existing"
    assert second_manifest == manifest_path


def test_submission_confirmation_fails_closed_without_exact_hash(tmp_path):
    job_id = "abcdef1234567890"
    artifact_dir = tmp_path / "projects/job-automation/artifacts" / job_id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "Abdelhamid_Farah_CV_Test_ATS.pdf").write_bytes(b"different-resume")
    record = {
        "id": "history-456",
        "data": {
            "role_key": f"tracker-{job_id}",
            "event": "application_submitted",
            "document_sha256": "a" * 64,
            "note": "{}",
        },
    }
    status, reason = assistant._archive_submission_record(repo=tmp_path, record=record)
    assert status == "unresolved"
    assert reason == f"submitted_resume_hash_not_found:{'a' * 64}"
    assert not (artifact_dir / "submissions" / "history-456" / "submission_manifest.json").exists()
