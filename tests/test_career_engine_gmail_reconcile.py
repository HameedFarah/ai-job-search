from __future__ import annotations

from career_engine.gmail_reconcile import classify_submission_message, match_submission_to_tracker


def message(*, subject: str, body: str, sender: str = "", labels: list[str] | None = None, urls: list[str] | None = None, to: str = ""):
    return {
        "id": "gmail-1",
        "thread_id": "thread-1",
        "subject": subject,
        "body": body,
        "from": sender,
        "to": to,
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


def test_linkedin_confirmation_uses_first_applied_job_not_recommendations() -> None:
    applied = "https://www.linkedin.com/jobs/view/9876543210/"
    recommended = "https://www.linkedin.com/jobs/view/1111111111/"
    result = classify_submission_message(message(
        subject="Abdelhamid, your application was sent to Khatib & Alami",
        sender="LinkedIn <jobs-noreply@linkedin.com>",
        urls=[applied, recommended],
        body=(f"{applied}\nSenior Architect\nKhatib & Alami · Riyadh (On-site)\n"
              "Applied on August 22, 2026\nRecommended for you\n" + recommended + "\nOther Role"),
    ))
    assert result is not None
    assert result["company"] == "Khatib & Alami"
    assert result["role"] == "Senior Architect"
    assert result["external_job_id"] == "9876543210"
    assert result["application_url"] == applied
    assert result["applied_date"] == "August 22, 2026"
    assert result["urls"] == [applied]


def test_linkedin_alert_without_submission_confirmation_is_ignored() -> None:
    assert classify_submission_message(message(
        subject="Jobs you may be interested in",
        sender="LinkedIn <jobalerts-noreply@linkedin.com>",
        urls=["https://www.linkedin.com/jobs/view/1111111111/"],
        body="Recommended jobs for you",
    )) is None


def test_qiddiya_workable_application_confirmation_is_classified() -> None:
    result = classify_submission_message(message(
        subject="Senior Director - Design - Qiddiya Investment Company",
        sender="Qiddiya Investment Company <noreply@candidates.workablemail.com>",
        body="Dear Abdelhamid Farah, Thank you for your application for the Senior Director - Design position at Qiddiya Investment Company.",
    ))
    assert result is not None
    assert result["company"] == "Qiddiya Investment Company"
    assert result["role"] == "Senior Director - Design"
    assert result["external_job_id"] == ""
    assert result["route"] == "portal"
    assert result["signal"] == "workable_application_receipt"


def test_oracle_taleo_wsp_requisition_confirmation_is_classified() -> None:
    result = classify_submission_message(message(
        subject="Your recent job application for Design Manager - BIM & GIS - 93332",
        sender="Talent Acquisition Team <No.Reply.Talent.Acquisition@Horizon-Oracle.wsp.com>",
        body=(
            "Hello Abdelhmaid, We received your job application for Design Manager - BIM & GIS - 93332. "
            "If your profile corresponds to our requirements, a member of our Recruiting team will contact you. "
            "Kind regards, WSP Talent Acquisition Team"
        ),
    ))
    assert result is not None
    assert result["company"] == "WSP"
    assert result["role"] == "Design Manager - BIM & GIS"
    assert result["external_job_id"] == "93332"
    assert result["route"] == "portal"
    assert result["signal"] == "oracle_taleo_submission_confirmation"


def test_oracle_taleo_without_identifiable_company_is_not_classified() -> None:
    result = classify_submission_message(message(
        subject="Your recent job application for Design Manager - BIM & GIS - 93332",
        sender="Talent Acquisition Team <no-reply@example-corp.net>",
        body="We received your job application for Design Manager - BIM & GIS - 93332.",
    ))
    assert result is None


def test_workable_application_receipt_extracts_requisition_from_role() -> None:
    result = classify_submission_message(message(
        subject="Manager - Design Governance - 20004876 - Qiddiya Investment Company",
        sender="Qiddiya Investment Company <noreply@candidates.workablemail.com>",
        body=(
            "Dear Abdelhamid Farah, Thank you for your application for the "
            "Manager - Design Governance - 20004876 position at Qiddiya Investment Company. "
            "Best regards, Qiddiya Investment Company"
        ),
    ))
    assert result is not None
    assert result["company"] == "Qiddiya Investment Company"
    assert result["role"] == "Manager - Design Governance"
    assert result["external_job_id"] == "20004876"
    assert result["signal"] == "workable_application_receipt"


def test_gmail_alias_recipient_is_accepted_for_confirmation() -> None:
    # The authenticated mailbox is hameedo@gmail.com; career mail is addressed
    # to the outward alias hameedfarah@gmail.com and must be processed.
    result = classify_submission_message(message(
        subject="Manager - Design Governance - 20004876 - Qiddiya Investment Company",
        sender="Qiddiya Investment Company <noreply@candidates.workablemail.com>",
        body="Thank you for your application for the Manager - Design Governance - 20004876 position at Qiddiya Investment Company.",
        to="hameedfarah@gmail.com",
    ))
    assert result is not None
    assert result["external_job_id"] == "20004876"


def test_sent_application_email_requires_outward_identity() -> None:
    result = classify_submission_message(message(
        subject="Abdelhamid Farah - Senior Architect / Project Director",
        sender="Abdelhmaid Farah <hameedo@gmail.com>",
        labels=["SENT"],
        to="db@bottegasearch.com",
        body="Dear Mr Bowler, I am writing to apply for the Senior Architect / Project Director position with Human Advancement through Investment and Development.",
    ))
    assert result is None


def test_sent_application_email_requires_verified_external_recipient() -> None:
    # Self-addressed review copy is not an application.
    assert classify_submission_message(message(
        subject="Abdelhamid Farah - Senior Architect / Project Director",
        sender="Abdelhamid Farah <hameedfarah@gmail.com>",
        labels=["SENT"],
        to="hameedo@gmail.com",
        body="Dear Mr Bowler, I am writing to apply for the Senior Architect / Project Director position with Human Advancement through Investment and Development.",
    )) is None
    assert classify_submission_message(message(
        subject="Abdelhamid Farah - Senior Architect / Project Director",
        sender="Abdelhamid Farah <hameedfarah@gmail.com>",
        labels=["SENT"],
        to="hameedfarah@gmail.com",
        body="Dear Mr Bowler, I am writing to apply for the Senior Architect / Project Director position with Human Advancement through Investment and Development.",
    )) is None
    # No recipient at all is not an application.
    assert classify_submission_message(message(
        subject="Abdelhamid Farah - Senior Architect / Project Director",
        sender="Abdelhamid Farah <hameedfarah@gmail.com>",
        labels=["SENT"],
        body="Dear Mr Bowler, I am writing to apply for the Senior Architect / Project Director position with Human Advancement through Investment and Development.",
    )) is None
    # A genuine external recipient is application evidence.
    result = classify_submission_message(message(
        subject="Abdelhamid Farah - Senior Architect / Project Director",
        sender="Abdelhamid Farah <hameedfarah@gmail.com>",
        labels=["SENT"],
        to="db@bottegasearch.com",
        body="Dear Mr Bowler, I am writing to apply for the Senior Architect / Project Director position with Human Advancement through Investment and Development.",
    ))
    assert result is not None
    assert result["route"] == "email"
    assert result["company"] == "Human Advancement through Investment and Development"
    assert result["role"] == "Senior Architect / Project Director"


def test_sent_email_with_role_only_is_not_classified() -> None:
    result = classify_submission_message(message(
        subject="Abdelhamid Farah - Senior Architect / Project Director",
        sender="Abdelhamid Farah <hameedfarah@gmail.com>",
        labels=["SENT"],
        to="db@bottegasearch.com",
        body="Dear Mr Bowler, please find my CV for the opportunity we discussed.",
    ))
    assert result is None


def test_buro_happold_confirmation_extracts_role_and_reference() -> None:
    result = classify_submission_message(message(
        subject="Thank you for applying for the role of Senior Design Manager – Structures",
        sender="no-reply@burohappold.com",
        body="Thank you for considering Buro Happold and for your application for the role - Senior Design Manager – Structures (burohappold/TP/652/2261).",
    ))
    assert result is not None
    assert result["company"] == "Buro Happold"
    assert result["role"] == "Senior Design Manager – Structures"
    assert result["external_job_id"] == "2261"
    assert result["route"] == "portal"
    assert result["signal"] == "buro_happold_submission_confirmation"


def test_workday_submission_confirmation_is_classified() -> None:
    result = classify_submission_message(message(
        subject="Your Parsons Job Application Has Been Received",
        sender="Parsons Workday <Parsons@myworkday.com>",
        body="Your resume has been successfully submitted for the position of Architectural Design Manager. If your qualifications are a fit...",
    ))
    assert result is not None
    assert result["company"] == "Parsons"
    assert result["role"] == "Architectural Design Manager"


def test_atkinsrealis_workday_receipt_uses_explicit_sender_company() -> None:
    result = classify_submission_message(message(
        subject="Application for the position of Senior Architectural Engineer - Madinah",
        sender='"Workday.Admin AtkinsRealis" slihrms@myworkday.com',
        body="Dear Abdelhamid, Thank you for submitting your application. Our Recruitment team will review your application.",
    ))
    assert result is not None
    assert result["company"] == "AtkinsRealis"
    assert result["role"] == "Senior Architectural Engineer - Madinah"


def test_nova_korn_ferry_receipt_extracts_explicit_company_and_role() -> None:
    result = classify_submission_message(message(
        subject="Thank you for applying to Nova International General Contracting",
        body="Dear Abdelhamid, Thank you for applying for the Project Director position at Nova International General Contracting. Your application has been received successfully.",
    ))
    assert result is not None
    assert result["company"] == "Nova International General Contracting"
    assert result["role"] == "Project Director"


def test_omrania_egis_receipt_extracts_subject_company_and_body_role() -> None:
    result = classify_submission_message(message(
        subject="Thank you for applying to Omrania",
        body="Dear Abdelhamid, Thank you for submitting your application for the position of Senior Architect. Your application is queued for review... Best regards, Omrania Hiring Team",
    ))
    assert result is not None
    assert result["company"] == "Omrania"
    assert result["role"] == "Senior Architect"


def test_generic_affiliate_application_is_not_classified() -> None:
    assert classify_submission_message(message(
        subject="Thank you for applying",
        sender="Hostinger Offers <offers@hostinger.example>",
        body="Thank you for applying. Explore our partner offers and earn rewards.",
    )) is None


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
        to="ross@beresfordwilson.com",
        body="Dear Ross, I am writing to apply for the Technical Project Manager position with Beresford Wilson and Partners. Please find my CV attached.",
    ))
    assert result is not None
    assert result["route"] == "email"
    assert result["company"] == "Beresford Wilson and Partners"
    assert result["role"] == "Technical Project Manager"


