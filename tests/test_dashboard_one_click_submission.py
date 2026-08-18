import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DashboardOneClickSubmissionTest(unittest.TestCase):
    def test_job_applied_is_one_click_and_preserves_submission_evidence(self):
        source = (ROOT / "dashboard/career-review/site/assets/one-click-submission.js").read_text(encoding="utf-8")
        self.assertNotIn("window.confirm(", source)
        self.assertNotIn("window.prompt(", source)
        self.assertIn("explicit_owner_confirmation", source)
        self.assertIn("application_submitted", source)
        self.assertIn("email_sent_owner_confirmed", source)
        self.assertIn("submissionDocumentEvidence", source)
        self.assertIn("submissionHistoryFields", source)
        self.assertIn("compactSubmissionNote", source)
        self.assertIn("to_stage: 'applied'", source)

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

    def test_wrangler_runs_worker_only_for_shell_and_site_data_paths(self):
        config = json.loads((ROOT / "dashboard/career-review/wrangler.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(config["main"], "./worker.js")
        self.assertFalse(config["workers_dev"])
        self.assertFalse(config["preview_urls"])
        self.assertEqual(config["assets"]["directory"], "./site")
        self.assertEqual(config["assets"]["binding"], "ASSETS")
        self.assertEqual(
            set(config["assets"]["run_worker_first"]),
            {"/", "/index.html", "/.herenow/data/*"},
        )
        self.assertEqual(config["secrets"]["required"], ["HERENOW_API_KEY"])
        self.assertEqual(config["routes"], [{
            "pattern": "career.farahdigital.com/*",
            "zone_name": "farahdigital.com",
        }])

    def test_worker_injects_one_click_override_into_dashboard_shell(self):
        source = (ROOT / "dashboard/career-review/worker.js").read_text(encoding="utf-8")
        self.assertIn('/assets/one-click-submission.js', source)
        self.assertIn("HTMLRewriter", source)
        self.assertIn("env.ASSETS.fetch(request)", source)


if __name__ == "__main__":
    unittest.main()
