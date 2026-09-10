from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import career_engine.central_outreach_sender as sender


ARABIC_SUBJECT = "عبدالحميد فرح | م. معماري - إدارة وتصميم"
ARABIC_BODY = """السلام عليكم ورحمة الله وبركاته،

تحية طيبة وبعد،

أتواصل معكم لبحث فرص قيادية مناسبة في مجالات إدارة التصميم، وإدارة المشاريع، وإدارة الاستشارات الهندسية.

أرفق لكم سيرتي الذاتية وملف الأعمال للاطلاع، ويسعدني مناقشة مدى ملاءمة خبرتي لاحتياجاتكم الحالية أو المستقبلية.

مع خالص التحية،

عبدالحميد فرح
hameedfarah@gmail.com"""


def _campaign() -> dict:
    return {
        "sender_email": sender.CAREER_OUTWARD_EMAIL,
        "subject": ARABIC_SUBJECT,
        "body": ARABIC_BODY,
        "content_version": "AR_V1",
        "start_at": datetime(2026, 9, 2, 8, 0, tzinfo=sender.RIYADH),
        "cadence_seconds": 96,
        "daily_cap": 300,
        "window_start_hour": 8,
        "window_end_hour": 19,
        "attachments": [
            {
                "path": str(sender.REPO_ROOT / "runtime/Abdelhamid_Farah_CV_Arabic.pdf"),
                "filename": "Abdelhamid_Farah_CV_Arabic.pdf",
                "sha256": "a" * 64,
                "drive_id": "cv-drive-id",
            },
            {
                "path": str(sender.REPO_ROOT / "runtime/Abdelhamid Farah-Portfolio-2026.pdf"),
                "filename": "Abdelhamid Farah-Portfolio-2026.pdf",
                "sha256": "b" * 64,
                "drive_id": "portfolio-drive-id",
            },
        ],
    }


def test_window_boundaries_are_exact():
    assert sender._window_open(datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc)) is True   # 08:00 Riyadh
    assert sender._window_open(datetime(2026, 9, 2, 15, 59, tzinfo=timezone.utc)) is True # 18:59 Riyadh
    assert sender._window_open(datetime(2026, 9, 2, 16, 0, tzinfo=timezone.utc)) is False # 19:00 Riyadh
    assert sender._window_open(datetime(2026, 9, 2, 4, 59, tzinfo=timezone.utc)) is False # 07:59 Riyadh


def test_window_close_buffer_prevents_edge_send_start():
    at_1858 = datetime(2026, 9, 2, 15, 58, 0, tzinfo=timezone.utc)
    at_185901 = datetime(2026, 9, 2, 15, 59, 1, tzinfo=timezone.utc)
    assert sender._seconds_until_window_close(at_1858) == 120
    assert sender._seconds_until_window_close(at_185901) == 59
    assert sender.MIN_SEND_START_BUFFER_SECONDS == 120


def test_campaign_package_is_config_driven():
    campaign = _campaign()
    assert campaign["subject"] == ARABIC_SUBJECT
    assert campaign["body"] == ARABIC_BODY
    assert campaign["content_version"] == "AR_V1"
    assert campaign["attachments"][0]["filename"] == "Abdelhamid_Farah_CV_Arabic.pdf"
    assert campaign["attachments"][1]["filename"] == "Abdelhamid Farah-Portfolio-2026.pdf"
    assert campaign["cadence_seconds"] >= sender.MIN_ALLOWED_CADENCE_SECONDS
    assert campaign["daily_cap"] == 300
    assert sender.DEFAULT_LEDGER.is_absolute()
    assert sender.DEFAULT_STATUS.is_absolute()
    assert sender.DEFAULT_LOCK.is_absolute()


def test_singleton_lock_is_exclusive(tmp_path):
    lock_path = tmp_path / "sender.lock"
    first = sender._acquire_singleton_lock(lock_path)
    assert first is not None
    second = sender._acquire_singleton_lock(lock_path)
    assert second is None
    first.close()
    third = sender._acquire_singleton_lock(lock_path)
    assert third is not None
    third.close()


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
    campaign = _campaign()
    item = sender._message_item({"queue_id": "Q1", "email": "person@baad.gov.sa"}, campaign)
    assert item["email"] == "person@baad.gov.sa"
    assert item["subject"] == ARABIC_SUBJECT
    assert item["body"] == ARABIC_BODY
    assert len(item["attachments"]) == 2
    assert [a["sha256"] for a in item["attachments"]] == ["a" * 64, "b" * 64]


