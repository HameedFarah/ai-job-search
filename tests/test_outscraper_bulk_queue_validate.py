from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from runtime.outscraper_bulk_queue_validate import atomic_json, journal_replay_items, load_journal


def test_journal_atomic_roundtrip():
    with TemporaryDirectory() as td:
        path = Path(td) / 'journal.json'
        atomic_json(path, {'state': 'complete', 'results': [{'email': 'a@example.com'}]})
        assert load_journal(path)['state'] == 'complete'


def test_inflight_journal_fails_closed_without_replay():
    with pytest.raises(RuntimeError, match='ambiguous'):
        journal_replay_items({'state': 'inflight'}, {'a@example.com'})


def test_complete_journal_replays_matching_subset():
    journal = {
        'state': 'complete',
        'results': [
            {'email': 'a@example.com', 'provider_status': 'RECEIVING'},
            {'email': 'b@example.com', 'provider_status': 'INVALID'},
        ],
    }
    assert journal_replay_items(journal, {'b@example.com'}) == [
        {'email': 'b@example.com', 'provider_status': 'INVALID'}
    ]


def test_applied_journal_allows_new_call_cycle():
    assert journal_replay_items({'state': 'applied'}, {'c@example.com'}) == []


def test_complete_journal_mismatch_fails_closed():
    with pytest.raises(RuntimeError, match='does not match'):
        journal_replay_items({'state': 'complete', 'results': [{'email': 'a@example.com'}]}, {'b@example.com'})
