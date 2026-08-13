from __future__ import annotations

import json
from types import SimpleNamespace

from career_engine import pipeline


class FakeTracker:
    def __init__(self):
        self.updated = None

    def get_job(self, job_id):
        return {"job": {"job_id": job_id}, "processing_state": {}, "generated_artifacts": []}

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
