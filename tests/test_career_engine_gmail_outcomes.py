from __future__ import annotations

import copy

from career_engine.gmail_outcomes import (
    _append_outcome_evidence,
    classify_outcome_message,
    match_outcome_to_tracker,
)


class FakeTracker:
    def __init__(self, record, match=("job1", "external_job_id")):
        self.record = copy.deepcopy(record)
        self.match_result = match
        self.calls = []

    def get_job(self, job_id):
        assert job_id == "job1"
        return copy.deepcopy(self.record)

    def list_rows(self):
        return [{"job_id": "job1"}]

    def match(self, evidence):
        return self.match_result

    def update_job(self, job_id, changes, **kwargs):
        self.calls.append((job_id, copy.deepcopy(changes), copy.deepcopy(kwargs)))
        self.record["job"].update({key: value for key, value in changes.items() if key in self.record["job"]})
        for key in ("submission_package", "processing_state"):
            if key in changes:
                self.record[key] = copy.deepcopy(changes[key])
        return {"job_id": job_id}


def base_record(application_status="submitted", processing_status="applied"):
    return {
        "job": {
            "job_id": "job1",
            "company": "Example",
            "role": "Design Manager",
            "external_job_id": "1",
            "source_url": "",
            "application_status": application_status,
            "processing_status": processing_status,
            "outcome": "",
            "next_action": "Track response",
        },
        "processing_state": {"status": processing_status, "blockers": [], "route": {}},
        "submission_package": {"gmail_evidence": [{"message_id": "submit-1"}]},
    }


def message(**kwargs):
    data = {
        "id": "m1",
        "thread_id": "t1",
        "subject": "",
        "body": "",
        "from": "",
        "date": "2026-08-27",
        "urls": [],
        "label_ids": [],
    }
    data.update(kwargs)
    return data


def test_qiddiya_rejection_classification():
    got = classify_outcome_message(message(
        subject="Senior Manager - Development (Strategy) - Commercial Office - SPA 356 - Qiddiya Investment Company",
        body=(
            "Dear Abdelhamid Farah Thank you for your interest in the Senior Manager - Development (Strategy) - "
            "Commercial Office - SPA 356 with Qiddiya Investment Company. The role has now been filled, and "
            "unfortunately, you were not selected on this occasion."
        ),
        **{"from": "Qiddiya Investment Company <noreply@candidates.workablemail.com>"},
    ))
    assert got["signal"] == "rejected"
    assert got["company"] == "Qiddiya Investment Company"
    assert got["role"] == "Senior Manager - Development (Strategy) - Commercial Office - SPA 356"


def test_bechtel_successfactors_rejection_classification():
    got = classify_outcome_message(message(
        subject="Bechtel Careers - Application Status - 298027",
        body=(
            "REF: Design Project Manager (EXPO) - 298027 Dear Abdelhamid, we regret to inform you that our hiring "
            "team has not selected your application for further consideration."
        ),
        **{"from": "Bechtel Careers <system@successfactors.com>"},
    ))
    assert got["signal"] == "rejected"
    assert got["company"] == "Bechtel"
    assert got["role"] == "Design Project Manager (EXPO)"
    assert got["external_job_id"] == "298027"


def test_atkins_workday_rejection_classification():
    got = classify_outcome_message(message(
        subject="Application for the position of R-157342 Commercial/ Claims Manager (Open)",
        body=(
            "Dear Abdelhamid. After careful review we must unfortunately inform you that we are unable to offer you "
            "a position at this time."
        ),
        **{"from": '"Workday.Admin AtkinsRealis" <slihrms@myworkday.com>'},
    ))
    assert got["signal"] == "rejected"
    assert got["company"] == "AtkinsRealis"
    assert got["role"] == "Commercial/ Claims Manager"
    assert got["external_job_id"] == "R-157342"


def test_nova_rejection_classification():
    got = classify_outcome_message(message(
        subject="Thank you for your interest in Nova by Korn Ferry",
        body=(
            "Thank you for your interest in the Project Director position at Nova by Korn Ferry. We have decided to "
            "proceed with other applicants who more closely fit our needs at this time."
        ),
        **{"from": "Nova by Korn Ferry Hiring Team <notifications@nova-gc.ae>"},
    ))
    assert got["signal"] == "rejected"
    assert got["company"] == "Nova by Korn Ferry"
    assert got["role"] == "Project Director"


