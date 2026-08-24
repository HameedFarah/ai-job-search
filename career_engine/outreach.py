"""Fail-closed, preflight-first bulk outreach sender."""
from __future__ import annotations

import hashlib, json, re, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Callable
from . import gmail

DEFAULT_MAX_RUN = 200
DEFAULT_MAX_PER_HOUR = 20
SENDABLE = {"queued", "preflighted"}
BLOCKED = {"invalid", "spamtrap", "abuse", "do_not_mail", "rejected"}
RIYADH = ZoneInfo("Asia/Riyadh")

def _sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def _now() -> str: return datetime.now(timezone.utc).isoformat(timespec="seconds")
def confirmation_token(queue_hash: str) -> str: return "OUTREACH-" + queue_hash[:12].upper()

def _load(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8")); rows = value.get("queue", value) if isinstance(value, dict) else value
    if not isinstance(rows, list): raise ValueError("Queue must be a JSON array or {queue: [...]}")
    return rows

def _content(row: dict[str, Any]) -> tuple[str, str]:
    template = row.get("template") or {}; company = str(row.get("company", ""))
    subject = str(row.get("subject") or template.get("subject", "")).replace("{company}", company).strip()
    body = str(row.get("body") or template.get("body", "")).replace("{company}", company).strip()
    if not subject or not body: raise ValueError("deterministic subject and body are required")
    return subject, body

def _validate(row: dict[str, Any], allow_catch_all: bool) -> tuple[str, str, str, Path, bytes]:
    for key in ("outreach_id", "company", "primary_email", "verification", "status", "priority_tier"):
        if not row.get(key): raise ValueError(f"missing field: {key}")
    recipient = str(row["primary_email"]).strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient): raise ValueError("invalid primary_email")
    verification = row["verification"] if isinstance(row["verification"], dict) else {}
    status = str(verification.get("status", "")).strip().lower().replace("-", "_")
    if status in BLOCKED or not status: raise ValueError(f"verification status not sendable: {status or 'missing'}")
    if status != "valid" and not (status == "catch_all" or status == "unknown"):
        raise ValueError(f"verification status not sendable: {status}")
    if status in {"catch_all", "unknown"} and not allow_catch_all: raise ValueError("catch-all/unknown requires explicit risk override")
    if not (verification.get("evidence") or verification.get("source")): raise ValueError("verification evidence is required")
    pdf = Path(str(row.get("cv_pdf_path", "")))
    if pdf.suffix.lower() != ".pdf" or not pdf.is_file() or pdf.stat().st_size == 0: raise ValueError("approved CV PDF is missing or invalid")
    data = pdf.read_bytes(); expected = str(row.get("cv_pdf_sha256", ""))
    if expected and expected != _sha(data): raise ValueError("CV PDF hash mismatch")
    subject, body = _content(row)
    return recipient, subject, body, pdf, data

def _checkpoint(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def process_queue(queue_path: Path, ledger_path: Path, *, apply=False, confirmation="", allow_catch_all=False,
                  max_run=DEFAULT_MAX_RUN, max_per_hour=DEFAULT_MAX_PER_HOUR, max_day=DEFAULT_MAX_RUN,
                  clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    rows = _load(queue_path); queue_hash = _sha(queue_path.read_bytes()); token = confirmation_token(queue_hash)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {"entries": {}, "sent": []}
    entries = ledger.setdefault("entries", {}); result = {"mode": "send" if apply else "preflight", "queue_sha256": queue_hash, "confirmation_token": token, "processed": 0, "sent": 0, "failed": 0, "skipped": 0}
    if max_run <= 0 or max_per_hour <= 0 or max_day <= 0:
        raise ValueError("max_run, max_per_hour, and max_day must be positive")
    if apply:
        if confirmation != token: raise ValueError(f"confirmation token mismatch; expected {token}")
        if not gmail.verify_authenticated_mailbox(): raise RuntimeError("authenticated Gmail mailbox is not hameedo@gmail.com")
    ids=set(); emails=set(); sent_run=0
    sent_day=sum(1 for x in entries.values() if x.get("status") == "sent" and _riyadh_date(x.get("sent_at")) == datetime.now(timezone.utc).astimezone(RIYADH).date().isoformat())
    last_send=None
    for row in rows:
        oid = str(row.get("outreach_id", "")); recipient_key = str(row.get("primary_email", "")).strip().lower()
        if not oid or oid in ids: raise ValueError("duplicate or missing outreach_id")
        ids.add(oid)
        if recipient_key in emails: raise ValueError("duplicate normalized email")
        emails.add(recipient_key); entry = entries.setdefault(oid, {})
        if entry.get("status") == "sent": result["skipped"] += 1; continue
        if str(row.get("status", "")).lower() not in SENDABLE: entry.update(status="failed", failure_reason="queue status is not eligible"); result["failed"] += 1; _checkpoint(ledger_path, ledger); continue
        try:
            recipient, subject, body, pdf, pdf_data = _validate(row, allow_catch_all)
            raw = gmail.build_application_message(recipient=recipient, subject=subject, body=body, pdf_path=pdf, sender=gmail.CAREER_OUTWARD_EMAIL)
            parsed = BytesParser(policy=policy.default).parsebytes(raw); attachments=[p for p in parsed.walk() if p.get_filename()]
            if len(attachments) != 1 or _sha(attachments[0].get_payload(decode=True) or b"") != _sha(pdf_data): raise ValueError("MIME attachment integrity check failed")
            record={"status":"preflighted","queue_sha256":queue_hash,"recipient":recipient,"subject_sha256":_sha(subject.encode()),"body_sha256":_sha(body.encode()),"attachment_sha256":_sha(pdf_data),"mime_sha256":_sha(raw),"updated_at":_now()}
            if not apply:
                entry.update(record); result["processed"] += 1; _checkpoint(ledger_path, ledger); continue
            prior = entries.get(oid, {})
            if prior.get("status") != "preflighted" or prior.get("queue_sha256") != queue_hash or any(prior.get(k) != record[k] for k in ("recipient", "subject_sha256", "body_sha256", "attachment_sha256", "mime_sha256")):
                raise RuntimeError("mandatory prior preflight missing, stale, or MIME/hash mismatch")
            result["processed"] += 1
            if sent_run >= max_run or sent_day >= max_day: result["capped"] = True; break
            if last_send is not None:
                interval=3600.0/max_per_hour; wait=max(0.0, interval-(clock()-last_send));
                if wait: sleep(wait)
            try: response = gmail.send_application_message(raw)
            except Exception as exc:
                entry.update(status="failed", failure_reason=str(exc), updated_at=_now()); _checkpoint(ledger_path, ledger)
                result["failed"] += 1
                if any(x in str(exc).lower() for x in ("429", "quota", "rate", "authorization", "unauthorized", "forbidden")): result["stopped_on_error"] = True; break
                continue
            entry.update(status="sent", gmail_message_id=response.get("id", ""), gmail_thread_id=response.get("threadId", ""), sent_at=_now()); sent_run += 1; sent_day += 1; last_send=clock(); result["sent"] += 1; _checkpoint(ledger_path, ledger)
        except Exception as exc:
            entry.update(status="failed", failure_reason=str(exc), updated_at=_now()); result["failed"] += 1; _checkpoint(ledger_path, ledger)
    return result

def _riyadh_date(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None: return ""
        return parsed.astimezone(RIYADH).date().isoformat()
    except (TypeError, ValueError):
        return ""
