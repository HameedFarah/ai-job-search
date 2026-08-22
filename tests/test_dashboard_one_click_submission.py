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

    def test_undo_retraction_keeps_extra_evidence_inside_schema_safe_note(self):
        source = (ROOT / "dashboard/career-review/site/assets/app.js").read_text(encoding="utf-8")
        block = source.split("async function undoAppliedMark", 1)[1].split("function showAppliedSuccess", 1)[0]
        self.assertNotIn("\n    retracted_event_id:", block)
        self.assertNotIn("\n    retracted_at:", block)
        self.assertIn("note: JSON.stringify({", block)
        self.assertIn("retracted_event_id: confirmation?.record?.id", block)
        self.assertIn("retracted_at: retractedAt", block)

    def test_add_job_request_uses_only_known_ai_request_fields(self):
        source = (ROOT / "dashboard/career-review/site/assets/add-job.js").read_text(encoding="utf-8")
        request_block = source.split("const record = await createRecord('ai_requests'", 1)[1].split("dialog.close()", 1)[0]
        self.assertIn("role_key: ADD_JOB_ROLE_KEY", request_block)
        self.assertIn("request_type: 'add_job'", request_block)
        self.assertIn("prompt: requestPrompt", request_block)
        self.assertIn("state: 'pending'", request_block)
        self.assertIn("requestPrompt.length > ADD_JOB_PROMPT_MAX_LENGTH", source)
        self.assertIn("const ADD_JOB_PROMPT_MAX_LENGTH = 8000", source)
        for field in ("job_url", "job_description", "company", "role", "location"):
            self.assertNotIn(f"{field}:", request_block)

    def test_add_job_opens_resumable_processing_detail_until_package_is_ready(self):
        source = (ROOT / "dashboard/career-review/site/assets/add-job.js").read_text(encoding="utf-8")
        self.assertIn("ADD_JOB_REQUEST_PARAM = 'add_job_request'", source)
        self.assertIn("renderAddJobProcessingOverlay", source)
        self.assertIn("restoreAddJobRequest", source)
        self.assertIn("window.setInterval(pollAddJobRequest, 2500)", source)
        self.assertIn("activeAddJobJobKey = `tracker-${progress.job_id}`", source)
        self.assertIn("url.searchParams.set('job', jobKey)", source)
        self.assertIn("window.location.replace", source)
        for phase in ("reading", "scoring", "generating", "publishing"):
            self.assertIn(phase, source)
        css = (ROOT / "dashboard/career-review/site/assets/add-job.css").read_text(encoding="utf-8")
        self.assertIn(".add-job-processing-detail", css)
        self.assertIn(".add-job-progress-step", css)
        self.assertIn("@keyframes add-job-spin", css)

    def test_private_worker_proxies_only_known_site_data_collections_for_owner_or_access_service(self):
        source = (ROOT / "dashboard/career-review/worker.js").read_text(encoding="utf-8")
        self.assertIn("/.herenow/data/", source)
        self.assertIn("HERENOW_API_KEY", source)
        self.assertIn("cf-access-authenticated-user-email", source)
        self.assertIn("hameedo@gmail.com", source)
        self.assertIn("ctx.access.getIdentity()", source)
        self.assertIn("service_token_status === true", source)
        self.assertIn("service_token_id", source)
        self.assertIn("Owner or approved Cloudflare Access service token required", source)
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