def test_account_level_error_classifier():
    for text in ("Gmail API request failed (403)", "Gmail API request failed (429)", "quota exceeded", "sending limit"):
        assert sender._is_account_level_error(RuntimeError(text)) is True
    assert sender._is_account_level_error(RuntimeError("recipient not found")) is False
    # systemd treats this exact code as a deliberate hard stop and restarts
    # other non-zero infrastructure failures only.
    assert sender.ACCOUNT_LEVEL_STOP_EXIT_CODE == 75


def test_permanent_recipient_error_classifier():
    for text in ("invalid recipient", "recipient not found", "SMTP 5.1.1", "no such user"):
        assert sender._is_permanent_recipient_error(RuntimeError(text)) is True
    assert sender._is_permanent_recipient_error(RuntimeError("temporary network reset")) is False


def test_reconciliation_exclusion_outcomes_are_persisted(monkeypatch):
    from types import SimpleNamespace

    writes = []
    monkeypatch.setattr(sender, "write_queue_fields", lambda token, row, updates: writes.append((row, dict(updates))))
    reconciler = SimpleNamespace(sent_by_email={}, ledger=None)
    sender._mark_gmail_skips("token", reconciler, [
        {"row_number": 2, "status": "PENDING", "email": "a@example.com", "skip_reason": "known_contacted_company_alias"},
        {"row_number": 3, "status": "PENDING", "email": "b@example.com", "skip_reason": "jordan_held"},
        {"row_number": 4, "status": "PENDING", "email": "c@example.com", "skip_reason": "permanent_bounce"},
        {"row_number": 5, "status": "PENDING", "email": "d@example.com", "skip_reason": "unresolved_company_identity"},
    ])
    assert [update[1]["Status"] for update in writes] == [
        "SKIPPED_ALREADY_CONTACTED", "HOLD", "FAILED_PERMANENT", "HOLD"
    ]


def test_master_success_uses_sent_pending_dsn(monkeypatch):
    from types import SimpleNamespace

    batches = []
    reconciler = SimpleNamespace(
        master={"email_to_company": {"person@example.com": "exampleco"}},
        master_rows=[{
            "Queue_ID": "SEND-1",
            "Email": "person@example.com",
            "Company_or_Office": "Example Co",
            "Send_State": "READY_VERIFIED_OUTSCRAPER",
        }],
    )
    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(
        sender,
        "write_campaign_updates",
        lambda token, updates, spreadsheet_id: batches.extend(updates),
    )
    sender._update_master_after_send(reconciler, "person@example.com", "mid123")
    assert batches == [(
        "SEND-1",
        "person@example.com",
        {
            "Send_State": "SENT",
            "Sent_Message_ID": "mid123",
            "Terminal_Outcome": "sent_pending_dsn",
        },
    )]


def test_after_hours_run_exits_before_gmail_auth_or_asset_download(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(sender, "_read_campaign_config", lambda _token: _campaign())
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: False)
    monkeypatch.setattr(sender, "_local_now", lambda: datetime(2026, 9, 2, 20, 10, tzinfo=sender.RIYADH))
    monkeypatch.setattr(sender, "_status", lambda path, phase, **extra: events.append((phase, extra)))
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (_ for _ in ()).throw(AssertionError("auth must not run")))
    monkeypatch.setattr(sender, "_materialize_campaign_assets", lambda *args: (_ for _ in ()).throw(AssertionError("assets must not download")))
    code = sender.run(
        ledger_path=tmp_path / "ledger.json",
        status_path=tmp_path / "status.json",
        ready_cache_path=tmp_path / "ready.json",
        once=True,
    )
    assert code == 0
    assert events and events[0][0] == "outside-send-window"


