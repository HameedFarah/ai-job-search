#!/usr/bin/env python3
"""Continuous deterministic sender for the Career Engine Auto Send Queue.

This is the production bridge between the canonical Google Sheet queue and the
proven outreach MIME/readback verifier.  It deliberately keeps all recipient
selection in ``career_engine.outreach_reconciler`` and all message integrity
checks in ``runtime.outreach_campaign_controller``.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from career_engine.gmail import CAREER_OUTWARD_EMAIL, _b64url_encode
from career_engine.outreach_reconciler import (
    CADENCE_SECONDS,
    MAX_DAILY,
    RIYADH,
    SENDER_GWS_CONFIG_DIR,
    WINDOW_END_HOUR,
    WINDOW_START_HOUR,
    QueueReconciler,
    _company_key,
    _email_domain,
    _read_queue_sheet,
    gmail_access_token_for_context,
    normalise_row,
    persist_new_row_defaults,
    verify_both_accounts_available,
    write_queue_fields,
)
from runtime.outreach_campaign_controller import build_raw, _verify_message_payload
from runtime.outscraper_sheet_runner import (
    SPREADSHEET_ID,
    rclone_access_token,
    sheets_request,
    write_campaign_updates,
)

SUBJECT = "Abdelhamid Farah | Senior Design & Project Leadership"
BODY = """Dear Hiring Team,

I am reaching out to express interest in senior design, project delivery, or consultancy-management opportunities with your organization. Please find my CV and portfolio attached for your consideration.

I would welcome the opportunity to discuss where my background may be relevant to your current or upcoming requirements.

Kind regards,
Abdelhamid Farah
hameedfarah@gmail.com"""

CV_PATH = REPO_ROOT / "runtime/Abdelhamid_Farah_CV_Senior_Design_Project_Leadership.pdf"
CV_SHA = "e35be83899bb6b05904b5b34754d7b834a7839bc5e89d8d569fe17595c50e0d5"
PORTFOLIO_PATH = REPO_ROOT / "runtime/Abdelhamid Farah-Portfolio-2026.pdf"
PORTFOLIO_SHA = "64f2a3b7caa1a827f8d03bf10cfa098b3c78dab73c0aa783d84e1784a4a05075"

# These defaults must be independent of the caller's current working directory.
# A diagnostic/manual invocation from outside the repo must never create a
# competing ledger/status tree or fail package validation because of relative
# paths.
DEFAULT_LEDGER = REPO_ROOT / "runtime/acceptance/auto-send-queue/ledger.json"
DEFAULT_STATUS = REPO_ROOT / "runtime/acceptance/auto-send-queue/status.json"
DEFAULT_LOCK = REPO_ROOT / "runtime/acceptance/auto-send-queue/sender.lock"
POLL_SECONDS = 60
SUCCESS_CADENCE_SECONDS = 96
# Do not begin a fresh Gmail transaction at the edge of the hard 19:00 stop.
# This margin is deliberately larger than the normal API/readback latency and
# still preserves the owner-approved operating window.
MIN_SEND_START_BUFFER_SECONDS = 120


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(RIYADH)


def _window_open(now: datetime | None = None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(RIYADH)
    return WINDOW_START_HOUR <= local.hour < WINDOW_END_HOUR


def _seconds_until_window_close(now: datetime | None = None) -> float:
    local = (now or datetime.now(timezone.utc)).astimezone(RIYADH)
    if not _window_open(local):
        return 0.0
    close = local.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    return max(0.0, (close - local).total_seconds())


def _atomic_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _status(path: Path, phase: str, **extra: Any) -> None:
    payload = {"at": utc_now(), "phase": phase, **extra}
    _atomic_status(path, payload)
    print(json.dumps(payload, sort_keys=True), flush=True)


def _acquire_singleton_lock(path: Path):
    """Hold one process-wide sender lock for the lifetime of the invocation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _gmail_json(token: str, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return sheets_request(token, method, url, payload)


def _sender_profile(token: str) -> str:
    payload = _gmail_json(token, "GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile")
    return str(payload.get("emailAddress") or "").strip().lower()


def _sender_sent_today_count(token: str) -> int:
    """Conservative daily safeguard: count every sender SENT message since Riyadh midnight."""
    local = _local_now()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    query = f"after:{int(midnight.timestamp())}"
    total = 0
    page_token = ""
    while True:
        params: list[tuple[str, str | int]] = [
            ("q", query),
            ("maxResults", 500),
            ("labelIds", "SENT"),
        ]
        if page_token:
            params.append(("pageToken", page_token))
        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urlencode(params)
        payload = _gmail_json(token, "GET", url)
        total += len(payload.get("messages") or [])
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            return total


def _message_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue_id": row["queue_id"],
        "email": row["email"],
        "subject": SUBJECT,
        "body": BODY,
        "attachments": [
            {"path": str(CV_PATH), "filename": CV_PATH.name, "sha256": CV_SHA},
            {"path": str(PORTFOLIO_PATH), "filename": PORTFOLIO_PATH.name, "sha256": PORTFOLIO_SHA},
        ],
    }


