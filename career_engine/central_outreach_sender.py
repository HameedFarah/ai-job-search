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
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from career_engine.gmail import CAREER_OUTWARD_EMAIL, _b64url_encode
from career_engine.outreach_reconciler import (
    RIYADH,
    SENDER_GWS_CONFIG_DIR,
    WINDOW_END_HOUR,
    WINDOW_START_HOUR,
    QueueReconciler,
    _company_key,
    _email_domain,
    _is_synthetic_email,
    _is_synthetic_queue_id,
    _master_index,
    _read_master_send_queue,
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

CONFIG_SHEET_NAME = "Config"
REQUIRED_CAMPAIGN_VERSION = "AR_V1"
REQUIRED_CAMPAIGN_SEQUENCE = "REGA_FIRST_THEN_BALADY"
MIN_ALLOWED_CADENCE_SECONDS = 90

# These defaults must be independent of the caller's current working directory.
# A diagnostic/manual invocation from outside the repo must never create a
# competing ledger/status tree or fail package validation because of relative
# paths.
DEFAULT_LEDGER = REPO_ROOT / "runtime/acceptance/auto-send-queue/ledger.json"
DEFAULT_STATUS = REPO_ROOT / "runtime/acceptance/auto-send-queue/status.json"
DEFAULT_LOCK = REPO_ROOT / "runtime/acceptance/auto-send-queue/sender.lock"
DEFAULT_READY_CACHE = REPO_ROOT / "runtime/acceptance/auto-send-queue/ready-queue.json"
READY_CACHE_SCHEMA = "auto-send-ready-queue/1"
READY_REFRESH_SECONDS = 30 * 60
POLL_SECONDS = 60
# Do not begin a fresh Gmail transaction at the edge of the hard 19:00 stop.
# This margin is deliberately larger than the normal API/readback latency and
# still preserves the owner-approved operating window.
MIN_SEND_START_BUFFER_SECONDS = 120
# Distinguish a deliberate Gmail account-level stop from transient infrastructure
# failures so systemd may safely restart the latter without retrying a restricted
# sending account.
ACCOUNT_LEVEL_STOP_EXIT_CODE = 75


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(RIYADH)


def _window_open(
    now: datetime | None = None,
    *,
    start_hour: int = WINDOW_START_HOUR,
    end_hour: int = WINDOW_END_HOUR,
) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(RIYADH)
    return start_hour <= local.hour < end_hour


def _seconds_until_window_close(
    now: datetime | None = None,
    *,
    start_hour: int = WINDOW_START_HOUR,
    end_hour: int = WINDOW_END_HOUR,
) -> float:
    local = (now or datetime.now(timezone.utc)).astimezone(RIYADH)
    if not _window_open(local, start_hour=start_hour, end_hour=end_hour):
        return 0.0
    close = local.replace(hour=end_hour, minute=0, second=0, microsecond=0)
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


def _read_campaign_config(sheet_token: str) -> dict[str, Any]:
    encoded = quote(f"{CONFIG_SHEET_NAME}!A:B", safe="!:")
    body = sheets_request(
        sheet_token,
        "GET",
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{encoded}",
    )
    values = body.get("values") or []
    config: dict[str, str] = {}
    for raw in values[1:]:
        if not isinstance(raw, list) or not raw:
            continue
        key = str(raw[0] or "").strip()
        value = str(raw[1] if len(raw) > 1 else "").strip()
        if key:
            config[key] = value

    required = {
        "sender_email", "subject", "body", "attachment_1", "attachment_2",
        "attachment_1_drive_id", "attachment_2_drive_id", "next_batch_content_version",
        "next_batch_content_language", "next_batch_resume_language", "campaign_assets_status",
        "campaign_sequence", "next_send_start_at", "next_send_timezone",
        "minimum_cadence_seconds", "campaign_rolling_24h_send_cap",
        "send_window_start_hour", "send_window_end_hour",
    }
    missing = sorted(key for key in required if not config.get(key))
    if missing:
        raise RuntimeError(f"campaign Config missing required keys: {missing}")
    if config["sender_email"].lower() != CAREER_OUTWARD_EMAIL:
        raise RuntimeError("campaign Config sender mismatch")
    if config["next_batch_content_version"] != REQUIRED_CAMPAIGN_VERSION:
        raise RuntimeError("campaign Config content version mismatch")
    if config["next_batch_content_language"].upper() != "ARABIC":
        raise RuntimeError("campaign Config content language is not ARABIC")
    if config["next_batch_resume_language"].upper() != "ARABIC":
        raise RuntimeError("campaign Config resume language is not ARABIC")
    if config["campaign_assets_status"].upper() != "READY":
        raise RuntimeError("campaign assets are not READY")
    if config["campaign_sequence"].upper() != REQUIRED_CAMPAIGN_SEQUENCE:
        raise RuntimeError("campaign sequence mismatch")
    if config["next_send_timezone"] != "Asia/Riyadh":
        raise RuntimeError("campaign timezone mismatch")

    start_at = datetime.fromisoformat(config["next_send_start_at"])
    if start_at.tzinfo is None:
        raise RuntimeError("campaign start must be timezone-aware")
    cadence_seconds = int(config["minimum_cadence_seconds"])
    daily_cap = int(config["campaign_rolling_24h_send_cap"])
    window_start = int(config["send_window_start_hour"])
    window_end = int(config["send_window_end_hour"])
    if cadence_seconds < MIN_ALLOWED_CADENCE_SECONDS:
        raise RuntimeError("campaign cadence is below hard safety minimum")
    if daily_cap <= 0 or daily_cap > 300:
        raise RuntimeError("campaign send cap is outside approved safety bound")
    if not (0 <= window_start < window_end <= 24):
        raise RuntimeError("campaign send window is invalid")

    return {
        "sender_email": config["sender_email"].lower(),
        "subject": config["subject"],
        "body": config["body"],
        "content_version": config["next_batch_content_version"],
        "start_at": start_at,
        "cadence_seconds": cadence_seconds,
        "daily_cap": daily_cap,
        "window_start_hour": window_start,
        "window_end_hour": window_end,
        "attachments": [
            {
                "filename": config["attachment_1"],
                "drive_id": config["attachment_1_drive_id"],
            },
            {
                "filename": config["attachment_2"],
                "drive_id": config["attachment_2_drive_id"],
            },
        ],
    }


def _download_drive_asset(token: str, drive_id: str, path: Path) -> str:
    request = Request(
        f"https://www.googleapis.com/drive/v3/files/{drive_id}?alt=media",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            data = response.read()
    except Exception as exc:
        raise RuntimeError(f"failed to materialize campaign asset {path.name}") from exc
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"Drive campaign asset is not a PDF: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return hashlib.sha256(data).hexdigest()


def _materialize_campaign_assets(sheet_token: str, campaign: dict[str, Any]) -> None:
    attachments = []
    for spec in campaign["attachments"]:
        path = REPO_ROOT / "runtime" / str(spec["filename"])
        digest = _download_drive_asset(sheet_token, str(spec["drive_id"]), path)
        attachments.append({
            "path": str(path),
            "filename": path.name,
            "sha256": digest,
            "drive_id": str(spec["drive_id"]),
        })
    campaign["attachments"] = attachments


def _message_item(row: dict[str, Any], campaign: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue_id": row["queue_id"],
        "email": row["email"],
        "subject": campaign["subject"],
        "body": campaign["body"],
        "attachments": [dict(item) for item in campaign["attachments"]],
    }


def _verify_local_package(item: dict[str, Any]) -> bytes:
    for attachment in item["attachments"]:
        path = Path(str(attachment["path"]))
        if not path.is_file() or not path.read_bytes().startswith(b"%PDF"):
            raise RuntimeError(f"missing/invalid campaign PDF: {path}")
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


def _is_synthetic_guard_error(exc: Exception) -> bool:
    """Return True when the error originates from the synthetic guard."""
    return "FAIL_CLOSED_SYNTHETIC" in str(exc)


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


def _cache_row(row: dict[str, Any]) -> dict[str, Any]:
    """Persist only fields required by the deterministic send transaction."""
    keys = (
        "queue_id", "email", "company", "source", "priority", "status",
        "added_at", "domain", "row_number", "balady_tier",
    )
    return {key: row.get(key, "") for key in keys}


def _write_ready_cache(path: Path, rows: list[dict[str, Any]]) -> None:
    _atomic_status(path, {
        "schema": READY_CACHE_SCHEMA,
        "refreshed_at": utc_now(),
        "ready_count": len(rows),
        "rows": [_cache_row(row) for row in rows],
    })


def _refresh_ready_queue(
    sheet_token: str,
    ledger_path: Path,
    ready_cache_path: Path,
    failed_this_run: set[str],
) -> tuple[QueueReconciler, list[dict[str, Any]]]:
    """Run the expensive canonical reconciliation once and cache its ordered result."""
    raw_rows = _read_queue_sheet(sheet_token)
    _persist_defaults(sheet_token, raw_rows)

    reconciler = QueueReconciler(sheet_token, ledger_path=ledger_path)
    reconciler.raw_rows = raw_rows
    reconciler.normalise_all()

    if not _queue_has_work(raw_rows):
        _write_ready_cache(ready_cache_path, [])
        return reconciler, []

    reconciler.master_rows = _read_master_send_queue(sheet_token)
    reconciler.master = _master_index(reconciler.master_rows)
    eligible, skips = reconciler.reconcile()
    _mark_gmail_skips(sheet_token, reconciler, skips.get("excluded", []))
    eligible = [row for row in eligible if row["queue_id"] not in failed_this_run]
    _write_ready_cache(ready_cache_path, eligible)
    return reconciler, eligible


def _drop_committed_ready_rows(
    rows: list[dict[str, Any]],
    reconciler: QueueReconciler,
    failed_this_run: set[str],
) -> list[dict[str, Any]]:
    """Drop rows already committed locally without touching Google/Gmail."""
    keep: list[dict[str, Any]] = []
    for row in rows:
        queue_id = str(row.get("queue_id") or "")
        if not queue_id or queue_id in failed_this_run:
            continue
        status = str(reconciler.ledger.get(queue_id).get("status") or "").upper()
        if status in {"SENT", "FAILED_PERMANENT"}:
            continue
        keep.append(row)
    return keep


def _cadence_wait_seconds(last_send_utc: datetime | None, cadence_seconds: int) -> float:
    if last_send_utc is None:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - last_send_utc).total_seconds()
    return max(0.0, float(cadence_seconds) - elapsed)


def run(
    *,
    ledger_path: Path,
    status_path: Path,
    ready_cache_path: Path = DEFAULT_READY_CACHE,
    poll_seconds: int = POLL_SECONDS,
    once: bool = False,
) -> int:
    sheet_token = rclone_access_token()
    campaign = _read_campaign_config(sheet_token)
    now_utc = datetime.now(timezone.utc)
    if now_utc < campaign["start_at"].astimezone(timezone.utc):
        _status(
            status_path,
            "campaign-not-started",
            local=_local_now().isoformat(),
            start_at=campaign["start_at"].isoformat(),
            content_version=campaign["content_version"],
        )
        return 0
    if not _window_open(
        now_utc,
        start_hour=campaign["window_start_hour"],
        end_hour=campaign["window_end_hour"],
    ):
        _status(status_path, "outside-send-window", local=_local_now().isoformat())
        return 0

    _materialize_campaign_assets(sheet_token, campaign)

    ok, detail = verify_both_accounts_available()
    if not ok:
        raise RuntimeError(f"FAIL_CLOSED_BOTH_GMAIL_REQUIRED: {detail}")
    sender_token = gmail_access_token_for_context(SENDER_GWS_CONFIG_DIR)
    if _sender_profile(sender_token) != CAREER_OUTWARD_EMAIL:
        raise RuntimeError("sender OAuth context is not hameedfarah@gmail.com")

    failed_this_run: set[str] = set()
    reconciler, ready_queue = _refresh_ready_queue(
        sheet_token,
        ledger_path,
        ready_cache_path,
        failed_this_run,
    )
    last_refresh_mono = time.monotonic()
    sender_sent_today = 0
    gmail_count_loaded = False

    while _window_open(
        start_hour=campaign["window_start_hour"],
        end_hour=campaign["window_end_hour"],
    ):
        seconds_left = _seconds_until_window_close(
            start_hour=campaign["window_start_hour"],
            end_hour=campaign["window_end_hour"],
        )
        if 0 < seconds_left < MIN_SEND_START_BUFFER_SECONDS:
            _status(status_path, "window-closing", seconds_until_close=round(seconds_left, 3))
            return 0

        # Cadence is pure local state; evaluate it before any remote refresh.
        last_send = _last_send_from_ledger(reconciler)
        cadence_wait = _cadence_wait_seconds(last_send, campaign["cadence_seconds"])
        if cadence_wait > 0:
            _status(status_path, "cadence-wait", seconds=round(cadence_wait, 3), ready=len(ready_queue))
            if once:
                return 0
            time.sleep(max(0.1, min(cadence_wait, seconds_left)))
            continue

        ready_queue = _drop_committed_ready_rows(ready_queue, reconciler, failed_this_run)
        refresh_due = (
            not ready_queue
            or (time.monotonic() - last_refresh_mono) >= READY_REFRESH_SECONDS
        )
        if refresh_due:
            sheet_token = rclone_access_token()
            reconciler, ready_queue = _refresh_ready_queue(
                sheet_token,
                ledger_path,
                ready_cache_path,
                failed_this_run,
            )
            last_refresh_mono = time.monotonic()
            ready_queue = _drop_committed_ready_rows(ready_queue, reconciler, failed_this_run)
            # External/manual sends can change the conservative account-level
            # daily count, so refresh it only with a populated periodic refresh.
            if ready_queue:
                sender_sent_today = _sender_sent_today_count(sender_token)
                gmail_count_loaded = True
            else:
                gmail_count_loaded = False

        if not ready_queue:
            _status(status_path, "idle", ready=0, sender=CAREER_OUTWARD_EMAIL)
            if once:
                return 0
            time.sleep(max(1, poll_seconds))
            continue

        if not gmail_count_loaded:
            sender_sent_today = _sender_sent_today_count(sender_token)
            gmail_count_loaded = True

        if sender_sent_today >= campaign["daily_cap"]:
            _status(
                status_path,
                "daily-cap",
                sender_sent_today=sender_sent_today,
                cap=campaign["daily_cap"],
            )
            return 0

        sent_today = _sent_today_from_ledger(reconciler)
        if max(sent_today, sender_sent_today) >= campaign["daily_cap"]:
            _status(
                status_path,
                "daily-cap",
                sender_sent_today=sender_sent_today,
                ledger_sent_today=sent_today,
                cap=campaign["daily_cap"],
            )
            return 0

        selected = ready_queue[0]
        queue_id = str(selected["queue_id"])
        email = str(selected["email"]).lower()
        reconciler.ledger.mark_pending(queue_id, selected)
        reconciler.ledger.mark_sending(queue_id)
        reconciler.ledger.save()
        write_queue_fields(sheet_token, int(selected["row_number"]), {
            "Status": "SENDING",
            "Last_Error": "",
        })

        gmail_send_committed = False
        committed_message_id = ""
        accounted_committed = False
        try:
            item = _message_item(selected, campaign)
            # Fail-closed synthetic guard: never send an obviously fake or
            # test identity even if it slipped through earlier filters.
            if _is_synthetic_email(selected.get("email", "")) or \
               _is_synthetic_queue_id(selected.get("queue_id", "")):
                _status(status_path, "synthetic-blocked", queue_id=queue_id, recipient=email)
                raise RuntimeError(
                    f"FAIL_CLOSED_SYNTHETIC: queue_id={queue_id} email={email}")
            raw = _verify_local_package(item)
            response = _send_raw(sender_token, raw)
            message_id = str(response.get("id") or "").strip()
            if not message_id:
                raise RuntimeError("Gmail send returned no message ID")
            sent_payload = _fetch_raw_sent(sender_token, message_id)
            _verify_message_payload(sent_payload, item, require_sent=True)

            # From this point Gmail delivery is committed. No downstream
            # Sheets/Master failure may turn this recipient into retryable.
            gmail_send_committed = True
            committed_message_id = message_id
            sent_at = utc_now()
            reconciler.ledger.mark_sent(queue_id, message_id, sent_at)
            reconciler.ledger.save()
            sender_sent_today += 1
            ready_queue = [row for row in ready_queue if str(row.get("queue_id") or "") != queue_id]
            try:
                _write_ready_cache(ready_cache_path, ready_queue)
            except Exception:
                pass
            accounted_committed = True
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
            continue
        except Exception as exc:
            if gmail_send_committed:
                # Gmail send already succeeded. Best-effort repair the queue,
                # but never classify this as recipient failure / retryable.
                if not accounted_committed:
                    sender_sent_today += 1
                    ready_queue = [
                        row for row in ready_queue
                        if str(row.get("queue_id") or "") != queue_id
                    ]
                    try:
                        _write_ready_cache(ready_cache_path, ready_queue)
                    except Exception:
                        pass
                    accounted_committed = True
                try:
                    write_queue_fields(sheet_token, int(selected["row_number"]), {
                        "Status": "SENT",
                        "Sent_At": utc_now(),
                        "Gmail_Message_ID": committed_message_id,
                        "Last_Error": "",
                    })
                except Exception:
                    pass

                failed_this_run.add(queue_id)
                _status(
                    status_path,
                    "sent-post-send-persistence-warning",
                    queue_id=queue_id,
                    recipient=email,
                    gmail_message_id=committed_message_id,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                )
                if once:
                    return 0
                continue

            if _is_account_level_error(exc):
                reconciler.ledger.mark_failed(queue_id, f"ACCOUNT_LEVEL: {type(exc).__name__}", permanent=False)
                reconciler.ledger.save()
                write_queue_fields(sheet_token, int(selected["row_number"]), {
                    "Status": "FAILED_TEMPORARY",
                    "Last_Error": "account-level Gmail restriction; sender stopped",
                })
                _status(status_path, "account-level-stop", error_type=type(exc).__name__)
                return ACCOUNT_LEVEL_STOP_EXIT_CODE

            # Synthetic guard violation — permanent reject, never retry.
            if _is_synthetic_guard_error(exc):
                reconciler.ledger.mark_failed(queue_id, "SYNTHETIC_GUARD", permanent=True)
                reconciler.ledger.save()
                write_queue_fields(sheet_token, int(selected["row_number"]), {
                    "Status": "FAILED_PERMANENT",
                    "Last_Error": "synthetic/test-identity guard rejected",
                })
                _status(status_path, "synthetic-permanent-fail", queue_id=queue_id, recipient=email)
                continue

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


def preflight(*, ledger_path: Path, status_path: Path) -> int:
    """Exercise the live campaign configuration and dedupe path without sending email."""
    sheet_token = rclone_access_token()
    campaign = _read_campaign_config(sheet_token)
    _materialize_campaign_assets(sheet_token, campaign)

    ok, detail = verify_both_accounts_available()
    if not ok:
        raise RuntimeError(f"FAIL_CLOSED_BOTH_GMAIL_REQUIRED: {detail}")
    sender_token = gmail_access_token_for_context(SENDER_GWS_CONFIG_DIR)
    if _sender_profile(sender_token) != CAREER_OUTWARD_EMAIL:
        raise RuntimeError("sender OAuth context is not hameedfarah@gmail.com")

    raw_rows = _read_queue_sheet(sheet_token)
    reconciler = QueueReconciler(sheet_token, ledger_path=ledger_path)
    reconciler.raw_rows = raw_rows
    reconciler.normalise_all()
    reconciler.master_rows = _read_master_send_queue(sheet_token)
    reconciler.master = _master_index(reconciler.master_rows)
    eligible, skips = reconciler.reconcile()

    rega = [row for row in eligible if "REGA" in str(row.get("source") or "").upper()]
    balady = [row for row in eligible if "BALADY" in str(row.get("source") or "").upper()]
    if rega and (not eligible or "REGA" not in str(eligible[0].get("source") or "").upper()):
        raise RuntimeError("REGA-first ordering invariant failed")

    _status(
        status_path,
        "preflight-ok",
        sender=campaign["sender_email"],
        content_version=campaign["content_version"],
        start_at=campaign["start_at"].isoformat(),
        cadence_seconds=campaign["cadence_seconds"],
        daily_cap=campaign["daily_cap"],
        window=[campaign["window_start_hour"], campaign["window_end_hour"]],
        queue_rows=len(raw_rows),
        eligible=len(eligible),
        eligible_rega=len(rega),
        eligible_balady=len(balady),
        skipped=sum(len(rows) for rows in skips.values()),
        next_queue_id=str(eligible[0].get("queue_id") or "") if eligible else "",
        next_recipient=str(eligible[0].get("email") or "") if eligible else "",
        next_source=str(eligible[0].get("source") or "") if eligible else "",
        sends=0,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Career Engine continuous Auto Send Queue sender")
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--status", default=str(DEFAULT_STATUS))
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--ready-cache", default=str(DEFAULT_READY_CACHE))
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="Process at most one selection cycle")
    parser.add_argument("--preflight", action="store_true", help="Verify live config/accounts/queue ordering without sending")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        raise SystemExit("poll-seconds must be positive")
    status_path = Path(args.status)
    lock_handle = _acquire_singleton_lock(Path(args.lock))
    if lock_handle is None:
        _status(status_path, "singleton-active")
        return 0
    try:
        if args.preflight:
            return preflight(ledger_path=Path(args.ledger), status_path=status_path)
        return run(
            ledger_path=Path(args.ledger),
            status_path=status_path,
            ready_cache_path=Path(args.ready_cache),
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
