from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from runtime.outscraper_bulk_queue_validate import (
    _eligible_pending,
    _preflight_queue,
    atomic_json,
    journal_replay_items,
    load_journal,
)


def _selected(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"queue_id": queue_id, "email": email} for queue_id, email in pairs]


def test_journal_atomic_roundtrip():
    with TemporaryDirectory() as td:
        path = Path(td) / "journal.json"
        atomic_json(path, {"state": "complete", "results": [{"email": "a@example.com"}]})
        assert load_journal(path)["state"] == "complete"


def test_inflight_journal_fails_closed_without_replay():
    with pytest.raises(RuntimeError, match="ambiguous"):
        journal_replay_items({"state": "inflight"}, _selected(("SEND-1", "a@example.com")))


def test_complete_journal_replays_exact_matching_selection():
    selected = _selected(("SEND-2", "b@example.com"))
    journal = {
        "state": "complete",
        "selected": selected,
        "results": [
            {"email": "b@example.com", "provider_status": "INVALID"},
        ],
    }
    assert journal_replay_items(journal, selected) == [
        {"email": "b@example.com", "provider_status": "INVALID"}
    ]


def test_applied_journal_allows_new_call_cycle():
    assert journal_replay_items({"state": "applied"}, _selected(("SEND-3", "c@example.com"))) == []


def test_complete_journal_selection_mismatch_fails_closed():
    journal = {
        "state": "complete",
        "selected": _selected(("SEND-1", "a@example.com")),
        "results": [{"email": "a@example.com", "provider_status": "RECEIVING"}],
    }
    with pytest.raises(RuntimeError, match="does not match"):
        journal_replay_items(journal, _selected(("SEND-2", "b@example.com")))


def test_selects_only_fresh_pending_rows():
    assert _eligible_pending({
        "Send_State": "PENDING_OUTSCRAPER_VALIDATION",
        "Outscraper_Status": "",
        "Outscraper_Evidence": "",
    }, False)
    assert not _eligible_pending({
        "Send_State": "READY_VERIFIED_OUTSCRAPER",
        "Outscraper_Status": "RECEIVING",
        "Outscraper_Evidence": "evidence",
    }, False)
    assert not _eligible_pending({
        "Send_State": "PENDING_OUTSCRAPER_VALIDATION",
        "Outscraper_Status": "UNKNOWN",
        "Outscraper_Evidence": "evidence",
    }, False)


def test_network_failed_requires_explicit_retry_mode():
    row = {
        "Send_State": "HOLD_OUTSCRAPER_NETWORK",
        "Outscraper_Status": "NETWORK_FAILED",
        "Outscraper_Evidence": "",
    }
    assert not _eligible_pending(row, False)
    assert _eligible_pending(row, True)


def test_preflight_accepts_dynamic_queue_length():
    _preflight_queue([
        {"Queue_ID": "SEND-1", "Email": "one@example.test"},
        {"Queue_ID": "SEND-2", "Email": "two@example.test"},
    ])
    _preflight_queue([
        {"Queue_ID": "SEND-1", "Email": "one@example.test"},
        {"Queue_ID": "SEND-2", "Email": "two@example.test"},
        {"Queue_ID": "SEND-3", "Email": "three@example.test"},
    ])


def test_preflight_rejects_duplicate_immutable_identity():
    with pytest.raises(SystemExit):
        _preflight_queue([
            {"Queue_ID": "SEND-1", "Email": "one@example.test"},
            {"Queue_ID": "SEND-1", "Email": "two@example.test"},
        ])
    with pytest.raises(SystemExit):
        _preflight_queue([
            {"Queue_ID": "SEND-1", "Email": "same@example.test"},
            {"Queue_ID": "SEND-2", "Email": "same@example.test"},
        ])