def test_egis_pending_classification():
    got = classify_outcome_message(message(
        subject="Thank you for applying to Egis Group",
        body=(
            "Thank you for submitting your application for the position of Director of Projects. Your application is "
            "queued for review and we will contact you shortly. Best regards, Egis Group Hiring Team"
        ),
        **{"from": "Egis Group <notification@recruitment.egis-group.com>"},
    ))
    assert got["signal"] == "pending"
    assert got["company"] == "Egis Group"
    assert got["role"] == "Director of Projects"


def test_interview_and_offer_classification():
    interview = classify_outcome_message(message(
        subject="Interview invitation - Senior Design Manager",
        body="We invite you to an interview for the Senior Design Manager position at Example Development.",
        **{"from": "Example Development <talent@example.com>"},
    ))
    assert interview["signal"] == "interview"
    offer = classify_outcome_message(message(
        subject="Offer letter - Design Manager",
        body="We are pleased to offer you the position of Design Manager at Example Development.",
        **{"from": "Example Development <talent@example.com>"},
    ))
    assert offer["signal"] == "offer"
    assert offer["company"] == "Example Development"
    assert offer["role"] == "Design Manager"


def test_sent_or_draft_never_classified():
    for label in ("SENT", "DRAFT"):
        assert classify_outcome_message(message(
            subject="Interview invitation",
            body="We invite you to an interview",
            label_ids=[label],
        )) is None


def test_rejection_preserves_application_status_and_terminalizes_processing(monkeypatch):
    tracker = FakeTracker(base_record())
    evidence = {
        "message_id": "rej-1", "thread_id": "t", "subject": "x", "sender": "y",
        "date": "2026-08-27", "signal": "rejected", "external_job_id": "1", "urls": [],
    }
    changed = _append_outcome_evidence(tracker, "job1", evidence, match_reason="external_job_id")
    assert changed is True
    _, changes, kwargs = tracker.calls[-1]
    assert changes["processing_status"] == "rejected"
    assert changes["outcome"] == "rejected"
    assert "application_status" not in changes
    assert changes["processing_state"]["status"] == "rejected"
    assert changes["processing_state"]["external_action_allowed"] is False
    assert kwargs["action"] == "rejected"


def test_interview_preserves_applied_lifecycle():
    tracker = FakeTracker(base_record(application_status="sent"))
    evidence = {
        "message_id": "int-1", "thread_id": "t", "subject": "x", "sender": "y",
        "date": "2026-08-27", "signal": "interview", "external_job_id": "1", "urls": [],
    }
    _append_outcome_evidence(tracker, "job1", evidence, match_reason="external_job_id")
    _, changes, _ = tracker.calls[-1]
    assert changes["outcome"] == "interview"
    assert "processing_status" not in changes
    assert "application_status" not in changes
    assert changes["processing_state"]["status"] == "applied"


def test_offer_requires_owner_review_and_never_marks_hired():
    tracker = FakeTracker(base_record())
    evidence = {
        "message_id": "offer-1", "thread_id": "t", "subject": "x", "sender": "y",
        "date": "2026-08-27", "signal": "offer", "external_job_id": "1", "urls": [],
    }
    _append_outcome_evidence(tracker, "job1", evidence, match_reason="external_job_id")
    _, changes, kwargs = tracker.calls[-1]
    assert changes["outcome"] == "offer"
    assert changes.get("outcome") != "hired"
    assert kwargs["requires_owner_review"] is True


def test_idempotent_by_message_id():
    record = base_record()
    record["submission_package"]["gmail_outcome_evidence"] = [{"message_id": "same"}]
    tracker = FakeTracker(record)
    evidence = {"message_id": "same", "signal": "rejected"}
    assert _append_outcome_evidence(tracker, "job1", evidence, match_reason="external_job_id") is False
    assert tracker.calls == []


def test_company_role_match_refuses_non_applied_record(monkeypatch):
    tracker = FakeTracker(base_record(application_status="not_submitted", processing_status="awaiting_owner_approval"))
    monkeypatch.setattr("career_engine.gmail_outcomes.match_submission_to_tracker", lambda tracker, evidence: ("job1", "company_role"))
    job_id, reason = match_outcome_to_tracker(tracker, {"company": "Example", "role": "Design Manager"})
    assert job_id == ""
    assert reason == "matched_non_applied_record"
