"""Hermetic tests for Modern Executive Sidebar revision bookkeeping.

These run entirely against a temporary CareerTracker (a copy of the repo's own
projects/job-automation/tracker.py placed in a tmp directory with
CAREER_ENGINE_TRACKER_BASE pointing at it). The live canonical tracker is
never read or written.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import tools.regenerate_sidebar_template as rst  # noqa: E402
from tools.regenerate_sidebar_template import (  # noqa: E402
    apply_revision,
    reconcile_sidebar_metadata,
    sidebar_logical_key,
)

TEMPLATE_VERSION = "1.5"
STEM = "Abdelhamid_Farah_Design_Manager_CV"


class _Paths:
    def __init__(self, tracker_base: Path):
        self.tracker_base = tracker_base


def _make_tracker(base: Path):
    module = rst._load_tracker_module(_Paths(base))
    return module.CareerTracker(base)


@pytest.fixture()
def tracker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base = tmp_path / "tracker-base"
    base.mkdir()
    shutil.copy(REPO_ROOT / "projects/job-automation/tracker.py", base / "tracker.py")
    monkeypatch.setenv("CAREER_ENGINE_TRACKER_BASE", str(base))
    tracker = _make_tracker(base)
    result = tracker.ingest(
        {
            "source": "fixture",
            "external_job_id": "sidebar-apply-1",
            "source_url": "",
            "company": "Fixture Consulting Co",
            "role": "Senior Design Manager",
            "full_job_description": "Hermetic fixture vacancy used to prove sidebar apply idempotence.",
        },
        comment="fixture job for sidebar apply idempotence tests",
        actor="system",
    )
    return base, tracker, result["job_id"]


def _qa_report(job_id: str, base: Path, *, docx_sha: str, pdf_sha: str, version: str = TEMPLATE_VERSION, stem: str = STEM) -> dict:
    artifact_dir = base / "artifacts" / job_id
    return {
        "job_id": job_id,
        "passed": True,
        "template_version": version,
        "template_sha256": "b" * 64,
        "bundle_hash": "bundle-hash-1",
        "docx": {"path": str(artifact_dir / f"{stem}_v{version}.docx"), "sha256": docx_sha},
        "pdf": {"path": str(artifact_dir / f"{stem}_v{version}.pdf"), "sha256": pdf_sha},
        "findings": [],
    }


def _counts(tracker, job_id: str) -> tuple[int, int, int]:
    record = tracker.get_job(job_id)
    artifacts = len(record.get("generated_artifacts") or [])
    history = len(record.get("history") or [])
    events = len(tracker.read_events(job_id))
    return artifacts, history, events


def _write_report(report: dict) -> None:
    rst._write_qa_report(report, root=REPO_ROOT)


def test_sidebar_logical_key_ignores_byte_differences() -> None:
    base_entry = {"type": "final_pdf", "variant": "modern-executive-sidebar", "path": "/x/Doc_v1.5.pdf", "sha256": "a", "template_version": "1.5"}
    rerendered = {**base_entry, "sha256": "ff"}
    assert sidebar_logical_key(base_entry) == sidebar_logical_key(rerendered)
    other_version = {**base_entry, "path": "/x/Doc_v1.6.pdf", "template_version": "1.6"}
    assert sidebar_logical_key(other_version) != sidebar_logical_key(base_entry)
    ats = {**base_entry, "variant": "ats-linear", "type": "ats_pdf"}
    non_sidebar = {**base_entry, "type": "cover_letter_pdf", "variant": ""}
    assert sidebar_logical_key(ats) is None
    assert sidebar_logical_key(non_sidebar) is None


def test_first_apply_appends_one_logical_revision(tracker_env) -> None:
    base, tracker, job_id = tracker_env
    _write_report(_qa_report(job_id, base, docx_sha="d1", pdf_sha="p1"))
    result = apply_revision(job_id, root=REPO_ROOT)
    assert result.get("applied") is True
    record = tracker.get_job(job_id)
    sidebar = [item for item in record["generated_artifacts"] if item.get("variant") == "modern-executive-sidebar"]
    assert [item["type"] for item in sidebar] == ["final_docx", "final_pdf"]
    # Exactly one appended event for the new logical revision.
    assert _counts(tracker, job_id) == (2, 2, 2)


def test_reapply_identical_report_is_a_full_noop(tracker_env) -> None:
    base, tracker, job_id = tracker_env
    report = _qa_report(job_id, base, docx_sha="d1", pdf_sha="p1")
    _write_report(report)
    assert apply_revision(job_id, root=REPO_ROOT).get("applied") is True
    before = _counts(tracker, job_id)
    again = apply_revision(job_id, root=REPO_ROOT)
    assert again == {"job_id": job_id, "applied": True, "already_recorded": True}
    assert _counts(tracker, job_id) == before


def test_reapply_after_rerender_sha_change_grows_nothing(tracker_env) -> None:
    """A rerender of the SAME logical revision changes bytes; a repeated apply
    must not append artifact entries, record history or event-log lines."""
    base, tracker, job_id = tracker_env
    _write_report(_qa_report(job_id, base, docx_sha="d1", pdf_sha="p1"))
    assert apply_revision(job_id, root=REPO_ROOT).get("applied") is True
    before = _counts(tracker, job_id)
    events_before = tracker.read_events(job_id)

    # Rerender: same template version, same revision stem, different bytes.
    _write_report(_qa_report(job_id, base, docx_sha="d2-rerendered", pdf_sha="p2-rerendered"))
    result = apply_revision(job_id, root=REPO_ROOT)
    assert result["applied"] is True
    assert result["already_recorded"] is True
    assert result["idempotent"] is True
    assert result["metadata_refresh_needed"] is True
    after = _counts(tracker, job_id)
    assert after == before, f"reapply grew counts: {before} -> {after}"
    # Append-only event log untouched by the idempotent reapply.
    assert tracker.read_events(job_id) == events_before


def test_reconcile_refreshes_current_metadata_in_place_with_single_event(tracker_env) -> None:
    base, tracker, job_id = tracker_env
    # A pre-existing unrelated artifact must never be touched by reconciliation.
    tracker.update_job(
        job_id,
        {"generated_artifacts": [{"type": "ats_pdf", "variant": "ats-linear", "path": "/x/ATS.pdf", "sha256": "keep-me"}]},
        comment="fixture pre-existing ATS artifact",
        actor="system",
    )
    _write_report(_qa_report(job_id, base, docx_sha="d1", pdf_sha="p1"))
    assert apply_revision(job_id, root=REPO_ROOT).get("applied") is True
    counts_after_apply = _counts(tracker, job_id)

    # Rerender with new bytes, then explicitly reconcile current metadata.
    _write_report(_qa_report(job_id, base, docx_sha="d2-rerendered", pdf_sha="p2-rerendered"))
    result = reconcile_sidebar_metadata(job_id, root=REPO_ROOT)
    assert result["reconciled"] is True
    assert result["reconciled_entries"] == 2

    record = tracker.get_job(job_id)
    artifacts = record["generated_artifacts"]
    assert len(artifacts) == counts_after_apply[0], "artifact list length must be unchanged"
    assert artifacts[0] == {"type": "ats_pdf", "variant": "ats-linear", "path": "/x/ATS.pdf", "sha256": "keep-me"}
    sidebar_meta = {item["type"]: item for item in artifacts if item.get("variant") == "modern-executive-sidebar"}
    assert sidebar_meta["final_pdf"]["sha256"] == "p2-rerendered"
    assert sidebar_meta["final_docx"]["sha256"] == "d2-rerendered"
    # Exactly one reconciliation event appended on top of the two earlier ones.
    assert _counts(tracker, job_id) == (counts_after_apply[0], counts_after_apply[1] + 1, counts_after_apply[2] + 1)
    # Append-only proof: the first event line is preserved byte-for-byte.
    lines = (base / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == counts_after_apply[2] + 1
    assert json_loads(lines[0])["action"] == "created"


def test_second_reconcile_without_new_render_is_a_noop(tracker_env) -> None:
    base, tracker, job_id = tracker_env
    _write_report(_qa_report(job_id, base, docx_sha="d1", pdf_sha="p1"))
    assert apply_revision(job_id, root=REPO_ROOT).get("applied") is True
    _write_report(_qa_report(job_id, base, docx_sha="d2", pdf_sha="p2"))
    assert reconcile_sidebar_metadata(job_id, root=REPO_ROOT)["reconciled"] is True
    before = _counts(tracker, job_id)
    again = reconcile_sidebar_metadata(job_id, root=REPO_ROOT)
    assert again == {"job_id": job_id, "reconciled": False, "reason": "already_current"}
    assert _counts(tracker, job_id) == before


def test_partial_logical_state_appends_only_the_missing_side(tracker_env) -> None:
    base, tracker, job_id = tracker_env
    # Pre-seed only the DOCX side of the v1.5 logical revision.
    artifact_dir = base / "artifacts" / job_id
    tracker.update_job(
        job_id,
        {"generated_artifacts": [{
            "type": "final_docx",
            "variant": "modern-executive-sidebar",
            "path": str(artifact_dir / f"{STEM}_v{TEMPLATE_VERSION}.docx"),
            "sha256": "d1",
            "bundle_hash": "bundle-hash-1",
            "template_version": TEMPLATE_VERSION,
        }]},
        comment="fixture partial sidebar revision state",
        actor="system",
    )
    _write_report(_qa_report(job_id, base, docx_sha="d1", pdf_sha="p1"))
    result = apply_revision(job_id, root=REPO_ROOT)
    assert result["applied"] is True
    record = tracker.get_job(job_id)
    sidebar = [item for item in record["generated_artifacts"] if item.get("variant") == "modern-executive-sidebar"]
    assert [item["type"] for item in sidebar] == ["final_docx", "final_pdf"], (
        "only the missing PDF side may be appended; no duplicate logical entries"
    )


def test_distinct_template_version_is_a_new_logical_revision(tracker_env) -> None:
    base, tracker, job_id = tracker_env
    _write_report(_qa_report(job_id, base, docx_sha="d1", pdf_sha="p1", version="1.5"))
    assert apply_revision(job_id, root=REPO_ROOT).get("applied") is True
    before = _counts(tracker, job_id)
    _write_report(_qa_report(job_id, base, docx_sha="d9", pdf_sha="p9", version="1.6"))
    result = apply_revision(job_id, root=REPO_ROOT)
    assert result["applied"] is True
    assert "already_recorded" not in result
    after = _counts(tracker, job_id)
    assert after[0] == before[0] + 2
    assert after[1] == before[1] + 1
    assert after[2] == before[2] + 1


def test_reconcile_refuses_unapplied_logical_revision(tracker_env) -> None:
    base, tracker, job_id = tracker_env
    _write_report(_qa_report(job_id, base, docx_sha="d1", pdf_sha="p1"))
    result = reconcile_sidebar_metadata(job_id, root=REPO_ROOT)
    assert result == {"job_id": job_id, "reconciled": False, "reason": "logical_revision_not_recorded", "hint": "apply the revision first"}
    assert _counts(tracker, job_id) == (0, 1, 1)


def json_loads(raw: str) -> dict:
    import json

    return json.loads(raw)
