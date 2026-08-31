import csv
import tempfile
import unittest
from pathlib import Path

from runtime.validate_outreach_emails import normalise_email, read_rows, result_status


class ValidateOutreachEmailsRunnerTests(unittest.TestCase):
    def test_normalise_email(self):
        self.assertEqual(normalise_email("  User@Example.COM "), "user@example.com")
        self.assertEqual(normalise_email(None), "")

    def test_read_rows_deduplicates_case_insensitively(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Queue_ID", "Email"])
                writer.writeheader()
                writer.writerow({"Queue_ID": "1", "Email": "A@example.com"})
                writer.writerow({"Queue_ID": "2", "Email": "a@example.com"})
                writer.writerow({"Queue_ID": "3", "Email": "b@example.com"})
            rows, emails = read_rows(path, "Email")
        self.assertEqual(len(rows), 3)
        self.assertEqual(emails, ["a@example.com", "b@example.com"])

    def test_result_status_is_fail_closed_for_missing_status(self):
        self.assertEqual(result_status({"status": "RECEIVING"}), "RECEIVING")
        self.assertEqual(result_status({"status": "invalid"}), "INVALID")
        self.assertEqual(result_status({}), "PROVIDER_FAILED")


if __name__ == "__main__":
    unittest.main()