def test_gmail_draft_from_career_sender_is_not_submission_evidence() -> None:
    result = classify_submission_message(message(
        subject="Abdelhamid Farah - Technical Project Manager",
        sender="hameedfarah@gmail.com",
        labels=["DRAFT"],
        body="Dear Ross, I am writing to apply for the Technical Project Manager position with BWP Ltd. Please find my CV attached.",
    ))
    assert result is None


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


def record(job_id: str, *, company: str, role: str, url: str = "", ext: str = "", status: str = "blocked", app: str = "not_submitted"):
    return {
        "job": {
            "job_id": job_id,
            "company": company,
            "role": role,
            "external_job_id": ext,
            "source_url": url,
            "processing_status": status,
            "application_status": app,
        },
        "processing_state": {"route": {"application_url": url}},
    }


class RecordsTracker:
    def __init__(self, records: list[dict]) -> None:
        self._records = {item["job"]["job_id"]: item for item in records}

    def list_rows(self):
        return [item["job"] for item in self._records.values()]

    def get_job(self, job_id: str):
        return self._records[job_id]


def test_superseded_duplicate_is_ignored_when_matching() -> None:
    tracker = RecordsTracker([
        record("s" * 20, company="KEO International Consultants", role="Commercial Manager", status="superseded", url="https://careers.keo.com/jobs/11111"),
        record("c" * 20, company="KEO International Consultants", role="Commercial Manager", status="blocked", url="https://www.linkedin.com/jobs/view/22222/"),
    ])
    job_id, reason = match_submission_to_tracker(tracker, {
        "company": "KEO International Consultants",
        "role": "Commercial Manager",
        "external_job_id": "",
        "urls": [],
    })
    assert job_id == "c" * 20
    assert reason == "company_role"


