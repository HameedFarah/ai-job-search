from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import career_engine.central_outreach_sender as sender


def test_window_boundaries_are_exact():
    assert sender._window_open(datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)) is True   # 08:00 Riyadh
    assert sender._window_open(datetime(2026, 9, 2, 15, 59, tzinfo=timezone.utc)) is True # 18:59 Riyadh
    assert sender._window_open(datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc)) is False # 19:00 Riyadh
    assert sender._window_open(datetime(2026, 9, 2, 4, 59, tzinfo=timezone.utc)) is False # 07:59 Riyadh


def test_package_constants_are_canonical():
    assert sender.SUBJECT == "Abdelhamid Farah | Senior Design & Project Leadership"
    assert sender.CV_PATH.name == "Abdelhamid_Farah_CV_Senior_Design_Project_Leadership.pdf"
    assert sender.CV_SHA == "e35be83899bb6b05904b5b34754d7b834a7839bc5e89d8d569fe17595c50e0d5"
    assert sender.PORTFOLIO_PATH.name == "Abdelhamid Farah-Portfolio-2026.pdf"
    assert sender.PORTFOLIO_SHA == "64f2a3b7caa1a827f8d03bf10cfa098b3c78dab73c0aa783d84e1784a4a05075"
    assert sender.SUCCESS_CADENCE_SECONDS >= 90


def test_daily_cap_listing_uses_sent_label_and_riyadh_midnight(monkeypatch):
    from urllib.parse import parse_qs, urlparse

    urls = []
    monkeypatch.setattr(sender, "_local_now", lambda: datetime(2026, 9, 2, 12, 0, tzinfo=sender.RIYADH))
    monkeypatch.setattr(sender, "_gmail_json", lambda token, method, url, payload=None: urls.append(url) or {"messages": [{"id": "m1"}]})
    assert sender._sender_sent_today_count("token") == 1
    parsed = parse_qs(urlparse(urls[0]).query)
    assert parsed["labelIds"] == ["SENT"]
    assert parsed["maxResults"] == ["500"]
    assert parsed["q"][0].startswith("after:")


def test_message_item_uses_exact_sender_package():
    item = sender._message_item({"queue_id": "Q1", "email": "person@example.com"})
    assert item["email"] == "person@example.com"
    assert item["subject"] == sender.SUBJECT
    assert item["body"] == sender.BODY
    assert len(item["attachments"]) == 2
    assert [a["sha256"] for a in item["attachments"]] == [sender.CV_SHA, sender.PORTFOLIO_SHA]


def test_account_level_error_classifier():
    for text in ("Gmail API request failed (403)", "Gmail API request failed (429)", "quota exceeded", "sending limit"):
        assert sender._is_account_level_error(RuntimeError(text)) is True
    assert sender._is_account_level_error(RuntimeError("recipient not found")) is False


def test_permanent_recipient_error_classifier():
    for text in ("invalid recipient", "recipient not found", "SMTP 5.1.1", "no such user"):
        assert sender._is_permanent_recipient_error(RuntimeError(text)) is True
    assert sender._is_permanent_recipient_error(RuntimeError("temporary network reset")) is False


def test_after_hours_run_exits_before_auth_or_sheet(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(sender, "_local_now", lambda: datetime(2026, 9, 2, 20, 10, tzinfo=sender.RIYADH))
    monkeypatch.setattr(sender, "_status", lambda path, phase, **extra: events.append((phase, extra)))
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (_ for _ in ()).throw(AssertionError("auth must not run")))
    monkeypatch.setattr(sender, "rclone_access_token", lambda: (_ for _ in ()).throw(AssertionError("sheet must not run")))
    code = sender.run(
        ledger_path=tmp_path / "ledger.json",
        status_path=tmp_path / "status.json",
        once=True,
    )
    assert code == 0
    assert events and events[0][0] == "outside-send-window"


def test_sender_profile_must_be_hameedfarah(monkeypatch, tmp_path):
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (True, "ok"))
    monkeypatch.setattr(sender, "gmail_access_token_for_context", lambda _context: "token")
    monkeypatch.setattr(sender, "_sender_profile", lambda _token: "wrong@example.com")
    with pytest.raises(RuntimeError, match="hameedfarah@gmail.com"):
        sender.run(
            ledger_path=tmp_path / "ledger.json",
            status_path=tmp_path / "status.json",
            once=True,
        )


