#!/usr/bin/env python3
"""Restart-safe Gmail draft materializer and sender for the prepared outreach queue.

The controller always completes and verifies the full draft set before sending.
Every draft must preserve the approved sender alias, recipient, subject/body, and
both attachment hashes. Sending requires an explicit queue-hash confirmation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from career_engine.gmail import (
    CAREER_OUTWARD_EMAIL,
    _b64url_decode,
    _b64url_encode,
    _gmail_access_token,
    _gmail_api_json,
    _save_draft_payload,
    run_gws,
    verify_authenticated_mailbox,
)
from runtime.outscraper_sheet_runner import SPREADSHEET_ID, rclone_access_token, write_campaign_updates
from runtime.prepare_outscraper_queue import gmail_dedupe, gmail_list_all

RIYADH = ZoneInfo("Asia/Riyadh")
DEFAULT_QUEUE = Path("runtime/acceptance/outscraper-monitor-20260901/email-preparation-queue.json")
DEFAULT_ROOT = Path("runtime/acceptance/outscraper-monitor-20260901/campaign")
DEFAULT_START_HOUR = 11
DEFAULT_END_HOUR = 20
DEFAULT_MAX_PER_HOUR = 20
DEFAULT_MAX_DAY = 180


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def confirmation_token(queue_path: Path) -> str:
    return "CAMPAIGN-" + hashlib.sha256(queue_path.read_bytes()).hexdigest()[:16].upper()


def load_queue(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("queue") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("prepared outreach queue is missing or malformed")
    ids: set[str] = set()
    emails: set[str] = set()
    for item in rows:
        queue_id = str(item.get("queue_id") or "").strip()
        email = str(item.get("email") or "").strip().lower()
        if not queue_id or not email or queue_id in ids or email in emails:
            raise RuntimeError("prepared outreach queue has duplicate/missing identity")
        ids.add(queue_id)
        emails.add(email)
        if str(item.get("sender") or "").strip().lower() != CAREER_OUTWARD_EMAIL:
            raise RuntimeError("prepared queue sender alias mismatch")
        attachments = item.get("attachments")
        if not isinstance(attachments, list) or len(attachments) != 2:
            raise RuntimeError("prepared queue must contain exactly two attachments")
        for attachment in attachments:
            file_path = Path(str(attachment.get("path") or ""))
            if not file_path.is_file() or not file_path.read_bytes().startswith(b"%PDF"):
                raise RuntimeError(f"missing/invalid campaign PDF: {file_path}")
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if digest != str(attachment.get("sha256") or "").strip().lower():
                raise RuntimeError("campaign attachment hash mismatch")
    return rows


def build_raw(item: dict) -> bytes:
    message = EmailMessage()
    message["To"] = str(item["email"]).strip().lower()
    message["From"] = CAREER_OUTWARD_EMAIL
    message["Subject"] = str(item["subject"])
    message.set_content(str(item["body"]))
    for attachment in item["attachments"]:
        path = Path(str(attachment["path"]))
        message.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=str(attachment["filename"]))
    boundary_seed = (
        str(item["queue_id"]) + str(item["email"]) + str(item["subject"]) +
        "".join(str(a["sha256"]) for a in item["attachments"])
    ).encode("utf-8")
    message.set_boundary("=_career_outreach_" + hashlib.sha256(boundary_seed).hexdigest()[:24])
    return message.as_bytes(policy=policy.SMTP)


def _plain_body(parsed) -> str:
    for part in parsed.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/plain":
            content = part.get_content()
            return (content if isinstance(content, str) else str(content)).replace("\r\n", "\n").strip()
    return ""


def _verify_message_payload(payload: dict, item: dict, *, require_sent: bool) -> dict:
    raw = str(payload.get("raw") or "")
    if not raw:
        raise RuntimeError("Gmail returned no raw MIME")
    parsed = BytesParser(policy=policy.default).parsebytes(_b64url_decode(raw))
    sender = parseaddr(str(parsed.get("From", "")))[1].lower()
    recipient = parseaddr(str(parsed.get("To", "")))[1].lower()
    if sender != CAREER_OUTWARD_EMAIL or recipient != str(item["email"]).strip().lower():
        raise RuntimeError("Gmail message sender/recipient verification failed")
    if str(parsed.get("Subject", "")).strip() != str(item["subject"]).strip():
        raise RuntimeError("Gmail message subject verification failed")
    if _plain_body(parsed) != str(item["body"]).replace("\r\n", "\n").strip():
        raise RuntimeError("Gmail message body verification failed")
    attachments = []
    for part in parsed.walk():
        filename = part.get_filename()
        if filename:
            data = part.get_payload(decode=True) or b""
            attachments.append((filename, hashlib.sha256(data).hexdigest()))
    expected = sorted((str(a["filename"]), str(a["sha256"]).lower()) for a in item["attachments"])
    if sorted(attachments) != expected:
        raise RuntimeError("Gmail message attachment verification failed")
    labels = set(payload.get("labelIds") or [])
    if require_sent and "SENT" not in labels:
        raise RuntimeError("Gmail sent-message verification failed")
    if not require_sent and "SENT" in labels:
        raise RuntimeError("draft unexpectedly has SENT label")
    return {
        "message_id": str(payload.get("id") or ""),
        "thread_id": str(payload.get("threadId") or ""),
        "sender": sender,
        "recipient": recipient,
        "attachment_count": len(attachments),
    }


def verify_draft(draft_id: str, item: dict) -> dict:
    params = json.dumps({"userId": "me", "id": draft_id, "format": "raw"}, separators=(",", ":"))
    saved = run_gws(["gmail", "users", "drafts", "get", "--params", params])
    message = dict(saved.get("message") or {})
    verified = _verify_message_payload(message, item, require_sent=False)
    verified["draft_id"] = str(saved.get("id") or draft_id)
    return verified


def verify_sent(message_id: str, item: dict) -> dict:
    params = json.dumps({"userId": "me", "id": message_id, "format": "raw"}, separators=(",", ":"))
    message = run_gws(["gmail", "users", "messages", "get", "--params", params])
    return _verify_message_payload(message, item, require_sent=True)


def progress(root: Path, phase: str, **extra) -> None:
    event = {"at": utc_now(), "phase": phase, **extra}
    root.mkdir(parents=True, exist_ok=True)
    with (root / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    atomic_json(root / "status.json", event)
    print(json.dumps(event, sort_keys=True), flush=True)


def load_ledger(path: Path) -> dict:
    if not path.is_file():
        return {"entries": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
        raise RuntimeError("campaign ledger is malformed")
    return payload


def _sheet_flush(sheet_token: str, updates: list[tuple[str, str, dict]]) -> None:
    for offset in range(0, len(updates), 25):
        write_campaign_updates(sheet_token, updates[offset:offset + 25], SPREADSHEET_ID)


def materialize_all(queue: list[dict], root: Path, sheet_token: str) -> dict:
    ledger_path = root / "ledger.json"
    ledger = load_ledger(ledger_path)
    entries = ledger["entries"]
    sent_by_email, draft_by_email = gmail_dedupe()
    sheet_updates: list[tuple[str, str, dict]] = []
    verified_count = 0
    already_sent = 0
    created = 0
    repaired = 0
    reused = 0
    for index, item in enumerate(queue, start=1):
        queue_id = str(item["queue_id"])
        email = str(item["email"]).lower()
        entry = dict(entries.get(queue_id) or {})
        if email in sent_by_email:
            entry.update(status="already_sent", sent_message_id=sent_by_email[email], updated_at=utc_now())
            entries[queue_id] = entry
            already_sent += 1
            sheet_updates.append((queue_id, email, {"Send_State": "ALREADY_SENT_DEDUPED", "Sent_Message_ID": sent_by_email[email], "Terminal_Outcome": "SENT"}))
            atomic_json(ledger_path, ledger)
            continue

        candidate = str(entry.get("draft_id") or draft_by_email.get(email) or "")
        verified = None
        if candidate:
            try:
                verified = verify_draft(candidate, item)
                reused += 1
            except Exception:
                verified = None
        if verified is None:
            raw = _b64url_encode(build_raw(item))
            saved, action = _save_draft_payload(raw, existing_draft_id=candidate)
            draft_id = str(saved.get("id") or candidate)
            if not draft_id:
                raise RuntimeError("Gmail draft save returned no draft ID")
            verified = verify_draft(draft_id, item)
            if action == "created":
                created += 1
            else:
                repaired += 1
        entry.update(
            status="draft_verified",
            draft_id=verified["draft_id"],
            gmail_message_id=verified["message_id"],
            sender=CAREER_OUTWARD_EMAIL,
            verified_at=utc_now(),
            attachment_hashes=[str(a["sha256"]) for a in item["attachments"]],
        )
        entries[queue_id] = entry
        verified_count += 1
        atomic_json(ledger_path, ledger)
        sheet_updates.append((queue_id, email, {
            "Gmail_Draft_ID": verified["draft_id"],
            "Gmail_Message_ID": verified["message_id"],
            "Send_State": "DRAFT_VERIFIED_READY_TO_SEND",
            "Terminal_Outcome": "",
        }))
        if len(sheet_updates) >= 25:
            _sheet_flush(sheet_token, sheet_updates)
            sheet_updates.clear()
        if index % 10 == 0:
            progress(root, "drafts-materializing", completed=index, total=len(queue), verified=verified_count, already_sent=already_sent, created=created, repaired=repaired, reused=reused)
    if sheet_updates:
        _sheet_flush(sheet_token, sheet_updates)
    summary = {"total": len(queue), "verified": verified_count, "already_sent": already_sent, "created": created, "repaired": repaired, "reused": reused}
    atomic_json(root / "draft-summary.json", summary)
    progress(root, "drafts-complete", **summary)
    return summary


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _sent_times(ledger: dict) -> list[datetime]:
    out = []
    for entry in ledger.get("entries", {}).values():
        parsed = _parse_time(str(entry.get("sent_at") or ""))
        if parsed:
            out.append(parsed.astimezone(timezone.utc))
    return out


def _seconds_until_window(now: datetime, start_hour: int, end_hour: int) -> float:
    local = now.astimezone(RIYADH)
    if start_hour <= local.hour < end_hour:
        return 0.0
    if local.hour < start_hour:
        target = local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    else:
        target = (local + timedelta(days=1)).replace(hour=start_hour, minute=0, second=0, microsecond=0)
    return max(0.0, (target - local).total_seconds())


def _recipient_sent_id(email: str) -> str:
    token = _gmail_access_token()
    matches = gmail_list_all(token, "messages", f"in:sent to:{email} after:2026/08/01")
    return str(matches[0].get("id") or "") if matches else ""


def send_all(queue: list[dict], root: Path, sheet_token: str, *, start_hour: int, end_hour: int, max_per_hour: int, max_day: int) -> None:
    ledger_path = root / "ledger.json"
    ledger = load_ledger(ledger_path)
    entries = ledger["entries"]
    remaining = [item for item in queue if str(entries.get(str(item["queue_id"]), {}).get("status") or "") not in {"sent", "already_sent"}]
    for item in remaining:
        entry = entries.get(str(item["queue_id"]), {})
        if entry.get("status") != "draft_verified" or not entry.get("draft_id"):
            raise RuntimeError("refusing send phase before all remaining drafts are verified")
    progress(root, "send-phase-start", remaining=len(remaining), max_per_hour=max_per_hour, max_day=max_day, start_hour=start_hour, end_hour=end_hour)

    last_send_monotonic: float | None = None
    while True:
        pending = [item for item in queue if str(entries.get(str(item["queue_id"]), {}).get("status") or "") not in {"sent", "already_sent"}]
        if not pending:
            sent = sum(str(entry.get("status") or "") == "sent" for entry in entries.values())
            deduped = sum(str(entry.get("status") or "") == "already_sent" for entry in entries.values())
            progress(root, "complete", sent=sent, already_sent=deduped, remaining=0)
            return

        now = datetime.now(timezone.utc)
        window_wait = _seconds_until_window(now, start_hour, end_hour)
        if window_wait > 0:
            progress(root, "outside-send-window", remaining=len(pending), resume_at_hour=start_hour)
            time.sleep(window_wait)
            continue

        sent_times = _sent_times(ledger)
        local_date = now.astimezone(RIYADH).date()
        today = [stamp for stamp in sent_times if stamp.astimezone(RIYADH).date() == local_date]
        if len(today) >= max_day:
            local = now.astimezone(RIYADH)
            target = (local + timedelta(days=1)).replace(hour=start_hour, minute=0, second=0, microsecond=0)
            progress(root, "daily-cap", sent_today=len(today), remaining=len(pending))
            time.sleep(max(1.0, (target - local).total_seconds()))
            continue
        recent = sorted(stamp for stamp in sent_times if 0 <= (now - stamp).total_seconds() < 3600)
        if len(recent) >= max_per_hour:
            wait = 3600 - (now - recent[0]).total_seconds() + 1
            progress(root, "hourly-cap", sent_last_hour=len(recent), remaining=len(pending))
            time.sleep(max(1.0, wait))
            continue
        if last_send_monotonic is not None:
            interval = 3600.0 / max_per_hour
            wait = interval - (time.monotonic() - last_send_monotonic)
            if wait > 0:
                time.sleep(wait)

        item = pending[0]
        queue_id = str(item["queue_id"])
        email = str(item["email"]).lower()
        entry = entries[queue_id]
        prior_sent_id = _recipient_sent_id(email)
        if prior_sent_id:
            entry.update(status="already_sent", sent_message_id=prior_sent_id, updated_at=utc_now())
            atomic_json(ledger_path, ledger)
            write_campaign_updates(sheet_token, [(queue_id, email, {"Send_State": "ALREADY_SENT_DEDUPED", "Sent_Message_ID": prior_sent_id, "Terminal_Outcome": "SENT"})])
            continue

        verify_draft(str(entry["draft_id"]), item)
        response = _gmail_api_json(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send",
            {"id": str(entry["draft_id"])},
        )
        sent_message_id = str(response.get("id") or "")
        if not sent_message_id:
            raise RuntimeError("Gmail draft send returned no message ID")
        verify_sent(sent_message_id, item)
        sent_at = utc_now()
        entry.update(status="sent", sent_message_id=sent_message_id, sent_at=sent_at, updated_at=sent_at)
        atomic_json(ledger_path, ledger)
        write_campaign_updates(sheet_token, [(queue_id, email, {"Send_State": "SENT_OUTREACH", "Sent_Message_ID": sent_message_id, "Terminal_Outcome": "SENT"})])
        last_send_monotonic = time.monotonic()
        sent_total = sum(str(value.get("status") or "") == "sent" for value in entries.values())
        progress(root, "sent", sent=sent_total, remaining=len(pending) - 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default=str(DEFAULT_QUEUE))
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--print-token", action="store_true")
    parser.add_argument("--materialize-limit", type=int, default=0, help="Draft-only canary limit; 0 means full queue")
    parser.add_argument("--start-hour", type=int, default=DEFAULT_START_HOUR)
    parser.add_argument("--end-hour", type=int, default=DEFAULT_END_HOUR)
    parser.add_argument("--max-per-hour", type=int, default=DEFAULT_MAX_PER_HOUR)
    parser.add_argument("--max-day", type=int, default=DEFAULT_MAX_DAY)
    args = parser.parse_args()
    queue_path = Path(args.queue)
    if args.print_token:
        print(confirmation_token(queue_path))
        return 0
    if not verify_authenticated_mailbox():
        raise SystemExit("authenticated Gmail mailbox is not the approved career mailbox")
    if not (0 <= args.start_hour < args.end_hour <= 24):
        raise SystemExit("invalid send window")
    if args.max_per_hour <= 0 or args.max_day <= 0 or args.materialize_limit < 0:
        raise SystemExit("invalid campaign limits")
    if args.send and args.materialize_limit:
        raise SystemExit("partial draft canary cannot be combined with --send")
    queue = load_queue(queue_path)
    materialize_queue = queue[:args.materialize_limit] if args.materialize_limit else queue
    root = Path(args.root)
    sheet_token = rclone_access_token()
    progress(root, "start", queue=len(queue), materialize_target=len(materialize_queue), sender=CAREER_OUTWARD_EMAIL, send_requested=bool(args.send))
    materialize_all(materialize_queue, root, sheet_token)
    if not args.send:
        progress(root, "draft-only-complete", queue=len(queue), materialized=len(materialize_queue))
        return 0
    token = confirmation_token(queue_path)
    if args.confirmation != token:
        raise SystemExit(f"send confirmation mismatch; expected {token}")
    send_all(
        queue,
        root,
        sheet_token,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        max_per_hour=args.max_per_hour,
        max_day=args.max_day,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