def test_active_applied_record_breaks_company_role_ambiguity() -> None:
    # KEO shape: a rejected out-of-lane duplicate plus the record that actually
    # holds the application. The confirmation must attach to the applied record.
    tracker = RecordsTracker([
        record("r" * 20, company="KEO International Consultants", role="Commercial Manager", status="rejected", url="https://careers.keo.com/jobs/12437", ext="12437"),
        record("a" * 20, company="KEO International Consultants", role="Commercial Manager", status="applied", app="submitted", url="https://www.linkedin.com/jobs/view/4455274430/", ext="4455274430"),
    ])
    job_id, reason = match_submission_to_tracker(tracker, {
        "company": "KEO International Consultants",
        "role": "Commercial Manager",
        "external_job_id": "",
        "urls": ["https://keo.icims.com/icims2/?r=3C08250692"],
    })
    assert job_id == "a" * 20
    assert reason == "company_role_active_record"


def test_url_family_breaks_company_role_ambiguity() -> None:
    # Qiddiya SPA 356 shape: identical company+role on a LinkedIn syndication
    # and a Workable record, none applied; the Workable confirmation URL
    # family resolves to the Workable-sourced record.
    tracker = RecordsTracker([
        record("l" * 20, company="Qiddiya | القدية", role="Senior Manager - Development (Strategy) - Commercial Office - SPA 356", status="rejected", url="https://www.linkedin.com/jobs/view/4448511150/", ext="4448511150"),
        record("w" * 20, company="Qiddiya Investment Company", role="Senior Manager - Development (Strategy) - Commercial Office - SPA 356", status="rejected", url="https://apply.workable.com/j/65F4EA6E83", ext="qiddiya-investment-company-1:65F4EA6E83"),
    ])
    job_id, reason = match_submission_to_tracker(tracker, {
        "company": "Qiddiya Investment Company",
        "role": "Senior Manager - Development (Strategy) - Commercial Office - SPA 356",
        "external_job_id": "5313700",
        "urls": [
            "https://qiddiya-investment-company-1.workable.com/jobs/5313700",
            "https://www.linkedin.com/in/abd-farah/",
        ],
    })
    assert job_id == "w" * 20
    assert reason == "company_role_url_family"


def test_ambiguous_title_company_remains_unmatched() -> None:
    # Same company+role on two records, none applied, no ATS family overlap
    # between evidence and candidates: stay ambiguous for manual review.
    tracker = RecordsTracker([
        record("r1" + "0" * 18, company="KEO International Consultants", role="Commercial Manager", status="rejected", url="https://careers.keo.com/jobs/12437", ext="12437"),
        record("r2" + "0" * 18, company="KEO International Consultants", role="Commercial Manager", status="blocked", url="https://example-keo.com/jobs/12438", ext="12438"),
    ])
    job_id, reason = match_submission_to_tracker(tracker, {
        "company": "KEO International Consultants",
        "role": "Commercial Manager",
        "external_job_id": "",
        "urls": ["https://keo.icims.com/icims2/?r=3C08250692"],
    })
    assert job_id == ""
    assert reason == "ambiguous_company_role"
