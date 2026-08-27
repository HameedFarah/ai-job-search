from __future__ import annotations

import json
from types import SimpleNamespace

from career_engine import pipeline


class FakeTracker:
    def __init__(self, record=None):
        self.updated = None
        self.record = record

    def get_job(self, job_id):
        return self.record or {"job": {"job_id": job_id}, "processing_state": {}, "generated_artifacts": []}

    def update_job(self, job_id, fields, **kwargs):
        self.updated = {"job_id": job_id, "fields": fields, "kwargs": kwargs}


def test_finalize_render_requires_cover_letter_for_portal(monkeypatch, tmp_path):
    job_id = "abcdef1234567890"
    artifact_dir = tmp_path / "artifacts" / job_id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "generated_application.json").write_text(json.dumps({"headline": "Senior Design Manager"}))
    (artifact_dir / "generation_packet.json").write_text(json.dumps({
        "bundle_hash": "bundle",
        "application_route": {"route": "portal"},
        "email_draft_policy": {"default_resume_variant": "ats-linear"},
    }))
    tracker = FakeTracker()
    monkeypatch.setattr(pipeline, "load_config", lambda root=None: ({}, SimpleNamespace(tracker_base=tmp_path)))
    monkeypatch.setattr(pipeline, "load_bundle", lambda root=None: {"bundle_hash": "bundle"})
    monkeypatch.setattr(pipeline, "validate_generated_application", lambda application, packet, bundle: [])
    monkeypatch.setattr(pipeline, "_load_tracker", lambda paths: tracker)
    monkeypatch.setattr(pipeline, "render_and_verify", lambda *a, **k: {
        "valid": True,
        "docx": {"docx": "/tmp/sidebar.docx", "sha256": "sidebar-docx"},
        "verification": {"pdf": "/tmp/sidebar.pdf", "sha256": "sidebar-pdf"},
    })
    monkeypatch.setattr(pipeline, "render_ats_and_verify", lambda *a, **k: {
        "valid": True,
        "docx": {"docx": "/tmp/ats.docx", "sha256": "ats-docx"},
        "verification": {"pdf": "/tmp/ats.pdf", "sha256": "ats-pdf"},
    })
    monkeypatch.setattr(pipeline, "render_cover_letter_and_verify", lambda *a, **k: {
        "valid": True,
        "docx": {"docx": "/tmp/cover.docx", "sha256": "cover-docx"},
        "verification": {"pdf": "/tmp/cover.pdf", "sha256": "cover-pdf"},
    })

    result = pipeline.finalize_render(job_id, root=tmp_path)

    assert result["valid"] is True
    assert result["cover_letter"]["valid"] is True
    assert result["submission_package"]["selected_resume_variant"] == "ats-linear"
    assert result["submission_package"]["cover_letter_pdf"] == "/tmp/cover.pdf"
    assert any(item["type"] == "cover_letter_pdf" for item in tracker.updated["fields"]["generated_artifacts"])
    assert tracker.updated["fields"]["processing_status"] == "awaiting_owner_approval"


def test_failed_render_restores_persisted_revision_not_latest_directory(tmp_path):
    artifact_dir = tmp_path / "artifacts" / "job"
    artifact_dir.mkdir(parents=True)
    older = artifact_dir / "revisions" / "zzzz"
    selected = artifact_dir / "revisions" / "aaaa"
    for revision, value in ((older, "wrong"), (selected, "right")):
        revision.mkdir(parents=True)
        (revision / "generated_application.json").write_text(value)
        (revision / "manifest.json").write_text(json.dumps({"files": [{"path": "generated_application.json"}]}))
    restored = pipeline._restore_revision(artifact_dir, "aaaa")
    assert restored["revision_id"] == "aaaa"
    assert (artifact_dir / "generated_application.json").read_text() == "right"


