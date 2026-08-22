from __future__ import annotations

import inspect

from career_engine.gmail_discovery import classify_message_category, discover_job_mail
from career_engine.gmail_reconcile import classify_submission_message


def _message(*, subject: str, body: str = "", sender: str = "", urls: list[str] | None = None):
    return {
        "id": "gmail-regression",
        "thread_id": "thread-regression",
        "subject": subject,
        "body": body,
        "from": sender,
        "to": "hameedfarah@gmail.com",
        "label_ids": ["INBOX"],
        "urls": urls or [],
        "date": "Sat, 22 Aug 2026 18:00:00 +0300",
    }


def test_linkedin_confirmation_uses_applied_job_not_recommendation() -> None:
    applied = "https://www.linkedin.com/jobs/view/9876543210/"
    recommended = "https://www.linkedin.com/jobs/view/1111111111/"
    result = classify_submission_message(_message(
        subject="Abdelhamid, your application was sent to Khatib & Alami",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
        urls=[applied, recommended],
        body=(
            f"{applied}\nSenior Architect\nKhatib & Alami · Riyadh (On-site)\n"
            "Applied on August 22, 2026\nRecommended for you\n"
            f"{recommended}\nOther Role"
        ),
    ))
    assert result is not None
    assert result["company"] == "Khatib & Alami"
    assert result["role"] == "Senior Architect"
    assert result["external_job_id"] == "9876543210"
    assert result["application_url"] == applied
    assert result["applied_date"] == "August 22, 2026"
    assert result["urls"] == [applied]


def test_linkedin_application_viewed_is_status_not_job_alert() -> None:
    assert classify_message_category(_message(
        subject="Your application was viewed by Confidential",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )) == "application_status"


def test_linkedin_interview_beats_sender_based_job_alert_detection() -> None:
    assert classify_message_category(_message(
        subject="Interview assessment invitation",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )) == "interview_assessment"


def test_atkinsrealis_workday_receipt_is_classified() -> None:
    result = classify_submission_message(_message(
        subject="Application for the position of Senior Architectural Engineer - Madinah",
        sender='"Workday.Admin AtkinsRealis" slihrms@myworkday.com',
        body="Dear Abdelhamid, Thank you for submitting your application. Our Recruitment team will review your application.",
    ))
    assert result is not None
    assert result["company"] == "AtkinsRealis"
    assert result["role"] == "Senior Architectural Engineer - Madinah"


def test_explicit_nova_receipt_is_classified() -> None:
    result = classify_submission_message(_message(
        subject="Thank you for applying to Nova International General Contracting",
        body=(
            "Dear Abdelhamid, Thank you for applying for the Project Director position at "
            "Nova International General Contracting. Your application has been received successfully."
        ),
    ))
    assert result is not None
    assert result["company"] == "Nova International General Contracting"
    assert result["role"] == "Project Director"


def test_explicit_omrania_receipt_is_classified() -> None:
    result = classify_submission_message(_message(
        subject="Thank you for applying to Omrania",
        body=(
            "Dear Abdelhamid, Thank you for submitting your application for the position of Senior Architect. "
            "Your application is queued for review. Best regards, Omrania Hiring Team"
        ),
    ))
    assert result is not None
    assert result["company"] == "Omrania"
    assert result["role"] == "Senior Architect"


def test_discovery_default_scan_ceiling_is_500() -> None:
    assert inspect.signature(discover_job_mail).parameters["max_results"].default == 500