def test_empty_queue_once_mode_performs_no_send(monkeypatch, tmp_path):
    events = []
    calls = {"send": 0}
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (True, "ok"))
    monkeypatch.setattr(sender, "gmail_access_token_for_context", lambda _context: "sender-token")
    monkeypatch.setattr(sender, "_sender_profile", lambda _token: sender.CAREER_OUTWARD_EMAIL)
    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(sender, "_read_queue_sheet", lambda _token: [])
    monkeypatch.setattr(sender, "_status", lambda path, phase, **extra: events.append((phase, extra)))
    monkeypatch.setattr(sender, "_send_raw", lambda *args, **kwargs: calls.__setitem__("send", calls["send"] + 1))
    code = sender.run(
        ledger_path=tmp_path / "ledger.json",
        status_path=tmp_path / "status.json",
        once=True,
    )
    assert code == 0
    assert calls["send"] == 0
    assert events and events[-1][0] == "idle"


def test_one_send_is_sending_then_verified_sent(monkeypatch, tmp_path):
    sequence = []
    queue_writes = []
    candidate = {
        "queue_id": "Q1", "email": "person@example.com", "company": "Example Co",
        "priority": "NORMAL", "status": "PENDING", "added_at": "2026-09-02T00:00:00Z",
        "domain": "example.com", "row_number": 2,
    }

    class FakeLedger:
        def __init__(self):
            self.entries = {}
        def mark_pending(self, qid, row):
            self.entries.setdefault(qid, {"status": "PENDING", "email": row["email"]})
        def mark_sending(self, qid):
            sequence.append("ledger_sending")
            self.entries[qid]["status"] = "SENDING"
        def mark_sent(self, qid, mid, sent_at):
            sequence.append("ledger_sent")
            self.entries[qid].update(status="SENT", gmail_message_id=mid, sent_at=sent_at)
        def mark_failed(self, *args, **kwargs):
            raise AssertionError("unexpected failure")
        def save(self):
            sequence.append("ledger_save")

    class FakeReconciler:
        def __init__(self, *args, **kwargs):
            self.ledger = FakeLedger()
            self.master = {"email_to_company": {}}
            self.master_rows = []
        def read_sheet(self):
            return []
        def normalise_all(self):
            return []
        def reconcile(self):
            return [candidate], {"excluded": []}
        def select_next(self, items, **kwargs):
            return items[0]

    raw_row = {"Email": "person@example.com", "Company_or_Office": "Example Co", "__row_number": "2"}
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (True, "ok"))
    monkeypatch.setattr(sender, "gmail_access_token_for_context", lambda _context: "sender-token")
    monkeypatch.setattr(sender, "_sender_profile", lambda _token: sender.CAREER_OUTWARD_EMAIL)
    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(sender, "_read_queue_sheet", lambda _token: [raw_row])
    monkeypatch.setattr(sender, "_persist_defaults", lambda *args, **kwargs: None)
    monkeypatch.setattr(sender, "QueueReconciler", FakeReconciler)
    monkeypatch.setattr(sender, "_sender_sent_today_count", lambda _token: 0)
    monkeypatch.setattr(sender, "_sent_today_from_ledger", lambda _r: 0)
    monkeypatch.setattr(sender, "_last_send_from_ledger", lambda _r: None)
    monkeypatch.setattr(sender, "_verify_local_package", lambda _item: b"raw")
    monkeypatch.setattr(sender, "_send_raw", lambda token, raw: sequence.append("gmail_send") or {"id": "mid123"})
    monkeypatch.setattr(sender, "_fetch_raw_sent", lambda token, mid: sequence.append("gmail_readback") or {"id": mid, "labelIds": ["SENT"]})
    monkeypatch.setattr(sender, "_verify_message_payload", lambda payload, item, require_sent: sequence.append("verified_sent") or {})
    monkeypatch.setattr(sender, "write_queue_fields", lambda token, row, updates: queue_writes.append(dict(updates)))
    monkeypatch.setattr(sender, "_update_master_after_send", lambda *args, **kwargs: sequence.append("master_updated"))
    monkeypatch.setattr(sender, "_status", lambda *args, **kwargs: None)

    code = sender.run(
        ledger_path=tmp_path / "ledger.json",
        status_path=tmp_path / "status.json",
        once=True,
    )
    assert code == 0
    assert queue_writes[0]["Status"] == "SENDING"
    assert queue_writes[-1]["Status"] == "SENT"
    assert queue_writes[-1]["Gmail_Message_ID"] == "mid123"
    assert sequence.index("ledger_sending") < sequence.index("gmail_send")
    assert sequence.index("gmail_send") < sequence.index("gmail_readback") < sequence.index("verified_sent")
    assert sequence.index("verified_sent") < sequence.index("ledger_sent") < sequence.index("master_updated")
