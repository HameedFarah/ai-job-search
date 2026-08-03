from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from datetime import date, timedelta
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from html import unescape
from pathlib import Path
from typing import Any


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_gws(args: list[str], *, timeout: int = 120) -> dict[str, Any]:
    completed = subprocess.run(["gws", *args], capture_output=True, text=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"gws failed ({completed.returncode}): {completed.stderr.strip() or completed.stdout.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from gws: {completed.stdout[:500]}") from exc


def profile() -> dict[str, Any]:
    params = json.dumps({"userId": "me"}, separators=(",", ":"))
    result = run_gws(["gmail", "users", "getProfile", "--params", params])
    return {
        "email_address": result.get("emailAddress", ""),
        "messages_total": result.get("messagesTotal"),
        "threads_total": result.get("threadsTotal"),
        "history_id": result.get("historyId", ""),
        "authenticated": bool(result.get("emailAddress")),
    }


def _message_text(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        text = content if isinstance(content, str) else str(content)
        if content_type == "text/plain":
            plain.append(text)
        else:
            html.append(text)
    selected = "\n".join(plain).strip() or "\n".join(html)
    selected = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", selected)
    selected = re.sub(r"(?s)<[^>]+>", " ", selected)
    selected = unescape(selected)
    selected = re.sub(r"[\t\r ]+", " ", selected)
    selected = re.sub(r"\n\s*\n+", "\n", selected)
    return selected.strip()


def _urls(message: Message) -> list[str]:
    values: list[str] = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        values.extend(re.findall(r"https?://[^\s<>\"']+", str(content)))
    cleaned: list[str] = []
    for value in values:
        value = unescape(value).rstrip(".,);]")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def get_message(message_id: str) -> dict[str, Any]:
    params = json.dumps({"userId": "me", "id": message_id, "format": "raw"}, separators=(",", ":"))
    payload = run_gws(["gmail", "users", "messages", "get", "--params", params])
    raw = payload.get("raw", "")
    if not raw:
        raise RuntimeError(f"Message {message_id} has no raw MIME content")
    parsed = BytesParser(policy=policy.default).parsebytes(_b64url_decode(raw))
    return {
        "id": payload.get("id", message_id),
        "thread_id": payload.get("threadId", ""),
        "label_ids": payload.get("labelIds", []),
        "date": str(parsed.get("Date", "")),
        "from": str(parsed.get("From", "")),
        "to": str(parsed.get("To", "")),
        "subject": str(parsed.get("Subject", "")),
        "message_id_header": str(parsed.get("Message-ID", "")),
        "body": _message_text(parsed),
        "urls": _urls(parsed),
    }


def search_messages(query: str, *, max_results: int = 100) -> list[dict[str, Any]]:
    params = json.dumps({"userId": "me", "q": query, "maxResults": max_results}, separators=(",", ":"))
    result = run_gws(["gmail", "users", "messages", "list", "--params", params])
    messages = result.get("messages", []) or []
    return [get_message(item["id"]) for item in messages if item.get("id")]


JOB_TERMS = (
    "job", "jobs", "vacancy", "vacancies", "career", "careers", "hiring", "recruiter",
    "opportunity", "position", "role", "architect", "design manager", "design director",
    "technical director", "project director", "project manager", "programme manager",
    "program manager", "head of design", "head of architecture", "construction manager",
)
JOB_SENDERS = (
    "linkedin", "indeed", "glassdoor", "naukrigulf", "bayt", "gulftalent", "workable",
    "greenhouse", "lever", "smartrecruiters", "jobright", "careers", "recruit", "talent",
)


def _job_relevance(message: dict[str, Any]) -> int:
    subject = message.get("subject", "").lower()
    sender = message.get("from", "").lower()
    body = message.get("body", "").lower()
    score = 0
    score += sum(4 for term in JOB_SENDERS if term in sender)
    score += sum(3 for term in JOB_TERMS if term in subject)
    score += min(8, sum(1 for term in JOB_TERMS if term in body))
    if "unsubscribe" in body:
        score -= 1
    if any(term in subject for term in ("application received", "application update", "interview", "assessment")):
        score += 8
    return score


def scan_job_mail(start: date, end_inclusive: date, *, max_results: int = 300) -> dict[str, Any]:
    after = (start - timedelta(days=1)).strftime("%Y/%m/%d")
    before = (end_inclusive + timedelta(days=1)).strftime("%Y/%m/%d")
    query = f"after:{after} before:{before} -in:spam -in:trash"
    messages = search_messages(query, max_results=max_results)
    candidates: list[dict[str, Any]] = []
    for message in messages:
        relevance = _job_relevance(message)
        if relevance < 4:
            continue
        candidates.append({
            **message,
            "relevance": relevance,
            "body": message.get("body", "")[:30000],
            "urls": message.get("urls", [])[:100],
        })
    candidates.sort(key=lambda item: (item["relevance"], item.get("date", "")), reverse=True)
    return {
        "schema_version": 1,
        "query": query,
        "start_date": start.isoformat(),
        "end_date": end_inclusive.isoformat(),
        "messages_scanned": len(messages),
        "candidate_messages": len(candidates),
        "candidates": candidates,
    }


def _parse_raw_message(raw: str) -> tuple[Message, bytes]:
    decoded = _b64url_decode(raw)
    return BytesParser(policy=policy.default).parsebytes(decoded), decoded


def list_matching_drafts(query: str, *, max_results: int = 20) -> list[dict[str, Any]]:
    params = json.dumps({"userId": "me", "q": query, "maxResults": max_results}, separators=(",", ":"))
    result = run_gws(["gmail", "users", "drafts", "list", "--params", params])
    drafts: list[dict[str, Any]] = []
    for item in result.get("drafts", []) or []:
        draft_id = item.get("id")
        if not draft_id:
            continue
        get_params = json.dumps({"userId": "me", "id": draft_id, "format": "raw"}, separators=(",", ":"))
        saved = run_gws(["gmail", "users", "drafts", "get", "--params", get_params])
        raw = saved.get("message", {}).get("raw", "")
        if not raw:
            continue
        parsed, _ = _parse_raw_message(raw)
        drafts.append({
            "draft_id": saved.get("id", draft_id),
            "message_id": saved.get("message", {}).get("id", ""),
            "thread_id": saved.get("message", {}).get("threadId", ""),
            "to": str(parsed.get("To", "")),
            "subject": str(parsed.get("Subject", "")),
        })
    return drafts


def build_application_message(*, recipient: str, subject: str, body: str, pdf_path: Path, sender: str) -> bytes:
    if not recipient or "@" not in recipient:
        raise ValueError("A verified application recipient is required")
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise FileNotFoundError(pdf_path)
    message = EmailMessage()
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(pdf_path.read_bytes(), maintype="application", subtype="pdf", filename=pdf_path.name)
    return message.as_bytes(policy=policy.SMTP)


def save_application_draft(
    *, recipient: str, subject: str, body: str, pdf_path: Path,
    verified_recipient_source: str, sender: str, existing_draft_id: str = "",
) -> dict[str, Any]:
    if not verified_recipient_source:
        raise ValueError("Recipient verification source is required")
    raw_bytes = build_application_message(recipient=recipient, subject=subject, body=body, pdf_path=pdf_path, sender=sender)
    raw = _b64url_encode(raw_bytes)
    if not existing_draft_id:
        matching = list_matching_drafts(f'to:{recipient} subject:"{subject}"')
        if len(matching) > 1:
            raise RuntimeError(f"Multiple matching drafts found: {[item['draft_id'] for item in matching]}")
        if matching:
            existing_draft_id = matching[0]["draft_id"]
    payload = json.dumps({"message": {"raw": raw}}, separators=(",", ":"))
    if existing_draft_id:
        params = json.dumps({"userId": "me", "id": existing_draft_id}, separators=(",", ":"))
        saved = run_gws(["gmail", "users", "drafts", "update", "--params", params, "--json", payload])
        action = "updated"
    else:
        params = json.dumps({"userId": "me"}, separators=(",", ":"))
        saved = run_gws(["gmail", "users", "drafts", "create", "--params", params, "--json", payload])
        action = "created"
    draft_id = saved.get("id", existing_draft_id)
    if not draft_id:
        raise RuntimeError("Gmail did not return a draft ID")
    return verify_draft(
        draft_id,
        expected_recipient=recipient,
        expected_subject=subject,
        expected_body=body,
        expected_pdf=pdf_path,
        recipient_source=verified_recipient_source,
        action=action,
    )


def verify_draft(
    draft_id: str, *, expected_recipient: str, expected_subject: str, expected_body: str,
    expected_pdf: Path, recipient_source: str, action: str = "verified",
) -> dict[str, Any]:
    params = json.dumps({"userId": "me", "id": draft_id, "format": "raw"}, separators=(",", ":"))
    saved = run_gws(["gmail", "users", "drafts", "get", "--params", params])
    raw = saved.get("message", {}).get("raw", "")
    if not raw:
        raise RuntimeError("Saved draft did not return raw MIME content")
    parsed, _ = _parse_raw_message(raw)
    body_text = ""
    attachments: list[dict[str, Any]] = []
    for part in parsed.walk():
        filename = part.get_filename()
        if filename:
            data = part.get_payload(decode=True) or b""
            attachments.append({
                "filename": filename,
                "content_type": part.get_content_type(),
                "size_bytes": len(data),
                "sha256": _sha256(data),
            })
        elif part.get_content_type() == "text/plain" and not body_text:
            content = part.get_content()
            body_text = content if isinstance(content, str) else str(content)
    labels = saved.get("message", {}).get("labelIds", []) or []
    expected_bytes = expected_pdf.read_bytes()
    verified = (
        str(parsed.get("To", "")) == expected_recipient
        and str(parsed.get("Subject", "")) == expected_subject
        and body_text.replace("\r\n", "\n").strip() == expected_body.replace("\r\n", "\n").strip()
        and len(attachments) == 1
        and attachments[0]["filename"] == expected_pdf.name
        and attachments[0]["sha256"] == _sha256(expected_bytes)
        and "SENT" not in labels
    )
    return {
        "verified": verified,
        "action": action,
        "draft_id": saved.get("id", draft_id),
        "message_id": saved.get("message", {}).get("id", ""),
        "thread_id": saved.get("message", {}).get("threadId", ""),
        "to": str(parsed.get("To", "")),
        "recipient_source": recipient_source,
        "subject": str(parsed.get("Subject", "")),
        "body_matches": body_text.replace("\r\n", "\n").strip() == expected_body.replace("\r\n", "\n").strip(),
        "attachments": attachments,
        "label_ids": labels,
        "sent": "SENT" in labels,
        "source_pdf_sha256": _sha256(expected_bytes),
    }