def _verify_local_package(item: dict[str, Any]) -> bytes:
    for attachment in item["attachments"]:
        path = Path(str(attachment["path"]))
        if not path.is_file() or not path.read_bytes().startswith(b"%PDF"):
            raise RuntimeError(f"missing/invalid campaign PDF: {path}")
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != str(attachment["sha256"]).lower():
            raise RuntimeError(f"campaign attachment hash mismatch: {path.name}")
    return build_raw(item)


def _fetch_raw_sent(token: str, message_id: str) -> dict[str, Any]:
    return _gmail_json(
        token,
        "GET",
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?format=raw",
    )


def _send_raw(token: str, raw: bytes) -> dict[str, Any]:
    return _gmail_json(
        token,
        "POST",
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        {"raw": _b64url_encode(raw)},
    )


def _is_account_level_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in (
        "(403)", "(429)", "quota", "rate limit", "sending limit", "daily limit",
        "forbidden", "insufficientpermissions", "usagelimit",
    ))


def _is_permanent_recipient_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in (
        "invalid recipient", "invalid address", "recipient not found", "5.1.1", "no such user",
    ))


def _sent_today_from_ledger(reconciler: QueueReconciler) -> int:
    today = _local_now().date()
    count = 0
    for entry in reconciler.ledger.entries.values():
        if str(entry.get("status") or "").upper() != "SENT":
            continue
        try:
            stamp = datetime.fromisoformat(str(entry.get("sent_at") or ""))
        except ValueError:
            continue
        if stamp.tzinfo and stamp.astimezone(RIYADH).date() == today:
            count += 1
    return count


def _last_send_from_ledger(reconciler: QueueReconciler) -> datetime | None:
    stamps: list[datetime] = []
    for entry in reconciler.ledger.entries.values():
        if str(entry.get("status") or "").upper() != "SENT":
            continue
        try:
            stamp = datetime.fromisoformat(str(entry.get("sent_at") or ""))
        except ValueError:
            continue
        if stamp.tzinfo:
            stamps.append(stamp.astimezone(timezone.utc))
    return max(stamps) if stamps else None


def _persist_defaults(sheet_token: str, raw_rows: list[dict[str, str]]) -> None:
    for raw in raw_rows:
        normalised = normalise_row(raw)
        persist_new_row_defaults(sheet_token, raw, normalised)


def _mark_gmail_skips(sheet_token: str, reconciler: QueueReconciler, skips: list[dict[str, Any]]) -> None:
    """Persist deterministic outcomes for rows rejected during live reconciliation."""
    contacted_reasons = {
        "already_sent_gmail", "domain_sent_gmail", "company_sent_gmail",
        "known_contacted_company_alias",
    }
    hold_reasons = {
        "jordan_held", "company_excluded", "canonical_hard_block",
        "unresolved_company_identity",
    }
    for row in skips:
        reason = str(row.get("skip_reason") or "")
        email = str(row.get("email") or "").lower()
        # Restart recovery: a persisted SENDING row with an exact Gmail Sent hit
        # is the same transaction completing after a crash, not a generic skip.
        if str(row.get("status") or "").upper() == "SENDING" and reason == "already_sent_gmail":
            message_id = str(reconciler.sent_by_email.get(email) or "")
            if message_id:
                sent_at = utc_now()
                reconciler.ledger.mark_sent(str(row["queue_id"]), message_id, sent_at)
                reconciler.ledger.save()
                write_queue_fields(sheet_token, int(row["row_number"]), {
                    "Status": "SENT",
                    "Sent_At": sent_at,
                    "Gmail_Message_ID": message_id,
                    "Last_Error": "",
                })
                _update_master_after_send(reconciler, email, message_id)
                continue
        if reason in contacted_reasons:
            status = "SKIPPED_ALREADY_CONTACTED"
        elif reason == "permanent_bounce":
            status = "FAILED_PERMANENT"
        elif reason in hold_reasons:
            status = "HOLD"
        else:
            continue
        write_queue_fields(sheet_token, int(row["row_number"]), {
            "Status": status,
            "Last_Error": reason,
        })


