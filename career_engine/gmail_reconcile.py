from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import load_config
from .gmail import CAREER_OUTWARD_EMAIL, search_messages
from .pipeline import _load_tracker


SUBMISSION_PHRASES = (
    "submitted successfully",
    "successfully submitted",
    "application has been received",
    "application was received",
    "confirm receipt of your application",
    "confirm receipt of your resume",
    "thank you for applying",
    "application confirmation",
    "application was sent to",
)

_COMPANY_ALIASES = {
    "qiddiya investment company": "qiddiya",
    "qiddiya القدية": "qiddiya",
    "qiddiya": "qiddiya",
    "keo international consultants": "keo",
    "keo": "keo",
    "parsons corporation": "parsons",
    "parsons": "parsons",
    "bechtel careers": "bechtel",
    "bechtel": "bechtel",
    "beresford wilson and partners": "beresford wilson partners",
    "beresford wilson partners": "beresford wilson partners",
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    value = _text(value).lower().replace("&", " and ")
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _company_key(value: Any) -> str:
    normalized = _key(value)
    return _COMPANY_ALIASES.get(normalized, normalized)


def _url_key(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    linked_in = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d+)", raw, re.I)
    if linked_in:
        return f"linkedin-job:{linked_in.group(1)}"
    raw = raw.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return raw.lower()


def _message_day(message: dict[str, Any]) -> str:
    value = _text(message.get("date"))
    return value


def _extract_workable(subject: str, body: str, urls: list[str]) -> dict[str, str] | None:
    match = re.search(r"^Thanks for applying to\s+(.+)$", subject, re.I)
    if not match:
        return None
    company = _text(match.group(1))
    role_match = re.search(r"application for the\s+(.+?)\s+job was submitted successfully", body, re.I | re.S)
    role = _text(role_match.group(1)) if role_match else ""
    external = ""
    for url in urls:
        job_match = re.search(r"workable\.com/jobs/(\d+)", url, re.I)
        if job_match:
            external = job_match.group(1)
            break
    return {"company": company, "role": role, "external_job_id": external, "route": "portal", "signal": "workable_submission_confirmation"}


def _extract_qiddiya_workable(subject: str, body: str, sender: str) -> dict[str, str] | None:
    if not re.search(r"@candidates\.workablemail\.com\b", sender, re.I):
        return None
    match = re.fullmatch(
        r"Senior Director - Design - Qiddiya Investment Company", subject, re.I
    )
    if not match:
        return None
    if not re.match(
        r"Dear\s+Abdelhamid Farah,\s+Thank you for your application for the "
        r"Senior Director - Design position at Qiddiya Investment Company\.",
        body,
        re.I | re.S,
    ):
        return None
    return {
        "company": "Qiddiya Investment Company",
        "role": "Senior Director - Design",
        "external_job_id": "",
        "route": "portal",
        "signal": "qiddiya_workable_submission_confirmation",
    }


def _extract_buro_happold(subject: str, body: str, sender: str) -> dict[str, str] | None:
    if not re.search(r"@burohappold\.com\b", sender, re.I):
        return None
    subject_match = re.fullmatch(r"Thank you for applying for the role of (.+)", subject, re.I)
    body_match = re.search(
        r"your application for the role\s*-\s*(.+?)\s*\(burohappold/TP/\d+/(\d+)\)",
        body,
        re.I | re.S,
    )
    if not body_match:
        return None
    role = _text(body_match.group(1))
    external = body_match.group(2)
    if subject_match and _key(subject_match.group(1)) != _key(role):
        return None
    return {
        "company": "Buro Happold",
        "role": role,
        "external_job_id": external,
        "external_job_reference": f"TP/652/{external}",
        "route": "portal",
        "signal": "buro_happold_submission_confirmation",
    }


def _extract_workday(subject: str, body: str) -> dict[str, str] | None:
    match = re.search(r"^Your\s+(.+?)\s+Job Application Has Been Received$", subject, re.I)
    if not match:
        return None
    company = _text(match.group(1))
    role_match = re.search(r"successfully submitted for the position of\s+(.+?)(?:\s*\.\s*|\s+If\b)", body, re.I | re.S)
    role = _text(role_match.group(1)) if role_match else ""
    return {"company": company, "role": role, "external_job_id": "", "route": "portal", "signal": "workday_submission_confirmation"}


def _extract_successfactors(subject: str, body: str) -> dict[str, str] | None:
    match = re.search(r"^(.+?)\s+Careers\s*[–—-]\s*Application Confirmation\s*-\s*([A-Za-z0-9_-]+)$", subject, re.I)
    if not match:
        return None
    company = _text(match.group(1))
    external = _text(match.group(2))
    ref_match = re.search(r"REF:\s*(.+?)\s*-\s*([A-Za-z0-9_-]+)\s*(?:\s|$)", body, re.I)
    role = _text(ref_match.group(1)) if ref_match else ""
    if ref_match and ref_match.group(2):
        external = _text(ref_match.group(2))
    return {"company": company, "role": role, "external_job_id": external, "route": "portal", "signal": "successfactors_submission_confirmation"}


def _extract_icims(subject: str, body: str, sender: str) -> dict[str, str] | None:
    if not re.search(r"thank you for applying", subject, re.I):
        return None
    company = ""
    sender_match = re.match(r"\"?([^\"<]+?)\s*@\s*icims", sender, re.I)
    if sender_match:
        company = _text(sender_match.group(1))
    role_match = re.search(r"recent application to the\s+(.+?)\s+position at\s+(.+?)(?:\.|\n)", body, re.I | re.S)
    role = _text(role_match.group(1)) if role_match else ""
    if not role:
        subject_match = re.match(r"Thank you for applying\s*-\s*(.+?)\s*-\s*(?:Saudi Arabia|Riyadh|Jeddah|KSA)\b", subject, re.I)
        role = _text(subject_match.group(1)) if subject_match else ""
    return {"company": company, "role": role, "external_job_id": "", "route": "portal", "signal": "icims_submission_confirmation"}


def _extract_linkedin(subject: str, body: str, urls: list[str]) -> dict[str, Any] | None:
    match = re.search(r"your application was sent to\s+(.+)$", subject, re.I)
    if not match:
        return None
    company = _text(match.group(1))
    primary = next((url for url in urls if re.search(r"linkedin\.com/(?:comm/)?jobs/view/", url, re.I)), "")
    role = ""
    body_match = re.search(r"Your application was sent to\s+(.+?)\s+(.+?)\s+\1\s+(?:Riyadh|Jeddah|Dubai|Abu Dhabi|KSA)\b", body, re.I)
    if body_match:
        company = _text(body_match.group(1))
        role = _text(body_match.group(2))
    return {"company": company, "role": role, "external_job_id": "", "route": "portal", "signal": "linkedin_submission_confirmation", "evidence_urls": [primary] if primary else []}


def _extract_sent(message: dict[str, Any], subject: str, body: str) -> dict[str, str] | None:
    labels = {str(value).upper() for value in message.get("label_ids", [])}
    if "DRAFT" in labels:
        return None
    sender = _text(message.get("from")).lower()
    if "SENT" not in labels and CAREER_OUTWARD_EMAIL.lower() not in sender:
        return None
    body_match = re.search(r"(?:writing|emailing) to apply for the\s+(.+?)\s+position\s+(?:with|at)\s+(.+?)(?:\.|,|\n)", body, re.I | re.S)
    role = ""
    company = ""
    if body_match:
        role = _text(body_match.group(1))
        company = _text(body_match.group(2))
    if not role:
        subject_match = re.match(r"Abdelhamid Farah\s*-\s*(.+)$", subject, re.I)
        role = _text(subject_match.group(1)) if subject_match else ""
    if not role or not company:
        return None
    return {"company": company, "role": role, "external_job_id": "", "route": "email", "signal": "sent_application_email"}


def classify_submission_message(message: dict[str, Any]) -> dict[str, Any] | None:
    subject = _text(message.get("subject"))
    body = _text(message.get("body"))
    sender = _text(message.get("from"))
    urls = [str(value) for value in message.get("urls", []) if value]
    lowered = f"{subject}\n{body}".lower()
    if "started your job application" in lowered and not any(phrase in lowered for phrase in SUBMISSION_PHRASES):
        return None

    extracted = (
        _extract_qiddiya_workable(subject, body, sender)
        or _extract_buro_happold(subject, body, sender)
        or _extract_workable(subject, body, urls)
        or _extract_workday(subject, body)
        or _extract_successfactors(subject, body)
        or _extract_icims(subject, body, sender)
        or _extract_linkedin(subject, body, urls)
        or _extract_sent(message, subject, body)
    )
    if not extracted:
        if not any(phrase in lowered for phrase in SUBMISSION_PHRASES):
            return None
        return None

    return {
        **extracted,
        "message_id": _text(message.get("id")),
        "thread_id": _text(message.get("thread_id")),
        "subject": subject,
        "sender": sender,
        "date": _message_day(message),
        "urls": extracted.get("evidence_urls", urls),
    }


def _tracker_records(tracker: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in tracker.list_rows():
        try:
            record = tracker.get_job(row["job_id"])
        except KeyError:
            continue
        if _key((record.get("job") or {}).get("processing_status")) == "superseded":
            continue
        records.append(record)
    return records


def _record_urls(record: dict[str, Any]) -> set[str]:
    job = record.get("job") or {}
    state = record.get("processing_state") or {}
    route = state.get("route") or {}
    values = [job.get("source_url"), route.get("application_url"), record.get("application_url")]
    return {_url_key(value) for value in values if _url_key(value)}


def _matches_external(record: dict[str, Any], external: str) -> bool:
    if not external:
        return False
    job = record.get("job") or {}
    if _key(job.get("external_job_id")) == _key(external):
        return True
    token = re.compile(rf"(?<![A-Za-z0-9]){re.escape(external)}(?![A-Za-z0-9])", re.I)
    return any(token.search(str(value or "")) for value in (
        job.get("source_url"),
        (record.get("processing_state") or {}).get("route", {}).get("application_url"),
    ))


def match_submission_to_tracker(tracker: Any, evidence: dict[str, Any]) -> tuple[str, str]:
    records = _tracker_records(tracker)
    external = _text(evidence.get("external_job_id"))
    reference = _text(evidence.get("external_job_reference"))
    if reference:
        exact_reference_matches = [record for record in records if _key((record.get("job") or {}).get("external_job_id")) == _key(reference)]
        if len(exact_reference_matches) == 1:
            return str(exact_reference_matches[0]["job"]["job_id"]), "external_job_id"
        if len(exact_reference_matches) > 1:
            return "", "ambiguous_external_job_id"
    if external:
        matches = [record for record in records if _matches_external(record, external)]
        if len(matches) == 1:
            return str(matches[0]["job"]["job_id"]), "external_job_id"
        if len(matches) > 1:
            return "", "ambiguous_external_job_id"

    evidence_urls = {_url_key(value) for value in evidence.get("urls", []) if _url_key(value)}
    if evidence_urls:
        matches = [record for record in records if evidence_urls.intersection(_record_urls(record))]
        if len(matches) == 1:
            return str(matches[0]["job"]["job_id"]), "source_url"
        if len(matches) > 1:
            return "", "ambiguous_source_url"

    company = _company_key(evidence.get("company"))
    role = _key(evidence.get("role"))
    if company and role:
        matches = [
            record for record in records
            if _company_key((record.get("job") or {}).get("company")) == company
            and _key((record.get("job") or {}).get("role")) == role
        ]
        if len(matches) == 1:
            return str(matches[0]["job"]["job_id"]), "company_role"
        if len(matches) > 1:
            return "", "ambiguous_company_role"
    return "", "unmatched"


def _evidence_stub_id(evidence: dict[str, Any]) -> str:
    material = "|".join((
        _company_key(evidence.get("company")),
        _key(evidence.get("role")),
        _key(evidence.get("external_job_id")),
        _url_key((evidence.get("urls") or [""])[0] if evidence.get("urls") else ""),
    ))
    return hashlib.sha256(f"gmail-submission|{material}".encode("utf-8")).hexdigest()[:20]


def _create_evidence_stub(tracker: Any, evidence: dict[str, Any]) -> str:
    company = _text(evidence.get("company")) or "Unknown employer"
    role = _text(evidence.get("role")) or "Unknown role"
    job_id = _evidence_stub_id(evidence)
    try:
        tracker.get_job(job_id)
        return job_id
    except KeyError:
        pass
    now = tracker.__class__.__module__ and __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
    source_url = next((str(url) for url in evidence.get("urls", []) if url), "")
    application_status = "sent" if evidence.get("route") == "email" else "submitted"
    job = {
        "job_id": job_id,
        "source": "gmail_submission_evidence",
        "external_job_id": _text(evidence.get("external_job_id")),
        "source_url": source_url,
        "company": company,
        "role": role,
        "location": "",
        "posting_date": "",
        "closing_date": "",
        "jd_hash": "",
        "full_jd_path": f"projects/job-automation/data/jobs/{job_id}.json",
        "first_seen": now,
        "last_seen": now,
        "ingested_by": "system",
        "fit_score": "",
        "priority": "unrated",
        "owner": "system",
        "processing_status": "applied",
        "resume_status": "unknown",
        "cover_letter_status": "unknown",
        "pdf_status": "unknown",
        "gmail_draft_status": "not_applicable",
        "application_status": application_status,
        "outcome": "",
        "last_updated": now,
        "next_action": "Track employer response and enrich the vacancy if needed",
        "notes": "Created from verified Gmail submission evidence because no canonical tracked vacancy matched. Full JD was not invented.",
    }
    record = {
        "job": job,
        "full_job_description": "",
        "normalized_requirements": [],
        "provenance": {
            "source": "gmail_submission_evidence",
            "source_url": source_url,
            "gmail_message_id": evidence.get("message_id", ""),
            "evidence_only_stub": True,
        },
        "scoring": {"total": None, "raw_total": None, "recommendation": "unrated", "rationale": [], "gaps": []},
        "evidence_matches": [],
        "processing_state": {
            "owner": "system",
            "status": "applied",
            "route": {"route": evidence.get("route", "portal"), "application_url": source_url},
            "external_action_allowed": False,
            "send_or_submit": False,
        },
        "generated_artifacts": [],
        "gmail_draft_reference": None,
        "submission_package": {},
        "history": [],
    }
    tracker._save_job_and_row(record)
    tracker.record_event(
        actor="system",
        entity_type="job",
        entity_id=job_id,
        action="created",
        before={},
        after={"job": job},
        comment="Created evidence-only applied record from verified Gmail submission confirmation; no missing JD details were invented.",
        source_refs=[source_url] if source_url else [],
        confidence="high",
        requires_owner_review=False,
    )
    return job_id


def _append_submission_evidence(tracker: Any, job_id: str, evidence: dict[str, Any], *, match_reason: str) -> bool:
    record = tracker.get_job(job_id)
    job = record.get("job") or {}
    package = dict(record.get("submission_package") or {})
    gmail_evidence = list(package.get("gmail_evidence") or [])
    if any(item.get("message_id") == evidence.get("message_id") for item in gmail_evidence if isinstance(item, dict)):
        return False
    gmail_evidence.append({
        "message_id": evidence.get("message_id", ""),
        "thread_id": evidence.get("thread_id", ""),
        "subject": evidence.get("subject", ""),
        "sender": evidence.get("sender", ""),
        "date": evidence.get("date", ""),
        "signal": evidence.get("signal", ""),
        "match_reason": match_reason,
        "external_job_id": evidence.get("external_job_id", ""),
        "urls": list(evidence.get("urls") or []),
    })
    package["gmail_evidence"] = gmail_evidence
    package["status_source"] = "gmail_submission_reconciliation"
    package["last_gmail_confirmation_at"] = evidence.get("date", "")
    target_application = "sent" if evidence.get("route") == "email" else "submitted"
    changes: dict[str, Any] = {"submission_package": package}
    already_applied = _key(job.get("processing_status")) == "applied" and _key(job.get("application_status")) in {"applied", "submitted", "sent"}
    if not already_applied:
        changes.update({
            "processing_status": "applied",
            "application_status": target_application,
            "next_action": "Track employer response and follow up when appropriate",
        })
    tracker.update_job(
        job_id,
        changes,
        comment=f"Verified Gmail submission evidence reconciled ({evidence.get('signal')}); matched by {match_reason} and promoted to applied when needed.",
        actor="system",
        action="reviewed",
        source_refs=list(evidence.get("urls") or []),
        confidence="high",
        requires_owner_review=False,
    )
    return True


def _state_path(root: Path) -> Path:
    _, paths = load_config(root)
    return paths.tracker_base / "runtime" / "gmail-submission-reconciliation-state.json"


def _load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return {
        "last_sync": payload.get("last_sync"),
        "processed_message_ids": list(payload.get("processed_message_ids") or []),
    }


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def reconcile_submission_mail(
    root: Path,
    *,
    start: date | None = None,
    end_inclusive: date | None = None,
    max_results: int = 300,
) -> dict[str, Any]:
    today = date.today()
    end_inclusive = end_inclusive or today
    state = _load_state(root)
    if start is None:
        try:
            start = date.fromisoformat(str(state.get("last_sync") or "")) - timedelta(days=1)
        except ValueError:
            start = end_inclusive - timedelta(days=3)
    after = (start - timedelta(days=1)).strftime("%Y/%m/%d")
    before = (end_inclusive + timedelta(days=1)).strftime("%Y/%m/%d")
    query = (
        f"after:{after} before:{before} -in:spam -in:trash "
        "{application applying \"submitted successfully\" \"thank you for applying\" "
        "\"application confirmation\" from:hameedfarah@gmail.com}"
    )
    messages = search_messages(query, max_results=max_results)
    _, paths = load_config(root)
    tracker = _load_tracker(paths)
    processed = set(str(value) for value in state.get("processed_message_ids", []))
    reconciled: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    classified = 0

    for message in messages:
        message_id = _text(message.get("id"))
        if not message_id or message_id in processed:
            continue
        evidence = classify_submission_message(message)
        if not evidence:
            continue
        classified += 1
        job_id, reason = match_submission_to_tracker(tracker, evidence)
        if not job_id and reason == "unmatched" and evidence.get("company") and evidence.get("role"):
            job_id = _create_evidence_stub(tracker, evidence)
            reason = "evidence_only_stub"
        if not job_id:
            unmatched.append({
                "message_id": message_id,
                "subject": evidence.get("subject", ""),
                "company": evidence.get("company", ""),
                "role": evidence.get("role", ""),
                "reason": reason,
            })
            continue
        changed = _append_submission_evidence(tracker, job_id, evidence, match_reason=reason)
        processed.add(message_id)
        reconciled.append({
            "message_id": message_id,
            "job_id": job_id,
            "company": evidence.get("company", ""),
            "role": evidence.get("role", ""),
            "signal": evidence.get("signal", ""),
            "match_reason": reason,
            "changed": changed,
        })

    state = {
        "last_sync": end_inclusive.isoformat(),
        "processed_message_ids": sorted(processed)[-5000:],
    }
    _save_state(root, state)
    report = {
        "schema_version": 1,
        "start_date": start.isoformat(),
        "end_date": end_inclusive.isoformat(),
        "query": query,
        "messages_scanned": len(messages),
        "submission_messages_classified": classified,
        "reconciled": reconciled,
        "unmatched": unmatched,
        "gmail_mutations": 0,
        "send_or_submit": False,
    }
    report_path = paths.tracker_base / "runtime" / "gmail-submission-reconciliation.latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report
