import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from runtime.outscraper_sheet_runner import (
    EXPECTED_HEADERS,
    MAX_WRITE_ROWS,
    _sheet_values,
    classify,
    ensure_queue_metadata,
    read_queue,
    rclone_access_token,
    send_state,
    sheets_request,
    write_campaign_updates,
    write_state_updates,
    write_updates,
)


class SheetRunnerTests(unittest.TestCase):
    def test_rclone_token_is_parsed_without_output(self):
        with patch("runtime.outscraper_sheet_runner.subprocess.run") as refresh:
            token = rclone_access_token(runner=lambda *args, **kwargs: json.dumps({"gdrive": {"token": json.dumps({"access_token": "secret"})}}).encode())
            self.assertEqual(token, "secret")
            refresh.assert_called_once()

    def test_classify_preserves_fail_closed_statuses(self):
        result = classify([{"status": "RECEIVING", "metadata": {"email": "A@EXAMPLE.COM"}}, {"status": "BLACKLISTED", "metadata": {"email": "b@example.com"}}])
        self.assertEqual(result, {"a@example.com": "RECEIVING", "b@example.com": "BLACKLISTED"})

    def test_sheets_request_retries_transient_network_error(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true}'
        with patch(
            "runtime.outscraper_sheet_runner.urllib.request.urlopen",
            side_effect=[urllib.error.URLError("transient"), response],
        ) as urlopen, patch("runtime.outscraper_sheet_runner.time.sleep") as sleep:
            result = sheets_request("token", "GET", "https://example.test")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_write_batch_is_bounded_and_uses_metadata_filter(self):
        with patch("runtime.outscraper_sheet_runner._verified_metadata_ids", return_value={"q1": 91}) as verify, \
             patch("runtime.outscraper_sheet_runner._metadata_write") as write:
            values = _sheet_values({"provider_status": "INVALID", "verification": "INVALID"})
            self.assertEqual(write_updates("token", [("q1", "a@example.com", values)]), 1)
            verify.assert_called_once()
            data = write.call_args.args[1]
            self.assertEqual(data[0]["dataFilter"], {"developerMetadataLookup": {"metadataId": 91}})
            row = data[0]["values"][0]
            self.assertEqual(len(row), len(EXPECTED_HEADERS))
            self.assertEqual(row[14], "REJECTED_OUTSCRAPER_INVALID")
            self.assertEqual(row[18], "INVALID")
            self.assertIsNone(row[0])
        self.assertEqual(MAX_WRITE_ROWS, 25)
        with self.assertRaises(RuntimeError):
            write_updates("token", [(f"q{n}", f"{n}@example.com", {}) for n in range(26)])

    def test_state_write_uses_metadata_filter_and_null_placeholders(self):
        with patch("runtime.outscraper_sheet_runner._verified_metadata_ids", return_value={"q1": 77}), \
             patch("runtime.outscraper_sheet_runner._metadata_write") as write:
            self.assertEqual(write_state_updates("token", [("q1", "a@example.com", "QUEUED")]), 1)
            data = write.call_args.args[1]
            self.assertEqual(data[0]["dataFilter"], {"developerMetadataLookup": {"metadataId": 77}})
            row = data[0]["values"][0]
            self.assertEqual(row[14], "QUEUED")
            self.assertTrue(all(value is None for i, value in enumerate(row) if i != 14))

    def test_campaign_write_tracks_draft_and_sent_provenance_by_metadata(self):
        with patch("runtime.outscraper_sheet_runner._verified_metadata_ids", return_value={"q1": 88}), \
             patch("runtime.outscraper_sheet_runner._metadata_write") as write:
            values = {
                "Gmail_Draft_ID": "draft-1",
                "Gmail_Message_ID": "message-1",
                "Send_State": "DRAFT_VERIFIED_READY_TO_SEND",
                "Sent_Message_ID": "sent-1",
                "Terminal_Outcome": "SENT",
            }
            self.assertEqual(write_campaign_updates("token", [("q1", "a@example.com", values)]), 1)
            data = write.call_args.args[1]
            self.assertEqual(data[0]["dataFilter"], {"developerMetadataLookup": {"metadataId": 88}})
            row = data[0]["values"][0]
            self.assertEqual((row[8], row[9], row[14], row[15], row[16]), ("draft-1", "message-1", "DRAFT_VERIFIED_READY_TO_SEND", "sent-1", "SENT"))
            self.assertTrue(all(value is None for i, value in enumerate(row) if i not in {8, 9, 14, 15, 16}))
        with self.assertRaises(RuntimeError):
            write_campaign_updates("token", [("q1", "a@example.com", {"Notes": "unsupported"})])

    def test_read_queue_pads_only_omitted_trailing_blanks(self):
        with patch("runtime.outscraper_sheet_runner.sheets_request", return_value={"values": [list(EXPECTED_HEADERS), ["q1", "A@Example.com"]]}):
            rows = read_queue("token")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Queue_ID"], "q1")
        self.assertEqual(rows[0]["Email"], "A@Example.com")
        self.assertEqual(rows[0]["Outscraper_Checked_At"], "")

    def test_read_queue_rejects_oversized_or_duplicate_identity_rows(self):
        oversized = list(EXPECTED_HEADERS) + ["extra"]
        with patch("runtime.outscraper_sheet_runner.sheets_request", return_value={"values": [list(EXPECTED_HEADERS), oversized]}):
            with self.assertRaises(RuntimeError):
                read_queue("token")
        with patch("runtime.outscraper_sheet_runner.sheets_request", return_value={"values": [list(EXPECTED_HEADERS), ["q1", "a@example.com"], ["q1", "b@example.com"]]}):
            with self.assertRaises(RuntimeError):
                read_queue("token")

    def test_existing_row_metadata_is_verified_against_current_queue_position(self):
        rows = [{"Queue_ID": "q1", "Email": "a@example.com"}]
        metadata = [{
            "metadataId": 99,
            "metadataValue": "q1",
            "location": {"dimensionRange": {"sheetId": 123, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}},
        }]
        with patch("runtime.outscraper_sheet_runner.read_queue", side_effect=[rows, rows]), \
             patch("runtime.outscraper_sheet_runner._sheet_id", return_value=123), \
             patch("runtime.outscraper_sheet_runner._queue_metadata", return_value=metadata):
            self.assertEqual(ensure_queue_metadata("token"), {"q1": 99})

    def test_missing_row_metadata_is_created_then_verified(self):
        rows = [{"Queue_ID": "q1", "Email": "a@example.com"}]
        metadata = [{
            "metadataId": 101,
            "metadataValue": "q1",
            "location": {"dimensionRange": {"sheetId": 123, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}},
        }]
        with patch("runtime.outscraper_sheet_runner.read_queue", side_effect=[rows, rows]), \
             patch("runtime.outscraper_sheet_runner._sheet_id", return_value=123), \
             patch("runtime.outscraper_sheet_runner._queue_metadata", side_effect=[[], metadata]), \
             patch("runtime.outscraper_sheet_runner.sheets_request", return_value={}) as request:
            self.assertEqual(ensure_queue_metadata("token"), {"q1": 101})
        url = request.call_args.args[2]
        payload = request.call_args.args[3]
        self.assertTrue(url.endswith(":batchUpdate"))
        created = payload["requests"][0]["createDeveloperMetadata"]["developerMetadata"]
        self.assertEqual(created["metadataValue"], "q1")
        self.assertEqual(created["location"]["dimensionRange"]["startIndex"], 1)

    def test_fail_closed_states(self):
        self.assertEqual(send_state("INVALID", "x"), "REJECTED_OUTSCRAPER_INVALID")
        self.assertEqual(send_state("BLACKLISTED", "x"), "REJECTED_OUTSCRAPER_BLACKLISTED")
        self.assertEqual(send_state("UNKNOWN", "x"), "HOLD_OUTSCRAPER_UNKNOWN")
        self.assertEqual(send_state("RECEIVING", "Catch All"), "HOLD_OUTSCRAPER_IDENTITY")
        self.assertEqual(send_state("RECEIVING", "RECEIVING", "Catch All"), "HOLD_OUTSCRAPER_IDENTITY")
        self.assertEqual(send_state("RECEIVING", "Valid"), "HOLD_OUTSCRAPER_IDENTITY")

    def test_jsonl_mapping_is_exact_and_sanitized(self):
        values = _sheet_values({"provider_status": "INVALID", "verification": "invalid", "status_details": "bad",
                                 "provider": "outscraper", "source_url": "https://example.test", "safe_to_send": False,
                                 "replacement_email": "evil@example.test", "checked_at": "2026-09-01T00:00:00Z"})
        self.assertEqual(values["Send_State"], "REJECTED_OUTSCRAPER_INVALID")
        self.assertEqual(values["Outscraper_Replacement_Email"], "")
        self.assertEqual(json.loads(values["Outscraper_Evidence"]), {"provider": "outscraper", "safe_to_send": False,
                                                                       "source_url": "https://example.test", "status_details": "bad"})


if __name__ == "__main__":
    unittest.main()
