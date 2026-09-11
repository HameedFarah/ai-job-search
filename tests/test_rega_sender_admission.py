import importlib.util
from pathlib import Path
from datetime import datetime

spec = importlib.util.spec_from_file_location("rega_sender_admission", Path(__file__).parents[1] / "scripts/rega_sender_admission.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def record(email="info@example.sa", **changes):
    c = {"email": email, "rank": 2, "relevance_confirmed": True,
         "validation": {"email": email, "status": "RECEIVING", "safe_to_send": True}}
    c.update(changes)
    return {"master_id": "CE-1", "company": "Example", "domain": "example.sa", "identity_status": "confirmed", "contacts": [c]}


def test_only_exact_verified_relevant_mailbox_can_release():
    r = record(); assert m.candidate_rows({"1": r})["info@example.sa"]["verified"]
    for override in [{"relevance_confirmed": False}, {"held_reason": "permanent_bounce"},
                     {"validation": {"email": "other@example.sa", "status": "RECEIVING", "safe_to_send": True}},
                     {"validation": {"email": "info@example.sa", "status": "UNKNOWN", "safe_to_send": False}}]:
        assert not m.candidate_rows({"1": record(**override)})["info@example.sa"]["verified"]


def test_all_candidates_are_staged_but_not_all_are_sendable():
    r = record(relevance_confirmed=False)
    assert len(m.candidate_rows({"1": r, "2": r})) == 1
    assert not m.candidate_rows({"1": r})["info@example.sa"]["verified"]


def test_release_is_dated_and_never_before_eight():
    a = {"not_before": "2026-09-13T08:00:00+03:00"}
    assert not m.release_time_ok(a, datetime.fromisoformat("2026-09-12T08:00:00+03:00"))
    assert not m.release_time_ok(a, datetime.fromisoformat("2026-09-13T07:59:59+03:00"))
    assert m.release_time_ok(a, datetime.fromisoformat("2026-09-13T08:00:00+03:00"))
    assert not m.release_time_ok(a, datetime.fromisoformat("2026-09-14T08:00:00+03:00"))
    assert not m.release_time_ok(a, datetime.fromisoformat("2026-09-13T19:00:00+03:00"))


def test_stage_only_adds_holds_and_preserves_existing_sent(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    headers = ["Queue_ID", "Email", "Company_or_Office", "Source", "Priority", "Status", "Added_At", "Sent_At", "Gmail_Message_ID", "Last_Error", "Evidence_or_Notes"]
    existing = {"Queue_ID": "OLD", "Email": "old@example.sa", "Source": "earlier", "Status": "SENT", "__row_number": "2"}
    rows = [dict(existing)]
    def request(token, method, url, body):
        assert ":append" in url and "valueInputOption=RAW" in url
        for values in body["values"]:
            row = dict(zip(headers, values)); row["__row_number"] = str(len(rows)+2); rows.append(row)
        return {}
    sheet = SimpleNamespace(rclone_access_token=lambda: "token", SPREADSHEET_ID="id", sheets_request=request)
    reconcile = SimpleNamespace(_read_queue_sheet=lambda token: rows)
    candidates = m.candidate_rows({"1": record(), "2": record("old@example.sa")})
    a = {"id": "REGA-OWNER-20260912", "not_before": "2026-09-13T08:00:00+03:00"}
    report = m.stage(SimpleNamespace(apply=True, root=tmp_path), a, candidates, None, reconcile, sheet)
    assert rows[0] == existing
    assert rows[1]["Status"] == "HOLD"
    assert rows[1]["Last_Error"].startswith("SCHEDULED_")
    assert report["readback"] == "verified"


def test_early_release_fails_before_network(tmp_path):
    import pytest
    from types import SimpleNamespace
    from unittest.mock import Mock
    sheet = Mock()
    with pytest.raises(RuntimeError, match="outside_authorized_release_time"):
        m.release(SimpleNamespace(apply=True, root=tmp_path), {"not_before": "2099-09-13T08:00:00+03:00"}, {}, Mock(), Mock(), sheet)
    sheet.rclone_access_token.assert_not_called()
