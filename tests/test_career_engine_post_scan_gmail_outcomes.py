from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from career_engine.post_scan import reconcile_after_scan


class PostScanGmailOutcomeTests(unittest.TestCase):
    def test_reconciliation_order_and_report_keys(self):
        calls: list[str] = []

        def submission(root):
            calls.append("submission")
            return {"messages_scanned": 2, "send_or_submit": False}

        def outcomes(root):
            calls.append("outcomes")
            return {"messages_scanned": 3, "application_states_changed": 1, "send_or_submit": False}

        def irrelevant(root):
            calls.append("irrelevant")
            return {"changed_count": 0, "send_or_submit": False}

        def calibration(root, report):
            calls.append("calibration")
            return {"changed_count": 0, "send_or_submit": False}

        report = {"send_or_submit": False}
        with patch("career_engine.post_scan.reconcile_submission_mail", side_effect=submission), \
             patch("career_engine.post_scan.reconcile_outcome_mail", side_effect=outcomes), \
             patch("career_engine.post_scan.reconcile_irrelevant_feedback", side_effect=irrelevant), \
             patch("career_engine.post_scan.apply_owner_feedback_calibration", side_effect=calibration):
            result = reconcile_after_scan(Path("."), report)

        self.assertIs(result, report)
        self.assertEqual(calls, ["submission", "outcomes", "irrelevant", "calibration"])
        self.assertEqual(result["gmail_outcome_reconciliation"]["application_states_changed"], 1)
        self.assertFalse(result["gmail_outcome_reconciliation"]["send_or_submit"])

    def test_outcome_failure_is_surfaced_and_does_not_abort_scan(self):
        report = {"send_or_submit": False}
        with patch("career_engine.post_scan.reconcile_submission_mail", return_value={"send_or_submit": False}), \
             patch("career_engine.post_scan.reconcile_outcome_mail", side_effect=RuntimeError("gmail unavailable")), \
             patch("career_engine.post_scan.reconcile_irrelevant_feedback", return_value={"send_or_submit": False}), \
             patch("career_engine.post_scan.apply_owner_feedback_calibration", return_value={"send_or_submit": False}):
            result = reconcile_after_scan(Path("."), report)

        outcome = result["gmail_outcome_reconciliation"]
        self.assertEqual(outcome["step"], "gmail_outcome_reconciliation")
        self.assertIn("gmail unavailable", outcome["error"])
        self.assertFalse(outcome["send_or_submit"])


if __name__ == "__main__":
    unittest.main()