def test_successful_import_preserves_complete_prior_package(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "artifacts" / "job"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "generation_packet.json").write_text(json.dumps({}))
    (artifact_dir / "generated_application.json").write_text(json.dumps({"headline": "old"}))
    filenames = [
        "selected.docx", "selected.pdf", "alternate.docx", "alternate.pdf",
        "cover.docx", "cover.pdf", "render_input.json", "pipeline_state.json",
    ]
    for name in filenames:
        (artifact_dir / name).write_text(f"old-{name}")
    artifacts = [
        {"type": "final_docx", "path": str(artifact_dir / "selected.docx")},
        {"type": "final_pdf", "path": str(artifact_dir / "selected.pdf")},
        {"type": "ats_docx", "path": str(artifact_dir / "alternate.docx")},
        {"type": "ats_pdf", "path": str(artifact_dir / "alternate.pdf")},
        {"type": "cover_letter_docx", "path": str(artifact_dir / "cover.docx")},
        {"type": "cover_letter_pdf", "path": str(artifact_dir / "cover.pdf")},
        {"type": "render_input", "path": str(artifact_dir / "render_input.json")},
    ]
    record = {
        "job": {"job_id": "job"},
        "processing_state": {"status": "awaiting_owner_approval", "external_action_allowed": False},
        "generated_artifacts": artifacts,
        "submission_package": {
            "selected_cv_docx": str(artifact_dir / "selected.docx"),
            "selected_cv_pdf": str(artifact_dir / "selected.pdf"),
            "cover_letter_docx": str(artifact_dir / "cover.docx"),
            "cover_letter_pdf": str(artifact_dir / "cover.pdf"),
        },
    }
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"headline": "new"}))
    monkeypatch.setattr(pipeline, "load_bundle", lambda root=None: {})
    monkeypatch.setattr(pipeline, "load_config", lambda root=None: ({}, SimpleNamespace(tracker_base=tmp_path)))
    monkeypatch.setattr(pipeline, "validate_generated_application", lambda *a: [])
    tracker = FakeTracker(record)
    monkeypatch.setattr(pipeline, "_load_tracker", lambda paths: tracker)

    result = pipeline.import_generated("job", candidate, root=tmp_path)

    assert result["valid"] is True
    assert json.loads((artifact_dir / "generated_application.json").read_text())["headline"] == "new"
    revisions = list((artifact_dir / "revisions").iterdir())
    assert len(revisions) == 1
    revision = revisions[0]
    assert json.loads((revision / "generated_application.json").read_text())["headline"] == "old"
    for name in filenames:
        assert (revision / name).read_text() == f"old-{name}"
    assert tracker.updated["fields"]["processing_state"]["pending_revision_id"] == revision.name


def test_rejected_import_does_not_create_revision_or_change_current_package(monkeypatch, tmp_path):
    artifact_dir = tmp_path / "artifacts" / "job"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "generation_packet.json").write_text(json.dumps({}))
    current = artifact_dir / "generated_application.json"
    current.write_text(json.dumps({"headline": "keep"}))
    existing_pdf = artifact_dir / "selected.pdf"
    existing_pdf.write_text("keep-pdf")
    application = tmp_path / "application.json"
    application.write_text(json.dumps({"headline": "rejected"}))
    monkeypatch.setattr(pipeline, "load_bundle", lambda root=None: {})
    monkeypatch.setattr(pipeline, "load_config", lambda root=None: ({}, SimpleNamespace(tracker_base=tmp_path)))
    monkeypatch.setattr(pipeline, "validate_generated_application", lambda *a: [{"severity": "error", "message": "bad"}])
    tracker = FakeTracker()
    monkeypatch.setattr(pipeline, "_load_tracker", lambda paths: tracker)
    result = pipeline.import_generated("job", application, root=tmp_path)
    assert result["valid"] is False
    assert json.loads(current.read_text())["headline"] == "keep"
    assert existing_pdf.read_text() == "keep-pdf"
    assert not (artifact_dir / "revisions").exists()
