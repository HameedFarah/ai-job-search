from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from career_engine.gmail_outcomes import (
    _append_outcome_evidence,
    classify_outcome_message,
    match_outcome_to_tracker,
)


class FakeTracker:
    def __init__(self, record):
        self.record = copy.deepcopy(record)
        self.calls = []

    def get_job(self, job_id):
        if job_id != "job1":
            raise KeyError(job_id)
        return copy.deepcopy(self.record)

    def list_rows(self):
        return [{"job_id": "job1"}]

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


class GmailOutcomeTests(unittest.TestCase):
    def test_qiddiya_rejection_classification(self):
        got = classify_outcome_message(message(
            subject="Senior Manager - Development (Strategy) - Commercial Office - SPA 356 - Qiddiya Investment Company",
            body=(
                "Dear Abdelhamid Farah Thank you for your interest in the Senior Manager - Development (Strategy) - "
                "Commercial Office - SPA 356 with Qiddiya Investment Company. The role has now been filled, and "
                "unfortunately, you were not selected on this occasion."
            ),
            **{"from": "Qiddiya Investment Company <noreply@candidates.workablemail.com>"},
        ))
        self.assertEqual(got["signal"], "rejected")
        self.assertEqual(got["company"], "Qiddiya Investment Company")
        self.assertEqual(got["role"], "Senior Manager - Development (Strategy) - Commercial Office - SPA 356")

    def test_bechtel_successfactors_rejection_classification(self):
        got = classify_outcome_message(message(
            subject="Bechtel Careers - Application Status - 298027",
            body=(
                "REF: Design Project Manager (EXPO) - 298027 Dear Abdelhamid, we regret to inform you that our hiring "
                "team has not selected your application for further consideration."
            ),
            **{"from": "Bechtel Careers <system@successfactors.com>"},
        ))
        self.assertEqual(got["signal"], "rejected")
        self.assertEqual(got["company"], "Bechtel")
        self.assertEqual(got["role"], "Design Project Manager (EXPO)")
        self.assertEqual(got["external_job_id"], "298027")

    def test_atkins_workday_rejection_classification(self):
        got = classify_outcome_message(message(
            subject="Application for the position of R-157342 Commercial/ Claims Manager (Open)",
            body=(
                "Dear Abdelhamid. After careful review we must unfortunately inform you that we are unable to offer you "
                "a position at this time."
            ),
            **{"from": '"Workday.Admin AtkinsRealis" <slihrms@myworkday.com>'},
        ))
        self.assertEqual(got["signal"], "rejected")
        self.assertEqual(got["company"], "AtkinsRealis")
        self.assertEqual(got["role"], "Commercial/ Claims Manager")
        self.assertEqual(got["external_job_id"], "R-157342")

    def test_nova_and_egis_classification(self):
        nova = classify_outcome_message(message(
            subject="Thank you for your interest in Nova by Korn Ferry",
            body=(
                "Thank you for your interest in the Project Director position at Nova by Korn Ferry. We have decided to "
                "proceed with other applicants who more closely fit our needs at this time."
            ),
            **{"from": "Nova by Korn Ferry Hiring Team <notifications@nova-gc.ae>"},
        ))
        self.assertEqual((nova["signal"], nova["company"], nova["role"]), ("rejected", "Nova by Korn Ferry", "Project Director"))

        egis = classify_outcome_message(message(
            subject="Thank you for applying to Egis Group",
            body=(
                "Thank you for submitting your application for the position of Director of Projects. Your application is "
                "queued for review and we will contact you shortly. Best regards, Egis Group Hiring Team"
            ),
            **{"from": "Egis Group <notification@recruitment.egis-group.com>"},
        ))
        self.assertEqual((egis["signal"], egis["company"], egis["role"]), ("pending", "Egis Group", "Director of Projects"))

    def test_interview_offer_and_sent_draft_guards(self):
        interview = classify_outcome_message(message(
            subject="Interview invitation - Senior Design Manager",
            body="We invite you to an interview for the Senior Design Manager position at Example Development.",
            **{"from": "Example Development <talent@example.com>"},
        ))
        self.assertEqual(interview["signal"], "interview")

        offer = classify_outcome_message(message(
            subject="Offer letter - Design Manager",
            body="We are pleased to offer you the position of Design Manager at Example Development.",
            **{"from": "Example Development <talent@example.com>"},
        ))
        self.assertEqual((offer["signal"], offer["company"], offer["role"]), ("offer", "Example Development", "Design Manager"))
        for label in ("SENT", "DRAFT"):
            self.assertIsNone(classify_outcome_message(message(
                subject="Interview invitation",
                body="We invite you to an interview",
                label_ids=[label],
            )))

    def test_rejection_preserves_submission_identity(self):
        tracker = FakeTracker(base_record())
        evidence = {
            "message_id": "rej-1", "thread_id": "t", "subject": "x", "sender": "y",
            "date": "2026-08-27", "signal": "rejected", "external_job_id": "1", "urls": [],
        }
        self.assertTrue(_append_outcome_evidence(tracker, "job1", evidence, match_reason="external_job_id"))
        _, changes, kwargs = tracker.calls[-1]
        self.assertEqual(changes["processing_status"], "rejected")
        self.assertEqual(changes["outcome"], "rejected")
        self.assertNotIn("application_status", changes)
        self.assertEqual(changes["processing_state"]["status"], "rejected")
        self.assertFalse(changes["processing_state"]["external_action_allowed"])
        self.assertEqual(kwargs["action"], "rejected")

    def test_interview_and_offer_preserve_application_lifecycle(self):
        tracker = FakeTracker(base_record(application_status="sent"))
        _append_outcome_evidence(tracker, "job1", {
            "message_id": "int-1", "thread_id": "t", "subject": "x", "sender": "y",
            "date": "2026-08-27", "signal": "interview", "external_job_id": "1", "urls": [],
        }, match_reason="external_job_id")
        _, changes, _ = tracker.calls[-1]
        self.assertEqual(changes["outcome"], "interview")
        self.assertNotIn("processing_status", changes)
        self.assertNotIn("application_status", changes)
        self.assertEqual(changes["processing_state"]["status"], "applied")

        tracker = FakeTracker(base_record())
        _append_outcome_evidence(tracker, "job1", {
            "message_id": "offer-1", "thread_id": "t", "subject": "x", "sender": "y",
            "date": "2026-08-27", "signal": "offer", "external_job_id": "1", "urls": [],
        }, match_reason="external_job_id")
        _, changes, kwargs = tracker.calls[-1]
        self.assertEqual(changes["outcome"], "offer")
        self.assertNotEqual(changes["outcome"], "hired")
        self.assertTrue(kwargs["requires_owner_review"])

    def test_idempotent_by_message_id(self):
        record = base_record()
        record["submission_package"]["gmail_outcome_evidence"] = [{"message_id": "same"}]
        tracker = FakeTracker(record)
        self.assertFalse(_append_outcome_evidence(tracker, "job1", {"message_id": "same", "signal": "rejected"}, match_reason="external_job_id"))
        self.assertEqual(tracker.calls, [])

    def test_company_role_match_refuses_non_applied_record(self):
        tracker = FakeTracker(base_record(application_status="not_submitted", processing_status="awaiting_owner_approval"))
        with patch("career_engine.gmail_outcomes.match_submission_to_tracker", return_value=("job1", "company_role")):
            job_id, reason = match_outcome_to_tracker(tracker, {"company": "Example", "role": "Design Manager"})
        self.assertEqual(job_id, "")
        self.assertEqual(reason, "matched_non_applied_record")


if __name__ == "__main__":
    unittest.main()
