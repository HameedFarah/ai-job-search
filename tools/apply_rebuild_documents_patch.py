from pathlib import Path

pipeline = Path("career_engine/pipeline.py")
text = pipeline.read_text(encoding="utf-8")
old = 'def prepare(payload: dict[str, Any], *, root: Path | None = None, actor: str = "chatgpt", force_weak: bool = False) -> dict[str, Any]:'
new = '''def prepare(\n    payload: dict[str, Any],\n    *,\n    root: Path | None = None,\n    actor: str = "chatgpt",\n    force_weak: bool = False,\n    allow_unresolved_route_for_owner_review: bool = False,\n) -> dict[str, Any]:'''
if old not in text:
    raise SystemExit("prepare signature not found")
text = text.replace(old, new, 1)
route_old = 'if normalized.get("source") in {"owner_dashboard", "Supplied directly by owner"}:'
route_new = 'if normalized.get("source") in {"owner_dashboard", "Supplied directly by owner"} or allow_unresolved_route_for_owner_review:'
if route_old not in text:
    raise SystemExit("unresolved route gate not found")
text = text.replace(route_old, route_new, 1)
pipeline.write_text(text, encoding="utf-8")

worker = Path("tools/career_dashboard_assistant.py")
text = worker.read_text(encoding="utf-8")
if "def run_rebuild_documents(" in text:
    raise SystemExit("rebuild helper already exists unexpectedly")
insert_at = text.index("\ndef apply_cv_edit(")
helper = r'''

def _ensure_rebuild_generation_packet(*, repo: Path, job_id: str) -> dict[str, Any]:
    try:
        return load_job_context(repo, job_id)
    except AssistantError as exc:
        if str(exc) != "generation_packet_missing":
            raise

    # A rebuild is an explicit owner request. Reuse the canonical preparation
    # pipeline for this one existing tracker record, while allowing an internal
    # review package when the employer route is unresolved. Closed vacancies
    # remain blocked by prepare(). No external action is enabled here.
    from career_engine.ops import _load_tracker_ops, _payload_from_record
    from career_engine.pipeline import prepare

    tracker = _load_tracker_ops(repo)
    record = tracker.get_job(job_id)
    state = prepare(
        _payload_from_record(record, repo),
        root=repo,
        actor="owner",
        force_weak=True,
        allow_unresolved_route_for_owner_review=True,
    )
    if not state.get("outputs", {}).get("generation_packet"):
        blockers = ", ".join(str(item) for item in state.get("blockers", [])) or "generation packet unavailable"
        raise AssistantError(f"document rebuild preparation blocked: {blockers}")
    return load_job_context(repo, job_id)


def run_rebuild_documents(
    *,
    repo: Path,
    dispatcher: Path,
    website_root: Path,
    job_id: str,
) -> str:
    _ensure_rebuild_generation_packet(repo=repo, job_id=job_id)
    try:
        # Prefer deterministic rerendering when validated application content
        # already exists. Only regenerate prose when content is missing or the
        # existing package cannot render successfully.
        action = _generate_application_package(
            repo=repo,
            dispatcher=dispatcher,
            job_id=job_id,
            force_regenerate=False,
        )
    except AssistantError:
        action = _generate_application_package(
            repo=repo,
            dispatcher=dispatcher,
            job_id=job_id,
            force_regenerate=True,
        )
    _refresh_dashboard_site(repo, website_root)
    return (
        f"CV and cover letter rebuilt ({action}) and the dashboard was republished. "
        "External action remains blocked pending owner submission."
    )
'''
text = text[:insert_at] + helper + text[insert_at:]
old = '''    job_id = job_id_from_role_key(role_key)\n    context = load_job_context(repo, job_id)\n    if request_type in {"edit_cv", "revise_cv", "resume_edit"}:'''
new = '''    job_id = job_id_from_role_key(role_key)\n    if request_type == "rebuild_documents":\n        answer = run_rebuild_documents(\n            repo=repo,\n            dispatcher=dispatcher,\n            website_root=website_root,\n            job_id=job_id,\n        )\n        return role_key, answer, {\n            "validation_status": "success",\n            "owner_input_needed": False,\n        }\n\n    context = load_job_context(repo, job_id)\n    if request_type in {"edit_cv", "revise_cv", "resume_edit"}:'''
if old not in text:
    raise SystemExit("answer_request insertion point not found")
text = text.replace(old, new, 1)
worker.write_text(text, encoding="utf-8")

js = Path("dashboard/career-review/site/assets/bulk-table.js")
text = js.read_text(encoding="utf-8")
if "/* ---- Per-job document rebuild status action ---- */" not in text:
    raise SystemExit("frontend rebuild action missing")
text = text.replace("request_type: 'edit_cv',\n      prompt: REBUILD_DOCUMENTS_PROMPT,", "request_type: 'rebuild_documents',\n      prompt: REBUILD_DOCUMENTS_PROMPT,", 1)
text = text.replace("&& data.request_type === 'edit_cv'", "&& data.request_type === 'rebuild_documents'", 1)
js.write_text(text, encoding="utf-8")

test = Path("tests/test_dashboard_rebuild_documents_status.py")
test.write_text(r'''import inspect
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
''', encoding="utf-8")
