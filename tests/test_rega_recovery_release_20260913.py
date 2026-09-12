from datetime import datetime, timezone
import json

import career_engine.central_outreach_sender as sender


def evidence(verified=True):
    return {
        "source": sender.REGA_RECOVERY_SOURCE,
        "verified": verified,
        "relevant": True,
        "held_reason": "",
        "master_id": "CE-00001",
        "domain": "example.sa",
        "validation": {
            "email": "hr@example.sa",
            "status": "RECEIVING",
            "safe_to_send": True,
        },
    }


def row(payload):
    return {
        "__row_number": "2",
        "Source": sender.REGA_RECOVERY_SOURCE,
        "Status": "HOLD",
        "Email": "hr@example.sa",
        "Last_Error": "SCHEDULED_2026-09-13T08:00:00+03:00",
        "Evidence_or_Notes": json.dumps(payload) + " | audit suffix",
    }


def test_due_verified_recovery_hold_is_released(monkeypatch):
    writes = []
    monkeypatch.setattr(sender, "write_queue_fields", lambda token, number, updates: writes.append((number, updates)))
    now = datetime(2026, 9, 13, 5, 0, tzinfo=timezone.utc)
    assert sender._release_due_verified_rega_holds("token", [row(evidence())], now_utc=now) == 1
    assert writes == [(2, {"Status": "PENDING", "Last_Error": ""})]


def test_recovery_hold_is_not_released_early_or_unverified(monkeypatch):
    writes = []
    monkeypatch.setattr(sender, "write_queue_fields", lambda *args, **kwargs: writes.append(True))
    early = datetime(2026, 9, 13, 4, 59, tzinfo=timezone.utc)
    due = datetime(2026, 9, 13, 5, 0, tzinfo=timezone.utc)
    assert sender._release_due_verified_rega_holds("token", [row(evidence())], now_utc=early) == 0
    assert sender._release_due_verified_rega_holds("token", [row(evidence(False))], now_utc=due) == 0
    assert writes == []
