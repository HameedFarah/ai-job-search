import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DashboardOneClickSubmissionTest(unittest.TestCase):
    def test_job_applied_is_one_click_and_preserves_submission_evidence(self):
        source = (ROOT / "dashboard/career-review/site/assets/shared.js").read_text(encoding="utf-8")
        self.assertNotIn("window.confirm(`Confirm that", source)
        self.assertNotIn("window.prompt('Optional: paste the submission confirmation", source)
        self.assertIn("SUBMISSION_CONFIRMATION_PENDING", source)
        self.assertIn("explicit_owner_confirmation", source)
        self.assertIn("application_submitted", source)
        self.assertIn("email_sent_owner_confirmed", source)
        self.assertIn("submissionDocumentEvidence", source)
        self.assertIn("submissionHistoryFields", source)
        self.assertIn("compactSubmissionNote", source)
        self.assertIn("confirmation_reference: ''", source)
        self.assertIn("to_stage: 'applied'", source)

    def test_undo_retraction_uses_only_live_history_schema_fields(self):
        source = (ROOT / "dashboard/career-review/site/assets/app.js").read_text(encoding="utf-8")
        block = source.split("async function undoAppliedMark", 1)[1].split("function showAppliedSuccess", 1)[0]
        self.assertNotIn("\n    retracted_event_id:", block)
        self.assertNotIn("\n    retracted_at:", block)
        self.assertIn("retracted_event_id: confirmation?.record?.id", block)
        self.assertIn("retracted_at: retractedAt", block)
        schema = json.loads((ROOT / "dashboard/career-review/site/.herenow/data.json").read_text(encoding="utf-8"))
        history_fields = schema["collections"]["history"]["fields"]
        self.assertNotIn("retracted_event_id", history_fields)
        self.assertNotIn("retracted_at", history_fields)

    def test_private_worker_proxies_only_known_site_data_collections_for_owner(self):
        source = (ROOT / "dashboard/career-review/worker.js").read_text(encoding="utf-8")
        self.assertIn("/.herenow/data/", source)
        self.assertIn("HERENOW_API_KEY", source)
        self.assertIn("cf-access-authenticated-user-email", source)
        self.assertIn("hameedo@gmail.com", source)
        self.assertIn("Authorization: `Bearer ${env.HERENOW_API_KEY}`", source)
        for collection in ("workflow", "comments", "history", "ai_requests", "preferences"):
            self.assertIn(f"'{collection}'", source)
        self.assertIn("ALLOWED_METHODS", source)
        self.assertIn("'GET'", source)
        self.assertIn("'POST'", source)
        self.assertIn("'PATCH'", source)
        self.assertIn("'DELETE'", source)
        self.assertIn("env.ASSETS.fetch(request)", source)

    def test_wrangler_runs_worker_only_for_site_data_path(self):
        config = json.loads((ROOT / "dashboard/career-review/wrangler.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(config["main"], "./worker.js")
        self.assertFalse(config["workers_dev"])
        self.assertFalse(config["preview_urls"])
        self.assertEqual(config["assets"]["directory"], "./site")
        self.assertEqual(config["assets"]["binding"], "ASSETS")
        self.assertEqual(config["assets"]["run_worker_first"], ["/.herenow/data/*"])
        self.assertEqual(config["secrets"]["required"], ["HERENOW_API_KEY"])
        self.assertEqual(config["routes"], [{
            "pattern": "career.farahdigital.com/*",
            "zone_name": "farahdigital.com",
        }])


if __name__ == "__main__":
    unittest.main()
