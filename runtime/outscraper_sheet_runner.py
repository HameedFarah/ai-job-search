#!/usr/bin/env python3
"""Bounded, no-send Outscraper queue runner for the native Google Sheet.

The provider call is deliberately injected (and is never made by the default
dry-run). Google authentication is obtained from the existing rclone Drive
remote; credentials and response bodies are never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from career_engine.rega_enrichment.outscraper_validation import MAX_BATCH_SIZE, validate_emails
from career_engine.rega_enrichment.provider_clients import OutscraperClient, ProviderBudget

SPREADSHEET_ID = "1kFoTS-YYrTYQb1ZEtLa4k8Iy3D15ZDO1c4Q8xZ8rI1k"
SHEET_NAME = "Send Queue"
QUEUE_METADATA_KEY = "career_queue_id"
MAX_WRITE_ROWS = 25
REQUIRED = ("Email", "Outscraper_Status", "Outscraper_Verification", "Outscraper_Replacement_Email", "Outscraper_Evidence", "Outscraper_Checked_At")
SHEET_COLUMNS = ("O", "S", "T", "U", "V", "W")
EXPECTED_HEADERS = (
    "Queue_ID", "Email", "Company_or_Office", "Source_Dataset", "Source_Record_ID",
    "Source_Verification", "Source_Date_or_Freshness", "Send_Eligibility", "Gmail_Draft_ID",
    "Gmail_Message_ID", "Sender_Email", "Draft_Subject", "Attachment_1", "Attachment_2",
    "Send_State", "Sent_Message_ID", "Terminal_Outcome", "Notes", "Outscraper_Status",
    "Outscraper_Verification", "Outscraper_Replacement_Email", "Outscraper_Evidence",
    "Outscraper_Checked_At",
)


def rclone_access_token(remote: str = "gdrive", runner: Callable[..., bytes] = subprocess.check_output) -> str:
    """Refresh then read the existing rclone remote token without exposing it."""
    try:
        subprocess.run(
            ["rclone", "about", f"{remote}:", "--json"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
        raw = runner(["rclone", "config", "dump"], stderr=subprocess.DEVNULL)
        config = json.loads(raw)
        token = config.get(remote, {}).get("token", "")
        token_obj = json.loads(token) if isinstance(token, str) else token
        value = token_obj.get("access_token", "") if isinstance(token_obj, dict) else ""
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("rclone remote has no usable Google access token")
        return value.strip()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("unable to obtain Google auth from rclone") from exc


def sheets_request(token: str, method: str, url: str, payload: dict | None = None) -> dict:
    if not token or "access_token" in token.lower():
        raise RuntimeError("invalid Google auth")
    data = json.dumps(payload).encode() if payload is not None else None
    last_exc: BaseException | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict):
                    raise RuntimeError("Google Sheets returned invalid JSON")
                return body
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError("Google Sheets request failed closed") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_exc = exc
            if attempt == 2:
                raise RuntimeError("Google Sheets request failed closed") from exc
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("Google Sheets request failed closed") from last_exc


def read_queue(token: str, spreadsheet_id: str = SPREADSHEET_ID) -> list[dict[str, str]]:
    encoded = urllib.parse.quote(SHEET_NAME + "!A:W", safe="!:")
    body = sheets_request(token, "GET", f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded}")
    values = body.get("values")
    if not isinstance(values, list) or not values:
        raise RuntimeError("Send Queue is empty or malformed")
    headers = tuple(str(x) for x in values[0])
    if headers != EXPECTED_HEADERS:
        raise RuntimeError("Send Queue headers/positions do not match the authoritative schema")

    rows: list[dict[str, str]] = []
    seen_queue_ids: set[str] = set()
    seen_emails: set[str] = set()
    for sheet_row_number, raw_row in enumerate(values[1:], start=2):
        if not isinstance(raw_row, list):
            raise RuntimeError(f"Send Queue row {sheet_row_number} is malformed")
        if len(raw_row) > len(headers):
            raise RuntimeError(f"Send Queue row {sheet_row_number} exceeds authoritative width")
        # Sheets values responses omit trailing blank cells. Padding is therefore
        # limited to the authoritative trailing columns; missing Queue_ID/Email
        # still fails closed immediately below.
        normalized = [str(x) for x in raw_row] + [""] * (len(headers) - len(raw_row))
        row = dict(zip(headers, normalized))
        queue_id = row["Queue_ID"].strip()
        email = row["Email"].strip().lower()
        if not queue_id or not email:
            raise RuntimeError(f"Send Queue row {sheet_row_number} is missing immutable identity")
        if queue_id in seen_queue_ids or email in seen_emails:
            raise RuntimeError("Send Queue contains duplicate Queue_ID or normalized Email")
        seen_queue_ids.add(queue_id)
        seen_emails.add(email)
        rows.append(row)
    return rows


def _sheet_id(token: str, spreadsheet_id: str = SPREADSHEET_ID) -> int:
    encoded_fields = urllib.parse.quote("sheets(properties(sheetId,title))", safe="(),")
    body = sheets_request(
        token,
        "GET",
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}?fields={encoded_fields}",
    )
    matches = [
        int((sheet.get("properties") or {}).get("sheetId"))
        for sheet in body.get("sheets", [])
        if str((sheet.get("properties") or {}).get("title") or "") == SHEET_NAME
    ]
    if len(matches) != 1:
        raise RuntimeError("authoritative Send Queue sheet id could not be resolved uniquely")
    return matches[0]


def _queue_metadata(token: str, spreadsheet_id: str = SPREADSHEET_ID) -> list[dict]:
    body = sheets_request(
        token,
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/developerMetadata:search",
        {
            "dataFilters": [{
                "developerMetadataLookup": {
                    "metadataKey": QUEUE_METADATA_KEY,
                    "visibility": "DOCUMENT",
                }
            }]
        },
    )
    return [
        dict(item.get("developerMetadata") or {})
        for item in body.get("matchedDeveloperMetadata", [])
        if isinstance(item, dict)
    ]


def ensure_queue_metadata(token: str, spreadsheet_id: str = SPREADSHEET_ID) -> dict[str, int]:
    """Attach/verify row metadata so writes survive row inserts, deletes, and reorders."""
    rows = read_queue(token, spreadsheet_id)
    sheet_id = _sheet_id(token, spreadsheet_id)
    metadata = _queue_metadata(token, spreadsheet_id)
    by_value: dict[str, list[dict]] = {}
    for item in metadata:
        value = str(item.get("metadataValue") or "").strip()
        if value:
            by_value.setdefault(value, []).append(item)

    missing = [row for row in rows if str(row.get("Queue_ID") or "").strip() not in by_value]
    if missing:
        current_row_number = {
            str(row.get("Queue_ID") or "").strip(): row_number
            for row_number, row in enumerate(rows, start=2)
        }
        requests = []
        for row in missing:
            queue_id = str(row.get("Queue_ID") or "").strip()
            row_number = current_row_number[queue_id]
            requests.append({
                "createDeveloperMetadata": {
                    "developerMetadata": {
                        "metadataKey": QUEUE_METADATA_KEY,
                        "metadataValue": queue_id,
                        "visibility": "DOCUMENT",
                        "location": {
                            "dimensionRange": {
                                "sheetId": sheet_id,
                                "dimension": "ROWS",
                                "startIndex": row_number - 1,
                                "endIndex": row_number,
                            }
                        },
                    }
                }
            })
        sheets_request(
            token,
            "POST",
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate",
            {"requests": requests},
        )
        metadata = _queue_metadata(token, spreadsheet_id)
        by_value = {}
        for item in metadata:
            value = str(item.get("metadataValue") or "").strip()
            if value:
                by_value.setdefault(value, []).append(item)

    current_rows = read_queue(token, spreadsheet_id)
    row_by_number = {row_number: row for row_number, row in enumerate(current_rows, start=2)}
    result: dict[str, int] = {}
    for row in current_rows:
        queue_id = str(row.get("Queue_ID") or "").strip()
        matches = by_value.get(queue_id, [])
        if len(matches) != 1:
            raise RuntimeError("Send Queue developer metadata is missing or duplicated")
        location = (matches[0].get("location") or {}).get("dimensionRange") or {}
        if int(location.get("sheetId", -1)) != sheet_id or str(location.get("dimension") or "") != "ROWS":
            raise RuntimeError("Send Queue developer metadata has invalid location")
        start_index = int(location.get("startIndex", -1))
        end_index = int(location.get("endIndex", -1))
        row_number = start_index + 1
        if end_index != start_index + 1 or row_number not in row_by_number:
            raise RuntimeError("Send Queue developer metadata row location is invalid")
        located = row_by_number[row_number]
        if str(located.get("Queue_ID") or "").strip() != queue_id:
            raise RuntimeError("Send Queue developer metadata identity mismatch")
        result[queue_id] = int(matches[0].get("metadataId"))
    return result


def classify(records: list[dict]) -> dict[str, str]:
    result = {}
    for record in records:
        meta = record.get("metadata") or {}
        email = str(meta.get("email") or "").strip().lower()
        status = str(record.get("status") or "UNKNOWN").upper()
        if email:
            result[email] = status
    return result


def send_state(status: str, verification: str, status_details: str = "") -> str:
    status, verification, status_details = status.upper(), verification.upper(), status_details.upper()
    if status == "INVALID":
        return "REJECTED_OUTSCRAPER_INVALID"
    if status == "BLACKLISTED":
        return "REJECTED_OUTSCRAPER_BLACKLISTED"
    if status == "UNKNOWN" or not status:
        return "HOLD_OUTSCRAPER_UNKNOWN"
    if status == "RECEIVING":
        # Owner decision 2026-09-01: catch-all is acceptable deliverability for
        # this preparation phase. It still requires the same identity/source
        # gate as any other RECEIVING mailbox before later send eligibility.
        return "HOLD_OUTSCRAPER_IDENTITY"
    return "HOLD_OUTSCRAPER_UNKNOWN"


def _sheet_values(item: dict) -> dict[str, str]:
    status = str(item.get("provider_status") or item.get("status") or "UNKNOWN").upper()
    verification = str(item.get("verification") or "UNKNOWN").upper()
    evidence = {
        key: item.get(key)
        for key in ("provider", "source_url", "status_details", "safe_to_send")
        if key in item and item.get(key) is not None
    }
    return {
        "Send_State": send_state(status, verification, str(item.get("status_details") or "")),
        "Outscraper_Status": status,
        "Outscraper_Verification": f"{verification}{(' | ' + str(item.get('status_details'))) if item.get('status_details') else ''}",
        "Outscraper_Replacement_Email": "",
        "Outscraper_Evidence": json.dumps(evidence, separators=(",", ":"), sort_keys=True),
        "Outscraper_Checked_At": str(item.get("checked_at") or ""),
    }


def validate_queue(client: OutscraperClient, emails: list[str], budget: ProviderBudget) -> list[dict]:
    """Use the canonical validator; callers must explicitly authorize provider use."""
    return validate_emails(client, emails, budget, batch_size=MAX_BATCH_SIZE)


def _verified_metadata_ids(
    token: str,
    identities: list[tuple[str, str]],
    spreadsheet_id: str,
) -> dict[str, int]:
    metadata_ids = ensure_queue_metadata(token, spreadsheet_id)
    current_by_id = {
        str(row.get("Queue_ID") or "").strip(): str(row.get("Email") or "").strip().lower()
        for row in read_queue(token, spreadsheet_id)
    }
    result: dict[str, int] = {}
    for queue_id, email in identities:
        queue_id = queue_id.strip()
        email = email.strip().lower()
        if not queue_id or current_by_id.get(queue_id) != email or queue_id not in metadata_ids:
            raise RuntimeError("Send Queue immutable identity changed before metadata write")
        result[queue_id] = metadata_ids[queue_id]
    return result


def _data_filter_range(metadata_id: int, cells: list[object | None]) -> dict:
    return {
        "dataFilter": {"developerMetadataLookup": {"metadataId": metadata_id}},
        "majorDimension": "ROWS",
        "values": [cells],
    }


def _metadata_write(token: str, data: list[dict], spreadsheet_id: str) -> None:
    response = sheets_request(
        token,
        "POST",
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchUpdateByDataFilter",
        {
            "valueInputOption": "USER_ENTERED",
            "includeValuesInResponse": False,
            "data": data,
        },
    )
    responses = response.get("responses")
    if not isinstance(responses, list) or len(responses) != len(data):
        raise RuntimeError("Google Sheets metadata write response count mismatch")
    if any(not isinstance(item, dict) or int(item.get("updatedRows", 0)) != 1 for item in responses):
        raise RuntimeError("Google Sheets metadata write did not update exactly one row per target")


def write_updates(
    token: str,
    rows: list[tuple[str, str, dict[str, str]]],
    spreadsheet_id: str = SPREADSHEET_ID,
) -> int:
    if len(rows) > MAX_WRITE_ROWS:
        raise RuntimeError("write batch exceeds bounded limit")
    identities = [(queue_id, email) for queue_id, email, _values in rows]
    if len(set((qid.strip(), email.strip().lower()) for qid, email in identities)) != len(identities):
        raise RuntimeError("write batch contains duplicate immutable identities")
    metadata_ids = _verified_metadata_ids(token, identities, spreadsheet_id)
    data = []
    for queue_id, _email, values in rows:
        cells: list[object | None] = [None] * len(EXPECTED_HEADERS)
        cells[14] = values.get("Send_State", "")
        for index, key in zip(range(18, 23), REQUIRED[1:]):
            cells[index] = values.get(key, "")
        data.append(_data_filter_range(metadata_ids[queue_id.strip()], cells))
    _metadata_write(token, data, spreadsheet_id)
    return len(rows)


def write_state_updates(
    token: str,
    rows: list[tuple[str, str, str]],
    spreadsheet_id: str = SPREADSHEET_ID,
) -> int:
    if len(rows) > MAX_WRITE_ROWS:
        raise RuntimeError("state write batch exceeds bounded limit")
    identities = [(queue_id, email) for queue_id, email, _state in rows]
    if len(set((qid.strip(), email.strip().lower()) for qid, email in identities)) != len(identities):
        raise RuntimeError("state write batch contains duplicate immutable identities")
    metadata_ids = _verified_metadata_ids(token, identities, spreadsheet_id)
    data = []
    for queue_id, _email, state in rows:
        cells: list[object | None] = [None] * len(EXPECTED_HEADERS)
        cells[14] = state
        data.append(_data_filter_range(metadata_ids[queue_id.strip()], cells))
    _metadata_write(token, data, spreadsheet_id)
    return len(rows)


CAMPAIGN_FIELD_INDEX = {
    "Gmail_Draft_ID": 8,
    "Gmail_Message_ID": 9,
    "Send_State": 14,
    "Sent_Message_ID": 15,
    "Terminal_Outcome": 16,
}


def write_campaign_updates(
    token: str,
    rows: list[tuple[str, str, dict[str, str]]],
    spreadsheet_id: str = SPREADSHEET_ID,
) -> int:
    """Write bounded campaign provenance fields using immutable row developer metadata."""
    if len(rows) > MAX_WRITE_ROWS:
        raise RuntimeError("campaign write batch exceeds bounded limit")
    identities = [(queue_id, email) for queue_id, email, _values in rows]
    if len(set((qid.strip(), email.strip().lower()) for qid, email in identities)) != len(identities):
        raise RuntimeError("campaign write batch contains duplicate immutable identities")
    metadata_ids = _verified_metadata_ids(token, identities, spreadsheet_id)
    data = []
    for queue_id, _email, values in rows:
        unknown = set(values) - set(CAMPAIGN_FIELD_INDEX)
        if unknown:
            raise RuntimeError(f"campaign update contains unsupported fields: {sorted(unknown)}")
        cells: list[object | None] = [None] * len(EXPECTED_HEADERS)
        for field, value in values.items():
            cells[CAMPAIGN_FIELD_INDEX[field]] = value
        data.append(_data_filter_range(metadata_ids[queue_id.strip()], cells))
    if data:
        _metadata_write(token, data, spreadsheet_id)
    return len(rows)


def verify_readback(before: list[dict[str, str]], after: list[dict[str, str]]) -> dict:
    emails = [str(row.get("Email") or "").strip().lower() for row in after]
    evidence_complete = all(row.get("Outscraper_Evidence") for row in after if row.get("Outscraper_Status"))
    return {"total_rows": len(after), "unique_normalized_emails": len({email for email in emails if email}),
            "evidence_complete": evidence_complete,
            "send_state_distribution": dict(Counter(row.get("Send_State", "") for row in after)),
            "status_distribution": dict(Counter(row.get("Outscraper_Status", "") for row in after))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--readback", action="store_true")
    parser.add_argument("--results-jsonl", help="Sanitized canonical-validator results; required for --apply")
    parser.add_argument("--spreadsheet-id", default=SPREADSHEET_ID)
    args = parser.parse_args()
    if args.readback and not args.apply:
        raise SystemExit("--readback requires --apply")
    if args.apply and not args.results_jsonl:
        raise SystemExit("--apply requires --results-jsonl")
    token = rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
    readback_summary = None
    rows = read_queue(token, args.spreadsheet_id)
    updates = []
    if args.results_jsonl:
        with open(args.results_jsonl, encoding="utf-8") as handle:
            by_email = {}
            for line in handle:
                item = json.loads(line)
                email = str(item.get("email") or "").strip().lower()
                if email:
                    by_email[email] = item
        for row in rows:
            email = str(row.get("Email") or "").strip().lower()
            item = by_email.get(email)
            if item:
                updates.append((str(row.get("Queue_ID") or "").strip(), email, _sheet_values(item)))
    if args.apply:
        writes = sum(write_updates(token, updates[i:i + MAX_WRITE_ROWS], args.spreadsheet_id) for i in range(0, len(updates), MAX_WRITE_ROWS))
        if args.readback:
            after = read_queue(token, args.spreadsheet_id)
            readback_summary = verify_readback(rows, after)
            if readback_summary["total_rows"] != len(rows) or not readback_summary["evidence_complete"]:
                raise RuntimeError("Sheet readback verification failed")
        else:
            readback_summary = None
    else:
        writes = 0
    normalized_emails = [str(row.get("Email") or "").strip().lower() for row in rows]
    queue_summary = {
        "unique_normalized_emails": len({email for email in normalized_emails if email}),
        "send_state_distribution": dict(Counter(str(row.get("Send_State") or "") for row in rows)),
        "source_dataset_distribution": dict(Counter(str(row.get("Source_Dataset") or "") for row in rows)),
        "outscraper_status_nonempty": sum(bool(str(row.get("Outscraper_Status") or "").strip()) for row in rows),
        "outscraper_evidence_nonempty": sum(bool(str(row.get("Outscraper_Evidence") or "").strip()) for row in rows),
    }
    print(json.dumps({"ok": True, "rows": len(rows), "queue_summary": queue_summary, "updates": len(updates), "dry_run": not args.apply, "writes": writes, "readback": readback_summary if args.readback else None, "sends": 0, "provider_calls": 0, "secret_values_in_output": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
