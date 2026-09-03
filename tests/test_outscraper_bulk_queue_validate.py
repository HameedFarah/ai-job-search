from __future__ import annotations

import unittest

from runtime.outscraper_bulk_queue_validate import _eligible_pending, _preflight_queue


class OutscraperLiveQueueSelectionTests(unittest.TestCase):
    def test_selects_only_fresh_pending_rows(self) -> None:
        self.assertTrue(_eligible_pending({
            "Send_State": "PENDING_OUTSCRAPER_VALIDATION",
            "Outscraper_Status": "",
            "Outscraper_Evidence": "",
        }, False))
        self.assertFalse(_eligible_pending({
            "Send_State": "READY_VERIFIED_OUTSCRAPER",
            "Outscraper_Status": "RECEIVING",
            "Outscraper_Evidence": "evidence",
        }, False))
        self.assertFalse(_eligible_pending({
            "Send_State": "PENDING_OUTSCRAPER_VALIDATION",
            "Outscraper_Status": "UNKNOWN",
            "Outscraper_Evidence": "evidence",
        }, False))

    def test_network_failed_requires_explicit_retry_mode(self) -> None:
        row = {
            "Send_State": "HOLD_OUTSCRAPER_NETWORK",
            "Outscraper_Status": "NETWORK_FAILED",
            "Outscraper_Evidence": "",
        }
        self.assertFalse(_eligible_pending(row, False))
        self.assertTrue(_eligible_pending(row, True))

    def test_preflight_accepts_dynamic_queue_length(self) -> None:
        _preflight_queue([
            {"Queue_ID": "SEND-1", "Email": "one@example.test"},
            {"Queue_ID": "SEND-2", "Email": "two@example.test"},
        ])
        _preflight_queue([
            {"Queue_ID": "SEND-1", "Email": "one@example.test"},
            {"Queue_ID": "SEND-2", "Email": "two@example.test"},
            {"Queue_ID": "SEND-3", "Email": "three@example.test"},
        ])

    def test_preflight_rejects_duplicate_immutable_identity(self) -> None:
        with self.assertRaises(SystemExit):
            _preflight_queue([
                {"Queue_ID": "SEND-1", "Email": "one@example.test"},
                {"Queue_ID": "SEND-1", "Email": "two@example.test"},
            ])
        with self.assertRaises(SystemExit):
            _preflight_queue([
                {"Queue_ID": "SEND-1", "Email": "same@example.test"},
                {"Queue_ID": "SEND-2", "Email": "same@example.test"},
            ])


if __name__ == "__main__":
    unittest.main()
