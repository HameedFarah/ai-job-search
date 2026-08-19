from __future__ import annotations

from career_engine.gmail_reconcile import classify_submission_message, match_submission_to_tracker


def message(*, subject: str, body: str, sender: str = "", labels: list[str] | None = None, urls: list[str] | None = None):
    return {
        "id": "gmail-1",
        "thread_id": "thread-1",
        "subject": subject,
        "body": body,
        "from": sender,
        "label_ids": labels or ["INBOX"],
        "urls": urls or [],
        "date": "Wed, 19 Aug 2026 20:00:00 +0300",
    }


def test_workable_submission_confirmation_is_classified() -> None:
    result = classify_submission_message(message(
        subject="Thanks for applying to Qiddiya Investment Company",
        sender="Workable <noreply@candidates.workablemail.com>",
        body="Your application for the Senior Director - Design job was submitted successfully.",
        urls=["https://qiddiya-investment-company-1.workable.com/jobs/5905285"],
    ))
    assert result is not None
    assert result["company"] == "Qiddiya Investment Company"
    assert result["role"] == "Senior Director - Design"
    assert result["external_job_id"] == "5905285"
    assert result["route"] == "portal"


def test_workday_submission_confirmation_is_classified() -> None:
    result = classify_submission_message(message(
        subject="Your Parsons Job Application Has Been Received",
        sender="Parsons Workday <Parsons@myworkday.com>",
        body="Your resume has been successfully submitted for the position of Architectural Design Manager. If your qualifications are a fit...",
    ))
    assert result is not None
    assert result["company"] == "Parsons"
    assert result["role"] == "Architectural Design Manager"


def test_successfactors_requisition_is_preserved() -> None:
    result = classify_submission_message(message(
        subject="Bechtel Careers – Application Confirmation - 298027",
        sender="Bechtel Careers <system@successfactors.com>",
        body="REF: Design Project Manager (EXPO) - 298027\n\nWe are pleased to confirm receipt of your application.",
    ))
    assert result is not None
    assert result["company"] == "Bechtel"
    assert result["role"] == "Design Project Manager (EXPO)"
    assert result["external_job_id"] == "298027"


def test_icims_started_application_is_not_submission() -> None:
    result = classify_submission_message(message(
        subject="You've started your job application!",
        sender='"KEO International Consultants @ icims" <keo+autoreply@talent.icims.com>',
        body="You have started your job application for Director of Architecture & Engineering.",
    ))
    assert result is None


def test_icims_thank_you_is_submission() -> None:
    result = classify_submission_message(message(
        subject="Thank you for applying - Director of Architecture & Engineering - Saudi Arabia-Riyadh",
        sender='"KEO International Consultants @ icims" <keo+autoreply@talent.icims.com>',
        body="Thank you very much for your recent application to the Director of Architecture & Engineering position at Saudi Arabia-Riyadh.",
    ))
    assert result is not None
    assert result["company"] == "KEO International Consultants"
    assert result["role"] == "Director of Architecture & Engineering"


def test_real_sent_application_email_is_submission_evidence() -> None:
    result = classify_submission_message(message(
        subject="Abdelhamid Farah - Technical Project Manager",
        sender="Abdelhamid Farah <hameedfarah@gmail.com>",
        labels=["SENT"],
        body="Dear Ross, I am writing to apply for the Technical Project Manager position with Beresford Wilson and Partners. Please find my CV attached.",
    ))
    assert result is not None
    assert result["route"] == "email"
    assert result["company"] == "Beresford Wilson and Partners"
    assert result["role"] == "Technical Project Manager"


class FakeTracker:
    def __init__(self) -> None:
        self.records = {
            "aaaaaaaaaaaaaaaaaaaa": {
                "job": {
                    "job_id": "aaaaaaaaaaaaaaaaaaaa",
                    "company": "Bechtel",
                    "role": "Design Project Manager (EXPO)",
                    "external_job_id": "297967",
                    "source_url": "https://jobs.bechtel.com/job/297967",
                    "processing_status": "generation_ready",
                },
                "processing_state": {"route": {"application_url": "https://jobs.bechtel.com/job/297967"}},
            },
            "bbbbbbbbbbbbbbbbbbbb": {
                "job": {
                    "job_id": "bbbbbbbbbbbbbbbbbbbb",
                    "company": "Bechtel",
                    "role": "Design Project Manager (EXPO)",
                    "external_job_id": "298027",
                    "source_url": "https://jobs.bechtel.com/job/298027",
                    "processing_status": "generation_ready",
                },
                "processing_state": {"route": {"application_url": "https://jobs.bechtel.com/job/298027"}},
            },
        }

    def list_rows(self):
        return [record["job"] for record in self.records.values()]

    def get_job(self, job_id: str):
        return self.records[job_id]


def test_same_title_bechtel_requisitions_dedupe_by_external_id() -> None:
    tracker = FakeTracker()
    job_id, reason = match_submission_to_tracker(tracker, {
        "company": "Bechtel",
        "role": "Design Project Manager (EXPO)",
        "external_job_id": "298027",
        "urls": [],
    })
    assert job_id == "bbbbbbbbbbbbbbbbbbbb"
    assert reason == "external_job_id"