def test_sender_profile_must_be_hameedfarah(monkeypatch, tmp_path):
    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(sender, "_read_campaign_config", lambda _token: _campaign())
    monkeypatch.setattr(sender, "_materialize_campaign_assets", lambda *args: None)
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (True, "ok"))
    monkeypatch.setattr(sender, "gmail_access_token_for_context", lambda _context: "token")
    monkeypatch.setattr(sender, "_sender_profile", lambda _token: "wrong@example.com")
    with pytest.raises(RuntimeError, match="hameedfarah@gmail.com"):
        sender.run(
            ledger_path=tmp_path / "ledger.json",
            status_path=tmp_path / "status.json",
            ready_cache_path=tmp_path / "ready.json",
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
    monkeypatch.setattr(sender, "_read_campaign_config", lambda _token: _campaign())
    monkeypatch.setattr(sender, "_materialize_campaign_assets", lambda *args: None)
    monkeypatch.setattr(sender, "_read_queue_sheet", lambda _token: [])
    monkeypatch.setattr(sender, "_status", lambda path, phase, **extra: events.append((phase, extra)))
    monkeypatch.setattr(sender, "_send_raw", lambda *args, **kwargs: calls.__setitem__("send", calls["send"] + 1))
    code = sender.run(
        ledger_path=tmp_path / "ledger.json",
        status_path=tmp_path / "status.json",
        ready_cache_path=tmp_path / "ready.json",
        once=True,
    )
    assert code == 0
    assert calls["send"] == 0
    assert events and events[-1][0] == "idle"


def test_long_running_sender_rechecks_live_start_gate_before_send(monkeypatch, tmp_path):
    events = []
    calls = {"config": 0, "send": 0}
    initial = _campaign()
    future = _campaign()
    future["start_at"] = datetime(2099, 1, 1, 8, 0, tzinfo=sender.RIYADH)
    candidate = {
        "queue_id": "OASQ-REGA-STALEGATE01",
        "email": "info@example.sa",
        "company": "Example Co",
        "priority": "IMPORTANT",
        "status": "PENDING",
        "added_at": "2026-09-10T00:00:00Z",
        "domain": "example.sa",
        "row_number": 2,
        "source": "REGA_OWNER",
        "balady_tier": "",
    }

    def read_config(_token):
        calls["config"] += 1
        return initial if calls["config"] == 1 else future

    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(sender, "_read_campaign_config", read_config)
    monkeypatch.setattr(sender, "_materialize_campaign_assets", lambda *args: None)
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (True, "ok"))
    monkeypatch.setattr(sender, "gmail_access_token_for_context", lambda _context: "sender-token")
    monkeypatch.setattr(sender, "_sender_profile", lambda _token: sender.CAREER_OUTWARD_EMAIL)
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(sender, "_seconds_until_window_close", lambda *args, **kwargs: 3600.0)
    monkeypatch.setattr(sender, "_cadence_wait_seconds", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(sender, "_refresh_ready_queue", lambda *args, **kwargs: (object(), [candidate]))
    monkeypatch.setattr(sender, "_drop_committed_ready_rows", lambda rows, *args: rows)
    monkeypatch.setattr(sender, "_sender_sent_today_count", lambda _token: 0)
    monkeypatch.setattr(sender, "_sent_today_from_ledger", lambda _r: 0)
    monkeypatch.setattr(sender, "_last_send_from_ledger", lambda _r: None)
    monkeypatch.setattr(sender, "_status", lambda path, phase, **extra: events.append((phase, extra)))
    monkeypatch.setattr(sender, "_send_raw", lambda *args, **kwargs: calls.__setitem__("send", calls["send"] + 1))

    code = sender.run(
        ledger_path=tmp_path / "ledger.json",
        status_path=tmp_path / "status.json",
        ready_cache_path=tmp_path / "ready.json",
        once=True,
    )

    assert code == 0
    assert calls["config"] == 2
    assert calls["send"] == 0
    assert events[-1][0] == "campaign-not-started"
    assert events[-1][1]["live_refresh"] is True


def test_one_send_is_sending_then_verified_sent(monkeypatch, tmp_path):
    sequence = []
    queue_writes = []
    candidate = {
        "queue_id": "Q1", "email": "person@baad.gov.sa", "company": "Example Co",
        "priority": "NORMAL", "status": "PENDING", "added_at": "2026-09-02T00:00:00Z",
        "domain": "baad.gov.sa", "row_number": 2,
    }

    class FakeLedger:
        def __init__(self):
            self.entries = {}
        def get(self, qid):
            return self.entries.get(qid, {})
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

    raw_row = {"Email": "person@baad.gov.sa", "Company_or_Office": "Example Co", "__row_number": "2"}
    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(sender, "_seconds_until_window_close", lambda *args, **kwargs: 3600.0)
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (True, "ok"))
    monkeypatch.setattr(sender, "gmail_access_token_for_context", lambda _context: "sender-token")
    monkeypatch.setattr(sender, "_sender_profile", lambda _token: sender.CAREER_OUTWARD_EMAIL)
    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(sender, "_read_campaign_config", lambda _token: _campaign())
    monkeypatch.setattr(sender, "_materialize_campaign_assets", lambda *args: None)
    monkeypatch.setattr(sender, "_read_queue_sheet", lambda _token: [raw_row])
    monkeypatch.setattr(sender, "_read_master_send_queue", lambda _token: [])
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
        ready_cache_path=tmp_path / "ready.json",
        once=True,
    )
    assert code == 0
    assert queue_writes[0]["Status"] == "SENDING"
    assert queue_writes[-1]["Status"] == "SENT"
    assert queue_writes[-1]["Gmail_Message_ID"] == "mid123"
    assert sequence.index("ledger_sending") < sequence.index("gmail_send")
    assert sequence.index("gmail_send") < sequence.index("gmail_readback") < sequence.index("verified_sent")
    assert sequence.index("verified_sent") < sequence.index("ledger_sent") < sequence.index("master_updated")


