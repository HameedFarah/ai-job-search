import urllib.error
from unittest.mock import MagicMock, patch

from runtime.prepare_outscraper_queue import _header_recipients, gmail_json, identity_eligible, priority_for


def test_engineering_source_receiving_identity_is_eligible():
    row = {
        'Email': 'info@example.com',
        'Source_Dataset': 'Engineering offices.xlsx / Sheet1',
        'Source_Verification': 'SOURCE_LISTED_ONLY',
        'Source_Record_ID': '123',
    }
    assert identity_eligible(row, set()) == (True, 'engineering_source_record_plus_current_receiving')
    assert priority_for(row) == (3, 'engineering_source_general')


def test_excluded_mailbox_is_not_eligible():
    row = {
        'Email': 'support@example.com',
        'Source_Dataset': 'Engineering offices.xlsx / Sheet1',
        'Source_Verification': 'SOURCE_LISTED_ONLY',
        'Source_Record_ID': '123',
    }
    assert identity_eligible(row, set()) == (False, 'excluded_mailbox_localpart')


def test_unverified_rega_deduped_is_held():
    row = {
        'Email': 'careers@example.com',
        'Source_Dataset': 'REGA deduped email queue / All_New_Emails',
        'Source_Verification': 'REGA_DEDUPE_QUEUE_2026-08-29',
        'Source_Record_ID': '1',
    }
    assert identity_eligible(row, set()) == (False, 'identity_not_confirmed')


def test_gmail_json_retries_transient_reset():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b'{"ok":true}'
    with patch(
        'runtime.prepare_outscraper_queue.urlopen',
        side_effect=[urllib.error.URLError(ConnectionResetError(104, 'reset')), response],
    ) as urlopen, patch('runtime.prepare_outscraper_queue.time.sleep') as sleep:
        result = gmail_json('token', 'https://example.test')
    assert result == {'ok': True}
    assert urlopen.call_count == 2
    sleep.assert_called_once()


def test_header_recipients_covers_to_cc_and_bcc():
    headers = [
        {'name': 'To', 'value': 'Hiring <jobs@example.com>, Other <other@example.com>'},
        {'name': 'Cc', 'value': 'HR <hr@example.com>'},
        {'name': 'Bcc', 'value': 'hidden@example.com'},
        {'name': 'Subject', 'value': 'ignored'},
    ]
    assert _header_recipients(headers) == {
        'jobs@example.com', 'other@example.com', 'hr@example.com', 'hidden@example.com'
    }
