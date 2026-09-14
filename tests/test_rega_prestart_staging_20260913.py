from pathlib import Path
import json

import career_engine.central_outreach_sender as sender


def test_prestart_stages_only_fully_verified_selected_route(monkeypatch, tmp_path):
    records = {
        "CE-1": {
            "master_id": "CE-1",
            "company": "Example Development",
            "identity_status": "confirmed",
            "discovery_version": 9,
            "domain": "example.sa",
            "selected": {
                "email": "hr@example.sa",
                "relevance_confirmed": True,
                "source_urls": ["https://example.sa/careers"],
                "validation": {
                    "email": "hr@example.sa",
                    "status": "RECEIVING",
                    "safe_to_send": True,
                },
            },
        },
        "CE-2": {
            "master_id": "CE-2",
            "company": "Held Company",
            "identity_status": "confirmed",
            "domain": "held.sa",
            "selected": {
                "email": "info@held.sa",
                "relevance_confirmed": True,
                "held_reason": "manual_hold",
                "validation": {
                    "email": "info@held.sa",
                    "status": "RECEIVING",
                    "safe_to_send": True,
                },
            },
        },
    }
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    monkeypatch.setattr(sender, "REGA_ENRICHMENT_RECORDS", path)
    calls = []
    monkeypatch.setattr(sender, "sheets_request", lambda token, method, url, payload=None: calls.append((method, url, payload)) or {})
    journal = tmp_path / "stage-journal.json"
    assert sender._stage_latest_verified_rega_records("token", [], journal_path=journal) == 1
    assert len(calls) == 1
    values = calls[0][2]["values"]
    assert len(values) == 1
    assert values[0][1] == "hr@example.sa"
    assert values[0][3] == sender.REGA_RECOVERY_SOURCE
    assert values[0][4:6] == ["IMPORTANT", "PENDING"]


def test_prestart_staging_dedupes_existing_email(monkeypatch, tmp_path):
    records = {
        "CE-1": {
            "master_id": "CE-1", "company": "Example", "identity_status": "confirmed", "discovery_version": 9, "domain": "example.sa",
            "selected": {"email": "hr@example.sa", "relevance_confirmed": True,
                         "validation": {"email": "hr@example.sa", "status": "RECEIVING", "safe_to_send": True}},
        }
    }
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    monkeypatch.setattr(sender, "REGA_ENRICHMENT_RECORDS", path)
    monkeypatch.setattr(sender, "sheets_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no write expected")))
    assert sender._stage_latest_verified_rega_records(
        "token", [{"Email": "HR@example.sa"}], journal_path=tmp_path / "stage-journal.json"
    ) == 0


def test_prestart_staging_journal_blocks_duplicate_after_uncertain_append(monkeypatch, tmp_path):
    records = {
        "CE-1": {
            "master_id": "CE-1", "company": "Example", "identity_status": "confirmed", "discovery_version": 10, "domain": "example.sa",
            "selected": {"email": "hr@example.sa", "relevance_confirmed": True,
                         "validation": {"email": "hr@example.sa", "status": "RECEIVING", "safe_to_send": True}},
        }
    }
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    monkeypatch.setattr(sender, "REGA_ENRICHMENT_RECORDS", path)
    journal = tmp_path / "stage-journal.json"
    calls = []

    def uncertain_append(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("response_lost_after_possible_commit")

    monkeypatch.setattr(sender, "sheets_request", uncertain_append)
    try:
        sender._stage_latest_verified_rega_records("token", [], journal_path=journal)
    except RuntimeError:
        pass
    assert len(calls) == 1
    payload = json.loads(journal.read_text())
    assert next(iter(payload["queue_ids"].values()))["state"] == "append_intent"

    monkeypatch.setattr(sender, "sheets_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate append must not run")))
    assert sender._stage_latest_verified_rega_records("token", [], journal_path=journal) == 0


def test_prestart_promotes_same_recovery_hold_when_checkpoint_is_now_verified(monkeypatch, tmp_path):
    email = "info@example.sa"
    queue_id = "OASQ-REGA-API-" + __import__("hashlib").sha256(email.encode()).hexdigest()[:12].upper()
    records = {
        "CE-1": {
            "master_id": "CE-1",
            "company": "Example",
            "identity_status": "confirmed",
            "discovery_version": 10,
            "domain": "example.sa",
            "selected": {
                "email": email,
                "relevance_confirmed": True,
                "source_urls": ["https://example.sa/contact"],
                "validation": {
                    "email": email,
                    "provider": "snov",
                    "status": "RECEIVING",
                    "safe_to_send": True,
                },
            },
        }
    }
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    monkeypatch.setattr(sender, "REGA_ENRICHMENT_RECORDS", path)
    writes = []
    monkeypatch.setattr(sender, "write_queue_rows_fields", lambda token, updates: writes.extend(updates))
    monkeypatch.setattr(sender, "sheets_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no append expected")))
    raw = [{
        "__row_number": "7",
        "Queue_ID": queue_id,
        "Email": email,
        "Source": sender.REGA_RECOVERY_SOURCE,
        "Priority": "IMPORTANT",
        "Status": "HOLD",
        "Sent_At": "",
        "Gmail_Message_ID": "",
        "Last_Error": "mailbox_verification_required",
        "Evidence_or_Notes": '{"verified":false}',
    }]
    journal = tmp_path / "stage-journal.json"
    assert sender._stage_latest_verified_rega_records("token", raw, journal_path=journal) == 1
    assert len(writes) == 1
    row_number, fields = writes[0]
    assert row_number == 7
    assert fields["Status"] == "PENDING"
    assert fields["Last_Error"] == ""
    evidence = json.loads(fields["Evidence_or_Notes"])
    assert evidence["verified"] is True
    assert evidence["validation"]["status"] == "RECEIVING"
    payload = json.loads(journal.read_text())
    assert payload["queue_ids"][queue_id]["state"] == "promoted_verified_hold"


def test_prestart_never_promotes_existing_sent_or_nonrecovery_row(monkeypatch, tmp_path):
    email = "info@example.sa"
    queue_id = "OASQ-REGA-API-" + __import__("hashlib").sha256(email.encode()).hexdigest()[:12].upper()
    records = {
        "CE-1": {
            "master_id": "CE-1", "company": "Example", "identity_status": "confirmed",
            "discovery_version": 10, "domain": "example.sa",
            "selected": {"email": email, "relevance_confirmed": True,
                         "validation": {"email": email, "status": "RECEIVING", "safe_to_send": True}},
        }
    }
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records))
    monkeypatch.setattr(sender, "REGA_ENRICHMENT_RECORDS", path)
    monkeypatch.setattr(sender, "write_queue_rows_fields", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no promotion expected")))
    monkeypatch.setattr(sender, "sheets_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no append expected")))
    raw = [{"__row_number": "7", "Queue_ID": queue_id, "Email": email, "Source": "OTHER", "Status": "HOLD"}]
    assert sender._stage_latest_verified_rega_records("token", raw, journal_path=tmp_path / "j.json") == 0
