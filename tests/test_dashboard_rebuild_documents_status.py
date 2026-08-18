import inspect
import subprocess
from pathlib import Path

from career_engine.pipeline import prepare
from tools import career_dashboard_assistant as assistant


def test_status_action_is_not_a_lifecycle_stage():
    path = Path("dashboard/career-review/site/assets/bulk-table.js")
    result = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    text = path.read_text(encoding="utf-8")
    assert "↻ Rebuild CV & cover letter" in text
    assert "request_type: 'rebuild_documents'" in text
    assert "[REBUILD_DOCUMENTS]" in text
    assert "stage: REBUILD_DOCUMENTS_ACTION" not in text


def test_prepare_has_explicit_owner_review_route_override():
    assert "allow_unresolved_route_for_owner_review" in inspect.signature(prepare).parameters


def test_rebuild_request_uses_dedicated_backend_before_context_load(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(assistant, "run_rebuild_documents", lambda **kwargs: calls.append(kwargs) or "rebuilt")
    monkeypatch.setattr(assistant, "load_job_context", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not load before rebuild preparation")))
    role_key, answer, metadata = assistant.answer_request(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "site",
        record={"data": {"role_key": "tracker-abcdef1234567890", "request_type": "rebuild_documents", "prompt": ""}},
    )
    assert role_key == "tracker-abcdef1234567890"
    assert answer == "rebuilt"
    assert calls[0]["job_id"] == "abcdef1234567890"
    assert metadata["validation_status"] == "success"


def test_rebuild_existing_package_rerenders_and_republishes(monkeypatch, tmp_path):
    generated = []
    published = []
    monkeypatch.setattr(assistant, "_ensure_rebuild_generation_packet", lambda **kwargs: {"application": {"headline": "Existing"}})
    monkeypatch.setattr(assistant, "_generate_application_package", lambda **kwargs: generated.append(kwargs) or "rendered_existing")
    monkeypatch.setattr(assistant, "_refresh_dashboard_site", lambda repo, website_root: published.append((repo, website_root)))
    answer = assistant.run_rebuild_documents(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "site",
        job_id="abcdef1234567890",
    )
    assert generated[0]["force_regenerate"] is False
    assert published == [(tmp_path, tmp_path / "site")]
    assert "dashboard was republished" in answer
