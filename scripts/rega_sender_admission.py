#!/usr/bin/env python3
"""Stage REGA evidence in the existing Auto Send Queue; dated verified release only.

No Gmail transmission implementation. The maintained central sender owns sending.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

SOURCE = "REGA_API_RECOVERY_20260912"
RUNTIME = Path("/home/hameedo/projects/ai-job-search-daily-runtime")
ROOT = Path("/home/hameedo/projects/ai-job-search/runtime/acceptance/rega-api-enrichment-20260911")


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    temp.replace(path)


def candidate_rows(records):
    result = {}
    for record in records.values():
        if record.get("excluded"):
            continue
        for contact in record.get("contacts", []):
            email = str(contact.get("email", "")).lower().strip()
            if not re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", email):
                continue
            validation = contact.get("validation") or {}
            relevant = (record.get("identity_status") == "confirmed"
                        and email.split("@")[1] == record.get("domain")
                        and contact.get("relevance_confirmed") is True
                        and not contact.get("held_reason"))
            verified = (relevant and validation.get("safe_to_send") is True
                        and validation.get("status") == "RECEIVING"
                        and validation.get("email") == email)
            item = {"email": email, "company": record.get("company", ""),
                    "master_id": record["master_id"], "domain": record.get("domain", ""),
                    "rank": contact.get("rank", 9), "verified": bool(verified),
                    "relevant": bool(relevant), "validation": validation,
                    "source_urls": contact.get("source_urls", []),
                    "hiring_evidence": contact.get("hiring_evidence", []),
                    "held_reason": contact.get("held_reason") or ("" if verified else "mailbox_verification_required")}
            previous = result.get(email)
            if previous is None or (item["verified"], item["relevant"], -item["rank"]) > (previous["verified"], previous["relevant"], -previous["rank"]):
                result[email] = item
    return result


def queue_id(email):
    return "OASQ-REGA-API-" + hashlib.sha256(email.encode()).hexdigest()[:12].upper()


def evidence(item, authorization):
    return json.dumps({"source": SOURCE, "authorization_id": authorization["id"],
                       "not_before": authorization["not_before"], **item}, ensure_ascii=False, separators=(",", ":"))


def release_time_ok(authorization, timestamp):
    start = datetime.fromisoformat(authorization["not_before"])
    if start.tzinfo is None or timestamp.tzinfo is None:
        return False
    # No late next-day automatic release of an owner-approved dated batch.
    return start <= timestamp < start.replace(hour=19)


def runtime_modules():
    sys.path.insert(0, str(RUNTIME))
    from career_engine import central_outreach_sender as sender
    from career_engine import outreach_reconciler as reconcile
    from runtime import outscraper_sheet_runner as sheet
    return sender, reconcile, sheet


def check_package(sender, sheet, authorization, token):
    campaign = sender._read_campaign_config(token)
    sender._materialize_campaign_assets(token, campaign)
    for key in ("sender_email", "subject", "body", "content_version"):
        if campaign[key] != authorization["campaign"][key]:
            raise RuntimeError("approved_campaign_changed:" + key)
    actual = {(x["filename"], x["sha256"]) for x in campaign["attachments"]}
    expected = {(x["filename"], x["sha256"]) for x in authorization["campaign"]["attachments"]}
    if actual != expected or len(actual) != 2:
        raise RuntimeError("approved_attachment_changed")
    return campaign


def stage(args, authorization, candidates, sender, reconcile, sheet):
    token = sheet.rclone_access_token()
    rows = reconcile._read_queue_sheet(token)
    by_email = {}
    for row in rows:
        email = row.get("Email", "").lower().strip()
        if email in by_email:
            raise RuntimeError("existing_queue_duplicate_email")
        by_email[email] = row
    changes = []; additions = []; report = {"at": now(), "action": "stage", "apply": args.apply,
        "candidate_emails": len(candidates), "added": [], "existing": [], "verified_candidates": []}
    for email, item in candidates.items():
        prior = by_email.get(email)
        if item["verified"]:
            report["verified_candidates"].append(email)
        if prior and prior.get("Source") != SOURCE:
            report["existing"].append({"email": email, "status": prior.get("Status"), "queue_id": prior.get("Queue_ID")})
            continue
        notes = evidence(item, authorization)
        reason = "SCHEDULED_" + authorization["not_before"] if item["verified"] else item["held_reason"]
        if prior:
            # Never alter owner/other-workflow holds or terminal/released rows.
            if prior.get("Status") != "HOLD" or not str(prior.get("Last_Error", "")).startswith(("SCHEDULED_", "mailbox_verification", "needs_current", "already_known", "permanent_bounce")):
                continue
            changes.extend([{"range": "'Auto Send Queue'!" + col + prior["__row_number"], "values": [[value]]}
                            for col, value in [("J", reason), ("K", notes)]])
        else:
            additions.append([queue_id(email), email, item["company"], SOURCE, "IMPORTANT", "HOLD", now(), "", "", reason, notes])
            report["added"].append(email)
    if args.apply:
        base = "https://sheets.googleapis.com/v4/spreadsheets/" + sheet.SPREADSHEET_ID
        if additions:
            sheet.sheets_request(token, "POST", base + "/values/Auto%20Send%20Queue!A:K:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS", {"values": additions})
        if changes:
            sheet.sheets_request(token, "POST", base + "/values:batchUpdate", {"valueInputOption": "RAW", "data": changes})
        current = reconcile._read_queue_sheet(token)
        for email in candidates:
            matching = [r for r in current if r.get("Email", "").lower().strip() == email]
            if len(matching) != 1:
                raise RuntimeError("stage_readback_identity_mismatch")
            if email in report["added"] and (matching[0]["Status"] != "HOLD" or matching[0]["Source"] != SOURCE):
                raise RuntimeError("stage_readback_hold_mismatch")
        report["readback"] = "verified"
    save(args.root / "sender-admission-stage.json", report)
    return report


def release(args, authorization, candidates, sender, reconcile, sheet):
    timestamp = datetime.now(timezone.utc)
    if args.apply and not release_time_ok(authorization, timestamp):
        raise RuntimeError("outside_authorized_release_time")
    token = sheet.rclone_access_token()
    campaign = check_package(sender, sheet, authorization, token)
    r = reconcile.QueueReconciler(token, ledger_path=sender.DEFAULT_LEDGER)
    rows = r.read_sheet(); owned = {}
    for row in rows:
        email = row.get("Email", "").lower().strip()
        if row.get("Source") == SOURCE and row.get("Status") == "HOLD" and row.get("Queue_ID") == queue_id(email):
            try:
                old = json.loads(row.get("Evidence_or_Notes", ""))
            except ValueError:
                continue
            candidate = candidates.get(email, {})
            if old.get("authorization_id") == authorization["id"] and candidate.get("verified") and row.get("Last_Error", "").startswith("SCHEDULED_"):
                owned[row["Queue_ID"]] = row
    simulated = [dict(row, Status="PENDING") if row.get("Queue_ID") in owned else row for row in rows]
    # A company's HR address wins over general addresses among this batch.
    simulated.sort(key=lambda row: (row.get("Queue_ID") in owned, candidates.get(row.get("Email", ""), {}).get("rank", 9)))
    r.normalised = [reconcile.normalise_row(row) for row in simulated]
    eligible, skipped = r.reconcile()
    ready = [row for row in eligible if row["queue_id"] in owned]
    for row in ready:
        sender._verify_local_package(sender._message_item(row, campaign))
    report = {"at": now(), "action": "release", "apply": args.apply,
              "not_before": authorization["not_before"], "ready_count": len(ready),
              "ready": [{"queue_id": x["queue_id"], "email": x["email"], "company": x["company"]} for x in ready],
              "held_or_deduped": sorted(set(owned) - {x["queue_id"] for x in ready}), "sends": 0}
    if args.apply:
        # Refresh rows by immutable identity immediately before each update.
        current = {x["Queue_ID"]: x for x in reconcile._read_queue_sheet(token)}
        for item in ready:
            row = current[item["queue_id"]]
            if row.get("Email") != item["email"] or row.get("Status") != "HOLD" or row.get("Source") != SOURCE:
                raise RuntimeError("release_concurrent_change")
            reconcile.write_queue_fields(token, int(row["__row_number"]), {"Status": "PENDING", "Last_Error": "", "Evidence_or_Notes": row["Evidence_or_Notes"] + " | Owner authorized dated release " + now()})
        current = {x["Queue_ID"]: x for x in reconcile._read_queue_sheet(token)}
        if any(current[x["queue_id"]].get("Status") != "PENDING" for x in ready):
            raise RuntimeError("release_readback_failed")
        report["readback"] = "verified"
    save(args.root / ("sender-admission-release.json" if args.apply else "sender-admission-preflight.json"), report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["stage", "release"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with (args.root / "sender-admission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        authorization = json.loads((args.root / "sender-authorization.json").read_text())
        if authorization.get("id") != "REGA-OWNER-20260912" or authorization.get("source") != SOURCE:
            raise RuntimeError("missing_owner_authorization")
        candidates = candidate_rows(json.loads((args.root / "records.json").read_text()))
        sender, reconcile, sheet = runtime_modules()
        action = stage if args.action == "stage" else release
        report = action(args, authorization, candidates, sender, reconcile, sheet)
        print(json.dumps({k:v for k,v in report.items() if k not in {"ready", "existing", "added", "verified_candidates", "held_or_deduped"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
