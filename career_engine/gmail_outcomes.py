from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import load_config
from .gmail import search_messages
from .gmail_reconcile import match_submission_to_tracker
from .pipeline import _load_tracker

_ACTIVE_APPLICATION_STATES = {"applied", "submitted", "sent"}
_REJECTION_TERMS = (
    "not selected",
    "not been selected",
    "unable to proceed",
    "unable to take your application further",
    "unable to offer you a position",
    "decided not to continue",
    "decided to proceed with other applicants",
    "decided to proceed with other candidates",
    "moving forward with other candidates",
    "moved to the next step in their hiring process, and your application was not",
    "role has now been filled",
    "position has now been filled",
    "regret to inform you",
)
_OFFER_TERMS = (
    "pleased to offer you",
    "pleased to offer",
    "extend an offer",
    "offer letter",
    "employment offer",
)
_INTERVIEW_TERMS = (
    "interview invitation",
    "invite you to an interview",
    "shortlisted for an interview",
    "schedule an interview",
    "scheduled interview",
    "technical interview",
    "phone interview",
    "final interview",
    "first interview",
)
_ASSESSMENT_TERMS = (
    "online assessment",
    "complete your assessment",
    "assessment invitation",
    "technical test",
    "coding challenge",
    "hirevue",
)
_PENDING_TERMS = (
    "queued for review",
    "currently reviewing",
    "working carefully to assess",
    "application has been received",
    "application was received",
    "we have received your application",
    "thank you for submitting your application",
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value: Any) -> str:
    return _text(value).lower()


def _strip_open_suffix(role: str) -> str:
    return re.sub(r"\s*\((?:open|closed|evergreen)\)\s*$", "", _text(role), flags=re.I)


def _sender_display(sender: str) -> str:
    value = _text(sender)
    quoted = re.match(r'^\s*"([^"]+)"', value)
    display = quoted.group(1) if quoted else re.sub(r"\s*<?[\w.+-]+@[\w.-]+>?\s*$", "", value)
    display = display.strip().strip('"')
    display = re.sub(r"^Workday\.Admin\s+", "", display, flags=re.I)
    return _text(display)


def _classify_signal(subject: str, body: str) -> str:
    combined = f"{subject}\n{body}".lower()
    if any(term in combined for term in _REJECTION_TERMS):
        return "rejected"
    if any(term in combined for term in _OFFER_TERMS):
        return "offer"
    if any(term in combined for term in _INTERVIEW_TERMS):
        return "interview"
    if any(term in combined for term in _ASSESSMENT_TERMS):
        return "assessment"
    if any(term in combined for term in _PENDING_TERMS):
        return "pending"
    return ""


def _extract_identity(subject: str, body: str, sender: str) -> dict[str, str]:
    company = ""
    role = ""
    external = ""

    ref_match = re.search(r"\bREF:\s*(.+?)\s+-\s+([A-Za-z0-9_-]{4,})\b", body, re.I)
    if ref_match:
        role = _text(ref_match.group(1))
        external = _text(ref_match.group(2))
        sf_company = re.match(r"^(.+?)\s+Careers\s*[–—-]\s*Application Status", subject, re.I)
        if sf_company:
            company = _text(sf_company.group(1))

    if not external or not role:
        workday = re.match(
            r"^Application for the position of\s+([A-Za-z]+-\d+)\s+(.+)$",
            subject,
            re.I,
        )
        if workday:
            external = external or _text(workday.group(1))
            role = role or _strip_open_suffix(workday.group(2))
            display = _sender_display(sender)
            if display and _key(display) not in {"workday admin", "workday.admin"}:
                company = company or display

    interest_with = re.search(
        r"thank you for your interest in the\s+([^\n.]+?)\s+with\s+([^\n.]+?)(?:\.|,|$)",
        body,
        re.I,
    )
    if interest_with:
        role = role or _text(interest_with.group(1))
        company = company or _text(interest_with.group(2))

    interest_our = re.search(
        r"thank you for your interest in\s+(.+?)\s+and our\s+(.+?)\s+position\b",
        body,
        re.I | re.S,
    )
    if interest_our:
        company = company or _text(interest_our.group(1))
        role = role or _text(interest_our.group(2))

    position_at = re.search(
        r"(?:interest in|application for)\s+the\s+(.+?)\s+position at\s+(.+?)(?:\.|,|\n)",
        body,
        re.I | re.S,
    )
    if position_at:
        role = role or _text(position_at.group(1))
        company = company or _text(position_at.group(2))

    offer_at = re.search(
        r"(?:pleased to offer you|extend an offer)(?:\s+for)?\s+the\s+(?:position|role)\s+of\s+(.+?)\s+(?:at|with)\s+(.+?)(?:\.|,|\n)",
        body,
        re.I | re.S,
    )
    if offer_at:
        role = role or _text(offer_at.group(1))
        company = company or _text(offer_at.group(2))

    position_of = re.search(r"application for the position of\s+(.+?)(?:\.|\n)", body, re.I | re.S)
    if position_of:
        role = role or _text(position_of.group(1))
    signature = re.search(
        r"(?:Best regards|Kind regards|Regards)[,:\s]+(.+?)\s+(?:Hiring Team|Talent Acquisition|Recruitment Team)\b",
        body,
        re.I | re.S,
    )
    if signature:
        company = company or _text(signature.group(1))

    if not company and role:
        suffix = re.match(rf"^{re.escape(role)}\s+-\s+(.+)$", subject, re.I)
        if suffix:
            company = _text(suffix.group(1))

    return {"company": company, "role": role, "external_job_id": external}


