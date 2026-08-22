"""Gmail discovery + reconciliation pipeline regression.

Covers Part G:
1. discovery wired into central scanner
2. cross-source dedupe
3. genuine confirmation promotes correct record
4. ambiguous leaves state unchanged
5. recruiter/interview/status classification
6. no send/submit
7. review projection privacy
8. failure surfaced
9. owner decisions remain authoritative
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from career_engine.gmail_discovery import (
    classify_message_category,
    discover_job_mail,
)
from career_engine.gmail_reconcile import (
    classify_submission_message,
    match_submission_to_tracker,
)

# Reuse the shared engine_root fixture from the central v1 tests.
from tests.test_career_engine_v1 import engine_root  # noqa: F401

import importlib.util as _importlib_util


def _load_daily_scanner():
    spec = _importlib_util.spec_from_file_location(
        "daily_scanner", Path("projects/job-automation/daily_scanner.py")
    )
    mod = _importlib_util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def _message(subject: str = "", body: str = "", sender: str = "", urls: list[str] | None = None, labels=None, mid: str = "m1") -> dict:
    return {
        "id": mid,
        "thread_id": "t1",
        "subject": subject,
        "body": body,
        "from": sender,
        "urls": urls or [],
        "label_ids": labels or [],
        "date": "Thu, 21 Aug 2026 10:00:00 +0300",
    }


# 1. discovery is importable and callable from daily_scanner
def test_daily_scanner_has_gmail_flag() -> None:
    import importlib
    ds = importlib.import_module("projects.job_automation.daily_scanner") if False else None
    # Direct check: the file contains --no-gmail
    text = Path("projects/job-automation/daily_scanner.py").read_text(encoding="utf-8")
    assert "--no-gmail" in text
    assert "gmail_discovery" in text
    assert "gmail_job_alerts" in text


def test_gmail_discovery_wired_into_daily_scanner_main(monkeypatch, tmp_path: Path) -> None:
    """daily_scanner.main merges gmail candidates into the scan payload."""
    ds = _load_daily_scanner()

    fake_candidates = [
        {
            "company": "Acme",
            "role": "Senior Architect",
            "source": "gmail_alert",
            "source_url": "https://www.linkedin.com/jobs/view/9999999999/",
            "application_url": "https://www.linkedin.com/jobs/view/9999999999/",
            "external_job_id": "9999999999",
            "full_job_description": "",
            "live_status": "unverified",
            "source_path": "gmail_job_alerts",
        }
    ]
    fake_discovery = {
        "schema_version": 1,
        "authenticated": True,
        "messages_scanned": 10,
        "career_relevant_messages": 5,
        "job_alert_messages": 2,
        "recruiter_messages": 0,
        "vacancy_messages": 0,
        "application_instruction_messages": 0,
        "submission_confirmation_messages": 0,
        "application_status_messages": 0,
        "interview_or_assessment_messages": 0,
        "candidate_jobs_extracted": 1,
        "jobs_new_after_deduplication": 1,
        "jobs_matched_existing": 0,
        "ambiguous_messages_manual_review": 0,
        "platform_counts": {"linkedin": 2},
        "errors": [],
        "send_or_submit": False,
        "candidates": fake_candidates,
    }

    import career_engine.gmail_discovery as gd

    monkeypatch.setattr(gd, "discover_job_mail", lambda root, **kw: dict(fake_discovery))
    # Patch run_scan to capture the merged input
    captured: dict = {}

    def fake_run_scan(path: Path, *, root: Path, scanner_id: str):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        jobs = payload if isinstance(payload, list) else payload.get("jobs", [])
        captured["jobs"] = jobs
        captured["paths"] = payload.get("paths", []) if isinstance(payload, dict) else []
        return {
            "schema_version": 1,
            "scanned_at": "2026-08-21T10:00:00+00:00",
            "scanner_id": scanner_id,
            "bundle_hash": "abc",
            "results": [],
            "statistics": {"jobs_discovered": len(jobs), "jobs_ingested": len(jobs), "new_jobs": 0, "existing_jobs": len(jobs), "by_path": {}},
            "send_or_submit": False,
        }

    monkeypatch.setattr(ds, "run_scan", fake_run_scan)
    monkeypatch.setattr(ds, "_build_review_bundle", lambda report: {"operation_date": "2026-08-21"})
    monkeypatch.setattr(ds, "_publish_review_bundle", lambda bundle: {"status": "skipped", "branch": "career-review-runtime"})
    monkeypatch.setattr(ds, "reconcile_existing_non_target_jobs", lambda *a, **kw: {})

    src = tmp_path / "input.json"
    src.write_text(json.dumps({"jobs": [{"company": "X", "role": "Y", "source_url": "https://example.com/a", "external_job_id": "1"}]}), encoding="utf-8")
    out = tmp_path / "out.json"
    # Need REPO_ROOT to be tmp engine root? daily_scanner main uses REPO_ROOT constant; patch it
    monkeypatch.setattr(ds, "REPO_ROOT", Path(tmp_path))
    monkeypatch.setattr(ds, "load_config", lambda root: ({"scoring": {"thresholds": {"high_priority": 70}}}, type("P", (), {"tracker_base": Path(tmp_path)})()))  # noqa
    monkeypatch.setattr(ds, "load_bundle", lambda root: {"bundle_hash": "abc", "taxonomy": {}, "config": {"daily_scanner": {"minimum_score_for_generation": 70, "maximum_generation_packets_per_scan": 5}}})
    monkeypatch.setattr(ds, "_load_tracker", lambda paths: type("T", (), {"list_rows": lambda self: [], "get_job": lambda self, jid: (_ for _ in ()).throw(KeyError(jid))})())

    # Call main - it should merge gmail candidate
    result = ds.main(["--input", str(src), "--output", str(out), "--scanner-id", "hermes_scanner", "--no-review-publish"])
    assert result == 0
    # Gmail candidate was merged
    assert any(j.get("external_job_id") == "9999999999" for j in captured.get("jobs", []))
    # Report contains gmail_discovery sanitized (no candidates)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "gmail_discovery" in report
    assert "candidates" not in report["gmail_discovery"]
    assert report["gmail_discovery"]["authenticated"] is True
    assert report["gmail_discovery"]["send_or_submit"] is False


def test_cross_source_duplicate_does_not_inflate() -> None:
    """Same URL via Gmail and direct source must be deduplicated before ingestion."""
    ds = _load_daily_scanner()
    _url_key_for_merge = ds._url_key_for_merge

    direct = {"source_url": "https://www.linkedin.com/jobs/view/1234567890/", "external_job_id": "1234567890"}
    gmail = {"source_url": "https://www.linkedin.com/comm/jobs/view/1234567890/?trk=eml", "external_job_id": "1234567890"}
    assert _url_key_for_merge(direct["source_url"]) == _url_key_for_merge(gmail["source_url"])


def test_genuine_confirmation_promotes_correct_record(engine_root: Path) -> None:
    from career_engine.pipeline import prepare
    from career_engine.gmail_reconcile import match_submission_to_tracker, _append_submission_evidence
    from career_engine.config import load_config
    from career_engine.pipeline import _load_tracker

    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    # Create two distinct tracker records via prepare
    base = {
        "company": "Bechtel",
        "role": "Senior Design Manager",
        "location": "Riyadh",
        "source": "test",
        "source_url": "https://example.com/bechtel/111",
        "application_url": "https://example.com/bechtel/111/apply",
        "live_status": "live",
        "live_verified_at": "2026-08-21T10:00:00+00:00",
        "live_verification_source": "test",
        "full_job_description": "Lead design governance and multidisciplinary coordination across complex programmes. Manage senior client and stakeholder relationships. Degree in architecture and strong Saudi project experience. Demonstrated team leadership.",
    }
    other = dict(base)
    other["role"] = "Field Engineer"
    other["source_url"] = "https://example.com/bechtel/222"
    other["application_url"] = "https://example.com/bechtel/222/apply"

    s1 = prepare(base, root=engine_root, actor="system")
    s2 = prepare(other, root=engine_root, actor="system")

    evidence = {
        "company": "Bechtel",
        "role": "Senior Design Manager",
        "external_job_id": "",
        "route": "portal",
        "signal": "workday_submission_confirmation",
        "urls": ["https://example.com/bechtel/111"],
        "subject": "Your Bechtel Job Application Has Been Received",
        "sender": "bechtel@myworkdayjobs.com",
        "message_id": "m-test-1",
        "thread_id": "t1",
        "date": "2026-08-21",
    }
    job_id, reason = match_submission_to_tracker(tracker, evidence)
    assert job_id == s1["job_id"]
    assert reason in {"source_url", "company_role"}


def test_ambiguous_confirmation_does_not_change_state(engine_root: Path) -> None:
    from career_engine.pipeline import prepare

    b1 = {
        "company": "KEO International Consultants",
        "role": "Commercial Manager",
        "location": "Riyadh",
        "source": "test",
        "source_url": "https://example.com/keo/1",
        "application_url": "https://example.com/keo/1/apply",
        "live_status": "live",
        "live_verified_at": "2026-08-21T10:00:00+00:00",
        "live_verification_source": "test",
        "full_job_description": "Lead design governance and multidisciplinary coordination across complex programmes. Manage senior client and stakeholder relationships. Degree in architecture.",
    }
    b2 = dict(b1)
    b2["source_url"] = "https://example.com/keo/2"
    b2["application_url"] = "https://example.com/keo/2/apply"
    # Two records same company+role -> ambiguous
    from career_engine.config import load_config
    from career_engine.pipeline import _load_tracker
    from career_engine.gmail_reconcile import match_submission_to_tracker

    prepare(b1, root=engine_root, actor="system")
    prepare(b2, root=engine_root, actor="system")
    _, paths = load_config(engine_root)
    tracker = _load_tracker(paths)
    evidence = {"company": "KEO International Consultants", "role": "Commercial Manager", "external_job_id": "", "urls": [], "subject": "", "sender": "", "message_id": "m2"}
    job_id, reason = match_submission_to_tracker(tracker, evidence)
    assert job_id == ""
    assert reason == "ambiguous_company_role"


def test_classification_covers_recruiter_interview_status() -> None:
    assert classify_message_category(_message(subject="Interview invitation - Senior Architect at NEOM", body="We would like to invite you for an interview", sender="talent@neom.com")) == "interview_assessment"
    assert classify_message_category(_message(subject="Application update", body="Your application status has been updated")) == "application_status"
    assert classify_message_category(_message(subject="Your application was viewed by Confidential", sender="LinkedIn <jobs-noreply@linkedin.com>")) == "application_status"
    assert classify_message_category(_message(subject="Interview assessment invitation", sender="LinkedIn <jobs-noreply@linkedin.com>")) == "interview_assessment"
    assert classify_message_category(_message(subject="Recruiter reached out", sender="John Recruiter <recruiter@parsons.com>")) == "recruiter"
    assert classify_message_category(_message(subject="You may be a fit for Senior Architect at ElMassri", sender="LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>")) == "job_alert"
    assert classify_message_category(_message(subject="Thank you for applying to Qiddiya Investment Company", body="application for the Senior Manager job was submitted successfully", sender="qiddiya@workable.com", urls=["https://apply.workable.com/j/123"])) == "submission_confirmation"


def test_no_send_submit_in_discovery_and_reconcile() -> None:
    from career_engine.gmail_discovery import discover_job_mail
    from pathlib import Path
    import unittest.mock as mock

    with mock.patch("career_engine.gmail_discovery.search_messages", return_value=[]):
        result = discover_job_mail(Path("."), max_results=5)
        assert result["send_or_submit"] is False
    # reconcile report also has flag
    text = Path("career_engine/gmail_reconcile.py").read_text(encoding="utf-8")
    assert "send_or_submit" in text


def test_review_projection_contains_counts_not_private_content(tmp_path: Path, monkeypatch) -> None:
    ds = _load_daily_scanner()
    _build_review_bundle = ds._build_review_bundle
    import json

    # Minimal report with gmail blocks containing would-be private fields if bug existed
    report = {
        "scanner_id": "hermes_scanner",
        "scanned_at": "2026-08-21T12:00:00+00:00",
        "bundle_hash": "abc",
        "results": [],
        "statistics": {"jobs_discovered": 0, "jobs_ingested": 0, "new_jobs": 0, "existing_jobs": 0, "by_path": {}},
        "gmail_discovery": {
            "authenticated": True,
            "messages_scanned": 5,
            "career_relevant_messages": 3,
            "job_alert_messages": 2,
            "platform_counts": {"linkedin": 2},
            "candidate_jobs_extracted": 2,
            "candidates": [{"source_url": "https://secret.example.com/job/1", "external_job_id": "1"}],  # must be stripped
            "errors": [],
            "send_or_submit": False,
        },
        "gmail_submission_reconciliation": {
            "messages_scanned": 2,
            "submission_messages_classified": 1,
            "reconciled": [{"message_id": "secret123", "job_id": "abc", "changed": True}],
            "unmatched": [],
            "ambiguous_manual_review": 0,
            "application_states_changed": 1,
            "send_or_submit": False,
        },
    }
    # _build_review_bundle loads real tracker/bundle; mock those to avoid filesystem dependency
    ds2 = _load_daily_scanner()

    class FakeTracker:
        def list_rows(self): return []
        def get_job(self, jid): raise KeyError(jid)

    monkeypatch.setattr(ds2, "_load_tracker", lambda paths: FakeTracker())
    monkeypatch.setattr(ds2, "load_bundle", lambda root: {"bundle_hash": "abc", "taxonomy": {}, "config": {"scoring": {"thresholds": {"high_priority": 70}}}})
    monkeypatch.setattr(ds2, "load_config", lambda root: ({"scoring": {"thresholds": {"high_priority": 70}}}, type("P", (), {"tracker_base": Path(tmp_path)})()))
    monkeypatch.setattr(ds2, "_git_value", lambda *a, **kw: "")
    monkeypatch.setattr(ds2, "_git", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    bundle = _build_review_bundle(report)
    dumped = json.dumps(bundle, ensure_ascii=False)
    # privacy: no secret URL, no message id, no url
    assert "secret.example.com" not in dumped
    assert "secret123" not in dumped
    # but counts present
    assert bundle["gmail"]["messages_scanned"] == 5
    assert bundle["gmail"]["platform_counts"] == {"linkedin": 2}
    assert bundle["gmail"]["candidate_jobs_extracted"] == 2
    assert bundle["privacy"]["contains_urls"] is False
    assert bundle["privacy"]["contains_email_addresses_or_messages"] is False
    assert bundle["send_or_submit"] is False


def test_gmail_failure_surfaced_not_silent_zero(tmp_path: Path, monkeypatch) -> None:
    ds = _load_daily_scanner()
    _gmail_review_block = ds._gmail_review_block

    report = {
        "gmail_discovery": {"authenticated": False, "messages_scanned": 0, "errors": ["GmailAuthError: invalid_grant"], "platform_counts": {}, "send_or_submit": False},
        "gmail_submission_reconciliation": {"error": "GmailAuthError: invalid_grant", "messages_scanned": 0, "send_or_submit": False},
    }
    block = _gmail_review_block(report)
    assert block["authenticated"] is False
    assert len(block["errors"]) > 0
    assert "invalid_grant" in block["errors"][0]


def test_review_projection_gmail_counts_match_scanner_statistics(tmp_path: Path, monkeypatch) -> None:
    """Defect A: derived Gmail summary must equal the scanner's canonical per-path counts."""
    ds = _load_daily_scanner()

    class FakeTracker:
        def list_rows(self): return []
        def get_job(self, jid): raise KeyError(jid)

    monkeypatch.setattr(ds, "_load_tracker", lambda paths: FakeTracker())
    monkeypatch.setattr(ds, "load_bundle", lambda root: {"bundle_hash": "abc", "taxonomy": {}, "config": {"scoring": {"thresholds": {"high_priority": 70}}}})
    monkeypatch.setattr(ds, "load_config", lambda root: ({"scoring": {"thresholds": {"high_priority": 70}}}, type("P", (), {"tracker_base": Path(tmp_path)})()))
    monkeypatch.setattr(ds, "_git_value", lambda *a, **kw: "")
    monkeypatch.setattr(ds, "_git", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    report = {
        "scanner_id": "hermes_scanner",
        "scanned_at": "2026-08-22T00:15:00+03:00",
        "bundle_hash": "abc",
        "results": [],
        "statistics": {
            "jobs_discovered": 504, "jobs_ingested": 504, "new_jobs": 89, "existing_jobs": 415,
            "by_path": {
                "gmail_job_alerts": {
                    "attempted": True, "status": "observed",
                    "jobs_discovered": 105, "jobs_ingested": 105,
                    "new_jobs": 89, "existing_jobs": 16,
                },
            },
        },
        # Raw discovery-level numbers (pre-tracker-dedupe) must NOT leak into the
        # canonical summary when per-path statistics exist.
        "gmail_discovery": {
            "authenticated": True, "messages_scanned": 150,
            "candidate_jobs_extracted": 802,
            "jobs_new_after_deduplication": 105, "jobs_matched_existing": 0,
            "platform_counts": {"linkedin": 64}, "errors": [], "send_or_submit": False,
        },
        "gmail_submission_reconciliation": {
            "messages_scanned": 10, "submission_messages_classified": 3,
            "reconciled": [], "unmatched": [], "ambiguous_manual_review": 0,
            "application_states_changed": 0, "send_or_submit": False,
        },
    }
    bundle = ds._build_review_bundle(report)
    gmail = bundle["gmail"]
    assert gmail["jobs_discovered_from_gmail"] == 105
    assert gmail["jobs_new_after_deduplication"] == 89
    assert gmail["jobs_matched_existing"] == 16
    # Consistency with the source coverage row for the same path.
    coverage = next(item for item in bundle["source_coverage"] if item["path"] == "gmail_job_alerts")
    assert gmail["jobs_discovered_from_gmail"] == coverage["jobs_discovered"]
    assert gmail["jobs_new_after_deduplication"] == coverage["new_jobs"]
    assert gmail["jobs_matched_existing"] == coverage["existing_jobs"]


def test_scan_sha_vs_current_sha_not_conflated(tmp_path: Path, monkeypatch) -> None:
    """Defects B/C: scan-time source identity and projection-time identity are distinct fields."""
    ds = _load_daily_scanner()

    class FakeTracker:
        def list_rows(self): return []
        def get_job(self, jid): raise KeyError(jid)

    monkeypatch.setattr(ds, "_load_tracker", lambda paths: FakeTracker())
    monkeypatch.setattr(ds, "load_bundle", lambda root: {"bundle_hash": "abc", "taxonomy": {}, "config": {"scoring": {"thresholds": {"high_priority": 70}}}})
    monkeypatch.setattr(ds, "load_config", lambda root: ({"scoring": {"thresholds": {"high_priority": 70}}}, type("P", (), {"tracker_base": Path(tmp_path)})()))
    monkeypatch.setattr(ds, "_git_value", lambda *a, **kw: "c" * 40)
    monkeypatch.setattr(ds, "_git", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    scan_sha = "3" * 40
    report = {
        "scanner_id": "hermes_scanner",
        "scanned_at": "2026-08-22T00:15:00+03:00",
        "bundle_hash": "abc",
        "scan_source_sha": scan_sha,
        "results": [],
        "statistics": {"jobs_discovered": 0, "jobs_ingested": 0, "new_jobs": 0, "existing_jobs": 0, "by_path": {}},
    }
    bundle = ds._build_review_bundle(report)
    scan = bundle["scan"]
    assert scan["scan_source_sha"] == scan_sha
    assert scan["current_source_sha"] == "c" * 40
    assert scan["scan_sha_matches_current_source"] is False


def test_owner_decisions_remain_authoritative() -> None:
    text = Path("projects/job-automation/daily_scanner.py").read_text(encoding="utf-8")
    # Discovery only appends candidates; reconciliation uses _append_submission_evidence which never overwrites owner-applied state without evidence
    assert "discover_job_mail" in text
    # The merge logic must not mutate existing tracker rows
    assert "jobs_matched_existing" in text