def _update_master_after_send(reconciler: QueueReconciler, email: str, message_id: str) -> None:
    target_company = str(reconciler.master.get("email_to_company", {}).get(email) or "")
    updates: list[tuple[str, str, dict[str, str]]] = []
    for row in reconciler.master_rows:
        row_email = str(row.get("Email") or "").strip().lower()
        queue_id = str(row.get("Queue_ID") or "").strip()
        if not row_email or not queue_id:
            continue
        row_company = _company_key(str(row.get("Company_or_Office") or ""))
        if row_email == email:
            updates.append((queue_id, row_email, {
                "Send_State": "SENT",
                "Sent_Message_ID": message_id,
                "Terminal_Outcome": "sent_pending_dsn",
            }))
        elif target_company and row_company == target_company:
            state = str(row.get("Send_State") or "").upper()
            if state not in {"SENT", "ALREADY_SENT_DEDUPED", "SKIPPED_ALREADY_CONTACTED"}:
                updates.append((queue_id, row_email, {
                    "Send_State": "SKIPPED_ALREADY_CONTACTED",
                    "Terminal_Outcome": "SENT_COMPANY_DEDUPED",
                }))
    if updates:
        sheet_token = rclone_access_token()
        for offset in range(0, len(updates), 25):
            write_campaign_updates(sheet_token, updates[offset:offset + 25], SPREADSHEET_ID)


def _queue_has_work(raw_rows: list[dict[str, str]]) -> bool:
    for raw in raw_rows:
        row = normalise_row(raw)
        if row["status"] in {"PENDING", "SENDING", "FAILED_TEMPORARY"}:
            return True
    return False


