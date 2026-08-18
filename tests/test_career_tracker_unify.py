from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tools import career_tracker_unify as unify

REPO = Path(__file__).resolve().parents[1]


def tracker_class():
    path = REPO / "projects/job-automation/tracker.py"
    spec = importlib.util.spec_from_file_location("tracker_test_unify", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CareerTracker


@pytest.fixture()
def runtime(tmp_path: Path):
    repo = tmp_path / "repo"
    base = repo / "projects/job-automation"
    tracker = tracker_class()(base)
    tracker.ensure_layout()
    (repo / "dashboard/career-review").mkdir(parents=True)
    return repo, tracker


def seed_job(
    tracker,
    job_id: str,
    *,
    company: str = "Example Co",
    role: str = "Design Manager",
    source_url: str = "",
    processing_status: str = "ingested",
    application_status: str = "not_submitted",
    route: str = "portal",
    first_seen: str = "2026-08-01T00:00:00+00:00",
):
    now = "2026-08-16T00:00:00+00:00"
    job = {
        "job_id": job_id,
        "source": "test",
        "external_job_id": "",
        "source_url": source_url,
        "company": company,
        "role": role,
        "location": "Riyadh, Saudi Arabia",
        "posting_date": "",
        "closing_date": "",
        "jd_hash": "",
        "full_jd_path": f"projects/job-automation/data/jobs/{job_id}.json",
        "first_seen": first_seen,
        "last_seen": now,
        "ingested_by": "chatgpt",
        "fit_score": "80",
        "priority": "high_priority",
        "owner": "chatgpt",
        "processing_status": processing_status,
        "resume_status": "not_started",
        "cover_letter_status": "not_started",
        "pdf_status": "not_started",
        "gmail_draft_status": "not_started",
        "application_status": application_status,
        "outcome": "",
        "last_updated": now,
        "next_action": "Review",
        "notes": "",
    }
    record = {
        "job": job,
        "full_job_description": "Senior design-management vacancy.",
        "normalized_requirements": [],
        "provenance": {"source": "test", "source_url": source_url},
        "scoring": {"total": 80, "raw_total": 80, "recommendation": "high_priority", "rationale": [], "gaps": []},
        "evidence_matches": [],
        "processing_state": {
            "owner": "chatgpt",
            "status": processing_status,
            "live_status": "live",
            "route": {"route": route, "application_url": source_url, "recipient": "jobs@example.com" if route == "email" else ""},
        },
        "generated_artifacts": [],
        "gmail_draft_reference": None,
        "history": [],
    }
    tracker._save_job_and_row(record)


class FakeHere:
    def __init__(self, workflow=None, history=None):
        self.collections = {"workflow": workflow or [], "history": history or []}
        self.patches = []

    def records(self, collection: str, limit: int = 1000):
        return list(self.collections.get(collection, []))

    def patch(self, collection: str, record_id: str, fields):
        self.patches.append((collection, record_id, dict(fields)))


def wrap(record_id: str, data: dict, stamp: str = "2026-08-16T10:00:00Z"):
    return {"id": record_id, "data": data, "createdAt": stamp, "updatedAt": stamp}


def test_summary_is_single_partition_and_application_split(runtime):
    repo, tracker = runtime
    seed_job(tracker, "a" * 20, processing_status="ingested")
    seed_job(tracker, "b" * 20, processing_status="manual_review_needed")
    seed_job(tracker, "c" * 20, processing_status="awaiting_owner_approval")
    seed_job(tracker, "d" * 20, processing_status="applied", application_status="submitted", route="portal")
    seed_job(tracker, "e" * 20, processing_status="applied", application_status="sent", route="email")
    seed_job(tracker, "f" * 20, processing_status="inactive")

    summary = unify.canonical_summary(tracker, repo)

    assert summary["counts"]["tracked_total"] == 6
    assert summary["counts"]["found"] == 1
    assert summary["counts"]["needs_review"] == 1
    assert summary["counts"]["ready_for_review"] == 1
    assert summary["counts"]["applied_total"] == 2
    assert summary["counts"]["submitted_portal"] == 1
    assert summary["counts"]["sent_email"] == 1
    assert summary["counts"]["closed_inactive"] == 1
    assert unify.verify_summary(summary) == []


def test_submission_confirmation_promotes_tracker_and_workflow_is_canonical(runtime):
    repo, tracker = runtime
    job_id = "1" * 20
    seed_job(tracker, job_id, source_url="https://example.com/job/1", processing_status="awaiting_owner_approval")
    here = FakeHere(
        workflow=[wrap("wf1", {"role_key": f"tracker-{job_id}", "stage": "applied", "company": "Example Co", "role": "Design Manager"})],
        history=[wrap("h1", {
            "role_key": f"tracker-{job_id}",
            "event": "application_submitted",
            "to_stage": "applied",
            "submitted_at": "2026-08-16T09:59:00Z",
            "url": "https://example.com/job/1",
            "note": json.dumps({"company": "Example Co", "role": "Design Manager", "document_sha256": "abc"}),
        })],
    )

    report = unify.reconcile_site_data(tracker, repo, here, apply=True)
    record = tracker.get_job(job_id)

    assert record["job"]["processing_status"] == "applied"
    assert record["job"]["application_status"] == "submitted"
    assert job_id in report["submission_promoted"]
    assert report["workflow_applied_without_evidence_blocked"] == []
    assert unify.canonical_stage(record, repo) == "applied"


def test_portal_open_does_not_become_application(runtime):
    repo, tracker = runtime
    job_id = "2" * 20
    seed_job(tracker, job_id, source_url="https://example.com/job/2", processing_status="awaiting_owner_approval")
    here = FakeHere(
        workflow=[wrap("wf2", {"role_key": f"tracker-{job_id}", "stage": "applied", "company": "Example Co", "role": "Design Manager"})],
        history=[wrap("h2", {"role_key": f"tracker-{job_id}", "event": "portal_opened", "to_stage": "ready_review"})],
    )

    report = unify.reconcile_site_data(tracker, repo, here, apply=True)
    record = tracker.get_job(job_id)

    assert record["job"]["application_status"] == "not_submitted"
    assert record["job"]["processing_status"] == "awaiting_owner_approval"
    assert f"tracker-{job_id}" in report["workflow_applied_without_evidence_blocked"]
    assert ("workflow", "wf2", {"stage": "ready_review", "role_key": f"tracker-{job_id}"}) in here.patches


def test_owner_undo_retracts_accidental_submission_without_deleting_history(runtime):
    repo, tracker = runtime
    job_id = "7" * 20
    seed_job(tracker, job_id, source_url="https://example.com/job/7", processing_status="awaiting_owner_approval")
    here = FakeHere(
        workflow=[wrap("wf7", {"role_key": f"tracker-{job_id}", "stage": "applied", "company": "Example Co", "role": "Design Manager"}, "2026-08-16T10:01:00Z")],
        history=[
            wrap("h7-submit", {
                "role_key": f"tracker-{job_id}",
                "event": "application_submitted",
                "to_stage": "applied",
                "submitted_at": "2026-08-16T09:59:00Z",
                "url": "https://example.com/job/7",
            }, "2026-08-16T09:59:00Z"),
            wrap("h7-undo", {
                "role_key": f"tracker-{job_id}",
                "event": "application_submission_retracted",
                "from_stage": "applied",
                "to_stage": "ready_review",
                "retracted_event_id": "h7-submit",
                "retracted_at": "2026-08-16T10:00:00Z",
            }, "2026-08-16T10:00:00Z"),
        ],
    )

    report = unify.reconcile_site_data(tracker, repo, here, apply=True)
    record = tracker.get_job(job_id)

    assert record["job"]["application_status"] == "not_submitted"
    assert record["job"]["processing_status"] == "ingested"
    assert job_id in report["submission_retracted"]
    assert f"tracker-{job_id}" in report["workflow_applied_without_evidence_blocked"]
    assert ("workflow", "wf7", {"stage": "found", "role_key": f"tracker-{job_id}"}) in here.patches


def test_dashboard_stage_is_projection_and_cannot_overwrite_tracker(runtime):
    repo, tracker = runtime
    job_id = "3" * 20
    seed_job(tracker, job_id, processing_status="ingested")
    here = FakeHere(
        workflow=[wrap("wf3", {"role_key": f"tracker-{job_id}", "stage": "manual_review_needed", "company": "Example Co", "role": "Design Manager"})]
    )

    unify.reconcile_site_data(tracker, repo, here, apply=True)

    assert tracker.get_job(job_id)["job"]["processing_status"] == "ingested"
    assert here.patches == [("workflow", "wf3", {"stage": "found", "role_key": f"tracker-{job_id}"})]


def test_direct_tracker_role_key_avoids_full_tracker_rescan(runtime, monkeypatch):
    _, tracker = runtime
    job_id = "9" * 20
    seed_job(tracker, job_id)

    def fail_scan(_tracker):
        raise AssertionError("direct tracker role keys must not rescan every job JSON")

    monkeypatch.setattr(unify, "tracker_records", fail_scan)
    resolved = unify.resolve_site_role(
        tracker,
        {"company": "Example Co", "role": "Design Manager"},
        f"tracker-{job_id}",
        apply=True,
    )

    assert resolved == job_id


def test_legacy_role_alias_resolves_only_to_existing_tracker_job(runtime, monkeypatch):
    _, tracker = runtime
    job_id = "8" * 20
    seed_job(tracker, job_id)

    def fail_scan(_tracker):
        raise AssertionError("known legacy aliases must resolve without a full tracker scan")

    monkeypatch.setattr(unify, "tracker_records", fail_scan)
    resolved = unify.resolve_site_role(
        tracker,
        {},
        "legacy-design-manager",
        apply=True,
        aliases={"legacy-design-manager": job_id},
    )

    assert resolved == job_id


def test_legacy_site_stub_is_superseded_and_direct_key_follows_canonical(runtime):
    _, tracker = runtime
    canonical = "7" * 20
    seed_job(tracker, canonical, company="Cenomi Centers", role="Senior Design Architect")
    stub = unify.create_stub(
        tracker,
        {"key": "cenomi-senior-design-architect", "company": "Cenomi Centers", "role": "Senior Design Architect"},
        source_label="dashboard_site_data",
        comment="test temporary Site Data stub",
    )

    changes = unify.supersede_legacy_site_stubs(
        tracker,
        {"cenomi-senior-design-architect": canonical},
        apply=True,
    )

    assert changes == [{"job_id": stub, "canonical_job_id": canonical, "legacy_key": "cenomi-senior-design-architect"}]
    retired = tracker.get_job(stub)
    assert retired["job"]["processing_status"] == "superseded"
    assert retired["processing_state"]["canonical_job_id"] == canonical
    assert unify.resolve_site_role(tracker, {}, f"tracker-{stub}", apply=True) == canonical


def test_superseded_direct_key_follows_legacy_next_action_without_reactivation(runtime):
    _, tracker = runtime
    canonical = "6" * 20
    duplicate = "5" * 20
    seed_job(tracker, canonical)
    seed_job(tracker, duplicate)
    tracker.update_job(
        duplicate,
        {"processing_status": "superseded", "next_action": f"Use canonical job {canonical}"},
        comment="test historical duplicate without canonical_job_id in processing_state",
    )

    assert "canonical_job_id" not in tracker.get_job(duplicate)["processing_state"]
    assert unify.resolve_site_role(tracker, {}, f"tracker-{duplicate}", apply=True) == canonical
    assert tracker.get_job(duplicate)["job"]["processing_status"] == "superseded"


def test_superseded_workflow_alias_cannot_overwrite_canonical_status(runtime):
    repo, tracker = runtime
    canonical = "6" * 20
    duplicate = "5" * 20
    seed_job(tracker, canonical, processing_status="blocked")
    seed_job(tracker, duplicate)
    tracker.update_job(
        duplicate,
        {"processing_status": "superseded", "next_action": f"Use canonical job {canonical}"},
        comment="test historical duplicate workflow alias",
    )
    here = FakeHere(workflow=[wrap("wf-alias", {"role_key": f"tracker-{duplicate}", "stage": "inactive"})])

    unify.reconcile_site_data(tracker, repo, here, apply=True)

    assert tracker.get_job(canonical)["job"]["processing_status"] == "blocked"
    assert here.patches == [("workflow", "wf-alias", {"stage": "found", "role_key": f"tracker-{canonical}"})]


def test_legacy_dashboard_seed_migrates_missing_job_and_dedupes_existing_url(runtime):
    repo, tracker = runtime
    existing_id = "4" * 20
    existing_url = "https://www.linkedin.com/jobs/view/example-role-4448815998/"
    seed_job(tracker, existing_id, source_url=existing_url)
    seed = [
        {"key": "existing", "company": "Existing", "role": "Role", "location": "Riyadh", "application_url": "https://www.linkedin.com/jobs/view/4448815998", "decision": "selective"},
        {"key": "missing", "company": "Missing Co", "role": "Project Director", "location": "Riyadh", "application_url": "https://example.com/jobs/missing", "decision": "do_not_pursue", "score": 45},
    ]
    seed_path = repo / "dashboard/career-review/legacy-tracker-seed.json"
    seed_path.write_text(json.dumps(seed), encoding="utf-8")

    report = unify.migrate_seed(tracker, repo, seed_path, apply=True)

    assert existing_id in report["matched"]
    assert len(report["created"]) == 1
    created = tracker.get_job(report["created"][0])
    assert created["job"]["company"] == "Missing Co"
    assert created["job"]["processing_status"] == "rejected"
    assert created["provenance"]["intake_stub"] is True


def test_exact_url_duplicates_are_superseded_without_losing_applied_record(runtime):
    repo, tracker = runtime
    url = "https://example.com/jobs/dup"
    seed_job(tracker, "5" * 20, source_url=url, processing_status="ingested", first_seen="2026-08-01T00:00:00Z")
    seed_job(tracker, "6" * 20, source_url=url, processing_status="applied", application_status="submitted", first_seen="2026-08-10T00:00:00Z")

    report = unify.supersede_exact_duplicates(tracker, apply=True)

    assert report["superseded"] == [{"job_id": "5" * 20, "canonical_job_id": "6" * 20}]
    assert tracker.get_job("5" * 20)["job"]["processing_status"] == "superseded"
    summary = unify.canonical_summary(tracker, repo)
    assert summary["counts"]["tracked_total"] == 1
    assert summary["counts"]["applied_total"] == 1


def test_layout_repair_reconstructs_orphan_csv_and_restores_json_index(runtime):
    repo, tracker = runtime
    seed_job(tracker, "7" * 20)
    rows = tracker.list_rows()
    orphan = dict(rows[0])
    orphan["job_id"] = "8" * 20
    orphan["company"] = "CSV Only"
    tracker._write_rows(rows + [orphan])

    report = unify.repair_tracker_layout(tracker, apply=True)

    assert "8" * 20 in report["reconstructed"]
    assert tracker.get_job("8" * 20)["job"]["company"] == "CSV Only"
    assert {row["job_id"] for row in tracker.list_rows()} == {"7" * 20, "8" * 20}