def classify_outcome_message(message: dict[str, Any]) -> dict[str, Any] | None:
    labels = {str(value).upper() for value in message.get("label_ids", [])}
    if labels & {"SENT", "DRAFT"}:
        return None
    subject = _text(message.get("subject"))
    body = _text(message.get("body"))
    sender = _text(message.get("from"))
    signal = _classify_signal(subject, body)
    if not signal:
        return None
    identity = _extract_identity(subject, body, sender)
    return {
        **identity,
        "signal": signal,
        "message_id": _text(message.get("id")),
        "thread_id": _text(message.get("thread_id")),
        "subject": subject,
        "sender": sender,
        "date": _text(message.get("date")),
        "urls": [str(value) for value in message.get("urls", []) if value],
    }


def _record_is_applied(record: dict[str, Any]) -> bool:
    job = record.get("job") or {}
    return (
        _key(job.get("processing_status")) == "applied"
        or _key(job.get("application_status")) in _ACTIVE_APPLICATION_STATES
    )


def match_outcome_to_tracker(tracker: Any, evidence: dict[str, Any]) -> tuple[str, str]:
    job_id, reason = match_submission_to_tracker(tracker, evidence)
    if not job_id:
        return "", reason
    record = tracker.get_job(job_id)
    if reason.startswith("company_role") and not _record_is_applied(record):
        return "", "matched_non_applied_record"
    return job_id, reason


def _append_outcome_evidence(tracker: Any, job_id: str, evidence: dict[str, Any], *, match_reason: str) -> bool:
    record = tracker.get_job(job_id)
    package = dict(record.get("submission_package") or {})
    items = list(package.get("gmail_outcome_evidence") or [])
    if any(isinstance(item, dict) and item.get("message_id") == evidence.get("message_id") for item in items):
        return False
    items.append({
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
    package["gmail_outcome_evidence"] = items
    package["last_gmail_outcome_at"] = evidence.get("date", "")
    package["outcome_status_source"] = "gmail_outcome_reconciliation"

    signal = _key(evidence.get("signal"))
    changes: dict[str, Any] = {"submission_package": package}
    processing_state = dict(record.get("processing_state") or {})
    requires_owner_review = False
    action = "reviewed"

    if signal == "rejected":
        changes.update({
            "processing_status": "rejected",
            "outcome": "rejected",
            "next_action": "Application closed after verified employer rejection",
        })
        blockers = list(processing_state.get("blockers") or [])
        if "employer_outcome:rejected" not in blockers:
            blockers.append("employer_outcome:rejected")
        processing_state.update({
            "status": "rejected",
            "outcome": "rejected",
            "external_action_allowed": False,
            "blockers": blockers,
        })
        changes["processing_state"] = processing_state
        action = "rejected"
    elif signal in {"interview", "assessment"}:
        changes.update({
            "outcome": "interview",
            "next_action": "Prepare for the verified employer interview or assessment",
        })
        processing_state["outcome"] = "interview"
        changes["processing_state"] = processing_state
    elif signal == "offer":
        changes.update({
            "outcome": "offer",
            "next_action": "Owner decision required on the verified employer offer",
        })
        processing_state["outcome"] = "offer"
        changes["processing_state"] = processing_state
        requires_owner_review = True
    elif signal == "pending":
        pass
    else:
        raise ValueError(f"Unsupported Gmail outcome signal: {signal}")

    tracker.update_job(
        job_id,
        changes,
        comment=(
            f"Verified Gmail application outcome evidence reconciled ({signal}); "
            f"matched by {match_reason}. Existing submission/application identity was preserved."
        ),
        actor="system",
        action=action,
        source_refs=list(evidence.get("urls") or []),
        confidence="high",
        requires_owner_review=requires_owner_review,
    )
    return True


def _state_path(root: Path) -> Path:
    _, paths = load_config(root)
    return paths.tracker_base / "runtime" / "gmail-outcome-reconciliation-state.json"


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


def reconcile_outcome_mail(
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
            start = end_inclusive - timedelta(days=7)
    after = (start - timedelta(days=1)).strftime("%Y/%m/%d")
    before = (end_inclusive + timedelta(days=1)).strftime("%Y/%m/%d")
    query = (
        f"after:{after} before:{before} -in:spam -in:trash -in:sent -in:drafts "
        "{\"not selected\" rejected \"role has now been filled\" \"regret to inform you\" "
        "interview assessment \"offer letter\" \"pleased to offer\" \"application status\" "
        "\"queued for review\" \"currently reviewing\"}"
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
        evidence = classify_outcome_message(message)
        if not evidence:
            continue
        classified += 1
        job_id, reason = match_outcome_to_tracker(tracker, evidence)
        if not job_id:
            unmatched.append({
                "message_id": message_id,
                "subject": evidence.get("subject", ""),
                "company": evidence.get("company", ""),
                "role": evidence.get("role", ""),
                "signal": evidence.get("signal", ""),
                "reason": reason,
            })
            processed.add(message_id)
            continue
        changed = _append_outcome_evidence(tracker, job_id, evidence, match_reason=reason)
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
        "outcome_messages_classified": classified,
        "reconciled": reconciled,
        "unmatched_manual_review": unmatched,
        "application_states_changed": sum(1 for item in reconciled if item.get("changed")),
        "gmail_mutations": 0,
        "send_or_submit": False,
    }
    report_path = paths.tracker_base / "runtime" / "gmail-outcome-reconciliation.latest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report["report_path"] = str(report_path)
    return report
