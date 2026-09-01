import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from runtime.run_outscraper_monitored import (
    EXPECTED_REGA_UNIVERSE,
    REGA_BASELINE_COMPLETE,
    assert_no_ambiguous_validation,
    checkpoint_validation_inflight,
    mailbox_route_kind,
    monitor_paths,
    normalize_receiving_states,
    read_csv_rows,
    write_consolidated,
)


class OutscraperMonitorTests(unittest.TestCase):
    def test_route_classification_prefers_recruitment_and_excludes_support(self):
        self.assertEqual(mailbox_route_kind("hr@example.com"), "recruitment")
        self.assertEqual(mailbox_route_kind("careers@example.com"), "recruitment")
        self.assertEqual(mailbox_route_kind("info@example.com"), "general")
        self.assertEqual(mailbox_route_kind("support@example.com"), "excluded")
        self.assertEqual(mailbox_route_kind("person.name@example.com"), "other")

    def test_old_catch_all_hold_is_normalized_only_for_receiving(self):
        rows = [
            {"Outscraper_Status": "RECEIVING", "Send_State": "HOLD_OUTSCRAPER_CATCH_ALL"},
            {"Outscraper_Status": "INVALID", "Send_State": "REJECTED_OUTSCRAPER_INVALID"},
            {"Outscraper_Status": "RECEIVING", "Send_State": "HOLD_OUTSCRAPER_IDENTITY"},
        ]
        rows[0].update({"Queue_ID": "q1", "Email": "a@example.com"})
        rows[1].update({"Queue_ID": "q2", "Email": "b@example.com"})
        rows[2].update({"Queue_ID": "q3", "Email": "c@example.com"})
        with patch("runtime.run_outscraper_monitored.write_state_updates", return_value=1) as write:
            written = normalize_receiving_states("token", rows)
        self.assertEqual(written, 1)
        self.assertEqual(write.call_args.args[1], [("q1", "a@example.com", "HOLD_OUTSCRAPER_IDENTITY")])

    def test_monitor_paths_are_restart_safe_fixed_names(self):
        with TemporaryDirectory() as td:
            paths = monitor_paths(Path(td))
            self.assertEqual(paths["status"].name, "status.json")
            self.assertEqual(paths["progress"].name, "progress.jsonl")
            self.assertEqual(paths["final"].name, "final-summary.json")

    def test_rega_universe_invariant(self):
        self.assertEqual(728 + REGA_BASELINE_COMPLETE, EXPECTED_REGA_UNIVERSE)
        self.assertEqual(EXPECTED_REGA_UNIVERSE, 757)

    def test_consolidated_checkpoint_preserves_duplicate_license_numbers(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "consolidated.csv"
            rows = {
                "1": {"company_id": "1", "License No": "100", "English Name": "Alpha"},
                "2": {"company_id": "2", "License No": "100", "English Name": "Beta"},
            }
            write_consolidated(path, ["company_id", "License No", "English Name"], rows, ["1", "2"])
            _, written = read_csv_rows(path)
        self.assertEqual(len(written), 2)
        self.assertEqual([row["company_id"] for row in written], ["1", "2"])
        self.assertEqual([row["License No"] for row in written], ["100", "100"])

    def test_validation_inflight_checkpoint_blocks_automatic_repeat(self):
        with TemporaryDirectory() as td:
            paths = monitor_paths(Path(td))
            validated = {}
            checkpoint_validation_inflight(paths, validated, ["a@example.com"])
            self.assertEqual(validated["a@example.com"]["checkpoint_state"], "inflight")
            with self.assertRaisesRegex(RuntimeError, "ambiguous prior Outscraper email validation"):
                assert_no_ambiguous_validation(validated, ["a@example.com"])

    def test_complete_validation_checkpoint_is_not_ambiguous(self):
        validated = {"a@example.com": {"email": "a@example.com", "checkpoint_state": "complete"}}
        assert_no_ambiguous_validation(validated, ["a@example.com"])


if __name__ == "__main__":
    unittest.main()