def run(*, ledger_path: Path, status_path: Path, poll_seconds: int = POLL_SECONDS, once: bool = False) -> int:
    if SUCCESS_CADENCE_SECONDS < CADENCE_SECONDS:
        raise RuntimeError("successful send cadence is below canonical minimum")
    if not _window_open():
        _status(status_path, "outside-send-window", local=_local_now().isoformat())
        return 0

    ok, detail = verify_both_accounts_available()
    if not ok:
        raise RuntimeError(f"FAIL_CLOSED_BOTH_GMAIL_REQUIRED: {detail}")
    sender_token = gmail_access_token_for_context(SENDER_GWS_CONFIG_DIR)
    if _sender_profile(sender_token) != CAREER_OUTWARD_EMAIL:
        raise RuntimeError("sender OAuth context is not hameedfarah@gmail.com")

    failed_this_run: set[str] = set()
    while _window_open():
        sheet_token = rclone_access_token()
        raw_rows = _read_queue_sheet(sheet_token)
        _persist_defaults(sheet_token, raw_rows)

        if not _queue_has_work(raw_rows):
            _status(status_path, "idle", queue_rows=len(raw_rows), sender=CAREER_OUTWARD_EMAIL)
            if once:
                return 0
            time.sleep(max(1, poll_seconds))
            continue

        reconciler = QueueReconciler(sheet_token, ledger_path=ledger_path)
        reconciler.read_sheet()
        reconciler.normalise_all()
        eligible, skips = reconciler.reconcile()
        _mark_gmail_skips(sheet_token, reconciler, skips.get("excluded", []))
        eligible = [row for row in eligible if row["queue_id"] not in failed_this_run]

        sender_sent_today = _sender_sent_today_count(sender_token)
        if sender_sent_today >= MAX_DAILY:
            _status(status_path, "daily-cap", sender_sent_today=sender_sent_today, cap=MAX_DAILY)
            return 0

        sent_today = _sent_today_from_ledger(reconciler)
        last_send = _last_send_from_ledger(reconciler)
        selected = reconciler.select_next(
            eligible,
            now_utc=datetime.now(timezone.utc),
            last_send_utc=last_send,
            sent_today_count=max(sent_today, sender_sent_today),
        )
        if selected is None:
            _status(status_path, "no-eligible-selection", eligible=len(eligible))
            if once:
                return 0
            time.sleep(max(1, min(poll_seconds, SUCCESS_CADENCE_SECONDS)))
            continue

        seconds_left = _seconds_until_window_close()
        if seconds_left < MIN_SEND_START_BUFFER_SECONDS:
            _status(status_path, "window-closing", seconds_until_close=round(seconds_left, 3))
            return 0

        queue_id = str(selected["queue_id"])
        email = str(selected["email"]).lower()
        reconciler.ledger.mark_pending(queue_id, selected)
        reconciler.ledger.mark_sending(queue_id)
        reconciler.ledger.save()
        write_queue_fields(sheet_token, int(selected["row_number"]), {
            "Status": "SENDING",
            "Last_Error": "",
        })

        try:
            item = _message_item(selected)
            raw = _verify_local_package(item)
            response = _send_raw(sender_token, raw)
            message_id = str(response.get("id") or "").strip()
            if not message_id:
                raise RuntimeError("Gmail send returned no message ID")
            sent_payload = _fetch_raw_sent(sender_token, message_id)
            _verify_message_payload(sent_payload, item, require_sent=True)
            sent_at = utc_now()
            reconciler.ledger.mark_sent(queue_id, message_id, sent_at)
            reconciler.ledger.save()
            write_queue_fields(sheet_token, int(selected["row_number"]), {
                "Status": "SENT",
                "Sent_At": sent_at,
                "Gmail_Message_ID": message_id,
                "Last_Error": "",
            })
            _update_master_after_send(reconciler, email, message_id)
            _status(status_path, "sent", queue_id=queue_id, recipient=email, gmail_message_id=message_id)
            if once:
                return 0
            time.sleep(SUCCESS_CADENCE_SECONDS)
        except Exception as exc:
            if _is_account_level_error(exc):
                reconciler.ledger.mark_failed(queue_id, f"ACCOUNT_LEVEL: {type(exc).__name__}", permanent=False)
                reconciler.ledger.save()
                write_queue_fields(sheet_token, int(selected["row_number"]), {
                    "Status": "FAILED_TEMPORARY",
                    "Last_Error": "account-level Gmail restriction; sender stopped",
                })
                _status(status_path, "account-level-stop", error_type=type(exc).__name__)
                return 2

            permanent = _is_permanent_recipient_error(exc)
            reconciler.ledger.mark_failed(queue_id, type(exc).__name__, permanent=permanent)
            reconciler.ledger.save()
            write_queue_fields(sheet_token, int(selected["row_number"]), {
                "Status": "FAILED_PERMANENT" if permanent else "FAILED_TEMPORARY",
                "Last_Error": str(exc)[:500],
            })
            failed_this_run.add(queue_id)
            _status(status_path, "recipient-failure", queue_id=queue_id, recipient=email, permanent=permanent)
            if once:
                return 1

    _status(status_path, "window-closed", local=_local_now().isoformat())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Career Engine continuous Auto Send Queue sender")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--status", default=str(DEFAULT_STATUS))
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="Process at most one selection cycle")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        raise SystemExit("poll-seconds must be positive")
    status_path = Path(args.status)
    lock_handle = _acquire_singleton_lock(Path(args.lock))
    if lock_handle is None:
        _status(status_path, "singleton-active")
        return 0
    try:
        return run(
            ledger_path=Path(args.ledger),
            status_path=status_path,
            poll_seconds=args.poll_seconds,
            once=args.once,
        )
    except Exception as exc:
        _status(status_path, "fatal", error_type=type(exc).__name__, error=str(exc)[:500])
        return 2
    finally:
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