def test_fast_path_reconciles_and_counts_gmail_once_for_multiple_sends(monkeypatch, tmp_path):
    calls = {"refresh": 0, "gmail_count": 0, "send": 0}
    candidates = [
        {
            "queue_id": "OASQ-A1B2C3D4E5F6", "email": "info@companyone.sa", "company": "One Co",
            "priority": "IMPORTANT", "status": "PENDING", "added_at": "2026-09-05T00:00:00Z",
            "domain": "companyone.sa", "row_number": 2, "source": "REGA_OWNER", "balady_tier": "",
        },
        {
            "queue_id": "OASQ-F6E5D4C3B2A1", "email": "info@companytwo.sa", "company": "Two Co",
            "priority": "IMPORTANT", "status": "PENDING", "added_at": "2026-09-05T00:00:01Z",
            "domain": "companytwo.sa", "row_number": 3, "source": "REGA_OWNER", "balady_tier": "",
        },
    ]

    class FakeLedger:
        def __init__(self):
            self.entries = {}
        def get(self, qid):
            return self.entries.get(qid, {})
        def mark_pending(self, qid, row):
            self.entries.setdefault(qid, {
                "status": "PENDING", "email": row["email"], "company": row["company"],
                "domain": row["domain"], "priority": row["priority"], "added_at": row["added_at"],
            })
        def mark_sending(self, qid):
            self.entries[qid]["status"] = "SENDING"
        def mark_sent(self, qid, mid, sent_at):
            self.entries[qid].update(status="SENT", gmail_message_id=mid, sent_at=sent_at)
        def mark_failed(self, qid, error, permanent=False):
            self.entries[qid]["status"] = "FAILED_PERMANENT" if permanent else "FAILED_TEMPORARY"
        def save(self):
            pass

    class FakeReconciler:
        def __init__(self):
            self.ledger = FakeLedger()
            self.master = {"email_to_company": {}}
            self.master_rows = []

    reconciler = FakeReconciler()

    def refresh(*args, **kwargs):
        calls["refresh"] += 1
        return reconciler, [dict(row) for row in candidates]

    def gmail_count(_token):
        calls["gmail_count"] += 1
        return 0

    def send_raw(_token, _raw):
        calls["send"] += 1
        return {"id": f"mid{calls['send']}"}

    monkeypatch.setattr(sender, "_window_open", lambda *args, **kwargs: calls["send"] < 2)
    monkeypatch.setattr(sender, "_seconds_until_window_close", lambda *args, **kwargs: 3600.0)
    monkeypatch.setattr(sender, "_cadence_wait_seconds", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr(sender, "verify_both_accounts_available", lambda: (True, "ok"))
    monkeypatch.setattr(sender, "gmail_access_token_for_context", lambda _context: "sender-token")
    monkeypatch.setattr(sender, "_sender_profile", lambda _token: sender.CAREER_OUTWARD_EMAIL)
    monkeypatch.setattr(sender, "rclone_access_token", lambda: "sheet-token")
    monkeypatch.setattr(sender, "_read_campaign_config", lambda _token: _campaign())
    monkeypatch.setattr(sender, "_materialize_campaign_assets", lambda *args: None)
    monkeypatch.setattr(sender, "_refresh_ready_queue", refresh)
    monkeypatch.setattr(sender, "_sender_sent_today_count", gmail_count)
    monkeypatch.setattr(sender, "_verify_local_package", lambda _item: b"raw")
    monkeypatch.setattr(sender, "_send_raw", send_raw)
    monkeypatch.setattr(sender, "_fetch_raw_sent", lambda token, mid: {"id": mid, "labelIds": ["SENT"]})
    monkeypatch.setattr(sender, "_verify_message_payload", lambda *args, **kwargs: {})
    monkeypatch.setattr(sender, "write_queue_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(sender, "_update_master_after_send", lambda *args, **kwargs: None)
    monkeypatch.setattr(sender, "_status", lambda *args, **kwargs: None)

    code = sender.run(
        ledger_path=tmp_path / "ledger.json",
        status_path=tmp_path / "status.json",
        ready_cache_path=tmp_path / "ready.json",
    )

    assert code == 0
    assert calls == {"refresh": 1, "gmail_count": 1, "send": 2}
    assert reconciler.ledger.entries["OASQ-A1B2C3D4E5F6"]["status"] == "SENT"
    assert reconciler.ledger.entries["OASQ-F6E5D4C3B2A1"]["status"] == "SENT"
