import inspect
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from career_engine.pipeline import prepare
from tools import career_dashboard_assistant as assistant


class DashboardRebuildDocumentsStatusTest(unittest.TestCase):
    def test_status_action_is_not_a_lifecycle_stage(self):
        path = Path("dashboard/career-review/site/assets/bulk-table.js")
        text = path.read_text(encoding="utf-8")
        self.assertIn("↻ Rebuild CV & cover letter", text)
        self.assertIn("request_type: 'rebuild_documents'", text)
        self.assertIn("[REBUILD_DOCUMENTS]", text)
        self.assertNotIn("stage: REBUILD_DOCUMENTS_ACTION", text)
        if shutil.which("node"):
            result = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_prepare_has_explicit_owner_review_route_override(self):
        self.assertIn("allow_unresolved_route_for_owner_review", inspect.signature(prepare).parameters)

    def test_rebuild_request_uses_dedicated_backend_before_context_load(self):
        calls = []
        with mock.patch.object(
            assistant,
            "run_rebuild_documents",
            side_effect=lambda **kwargs: calls.append(kwargs) or "rebuilt",
        ), mock.patch.object(
            assistant,
            "load_job_context",
            side_effect=AssertionError("must not load before rebuild preparation"),
        ):
            role_key, answer, metadata = assistant.answer_request(
                repo=Path("/tmp/rebuild-test"),
                dispatcher=Path("/tmp/dispatcher.py"),
                website_root=Path("/tmp/site"),
                record={
                    "data": {
                        "role_key": "tracker-abcdef1234567890",
                        "request_type": "rebuild_documents",
                        "prompt": "",
                    }
                },
            )
        self.assertEqual(role_key, "tracker-abcdef1234567890")
        self.assertEqual(answer, "rebuilt")
        self.assertEqual(calls[0]["job_id"], "abcdef1234567890")
        self.assertEqual(metadata["validation_status"], "success")

    def test_missing_packet_reprepares_as_explicit_owner_review(self):
        class Tracker:
            def get_job(self, job_id):
                self.job_id = job_id
                return {"job": {"company": "Example", "role": "Design Manager"}}

        tracker = Tracker()
        prepared = {
            "outputs": {"generation_packet": "/tmp/generation_packet.json"},
            "blockers": [],
        }
        refreshed_context = {
            "packet": {"job_id": "abcdef1234567890"},
            "application": {},
            "artifact_dir": Path("/tmp/artifacts"),
        }
        with mock.patch.object(
            assistant,
            "load_job_context",
            side_effect=[assistant.AssistantError("generation_packet_missing"), refreshed_context],
        ), mock.patch(
            "career_engine.ops._load_tracker_ops",
            return_value=tracker,
        ), mock.patch(
            "career_engine.ops._payload_from_record",
            return_value={"source": "scanner", "role": "Design Manager"},
        ), mock.patch(
            "career_engine.pipeline.prepare",
            return_value=prepared,
        ) as prepare_mock:
            result = assistant._ensure_rebuild_generation_packet(
                repo=Path("/tmp/repo"),
                job_id="abcdef1234567890",
            )
        self.assertEqual(result, refreshed_context)
        self.assertEqual(tracker.job_id, "abcdef1234567890")
        kwargs = prepare_mock.call_args.kwargs
        self.assertTrue(kwargs["force_weak"])
        self.assertTrue(kwargs["allow_unresolved_route_for_owner_review"])
        self.assertEqual(kwargs["actor"], "owner")

    def test_rebuild_existing_package_rerenders_and_republishes(self):
        generated = []
        published = []
        with mock.patch.object(
            assistant,
            "_ensure_rebuild_generation_packet",
            return_value={"application": {"headline": "Existing"}},
        ), mock.patch.object(
            assistant,
            "_generate_application_package",
            side_effect=lambda **kwargs: generated.append(kwargs) or "rendered_existing",
        ), mock.patch.object(
            assistant,
            "_refresh_dashboard_site",
            side_effect=lambda repo, website_root: published.append((repo, website_root)),
        ):
            answer = assistant.run_rebuild_documents(
                repo=Path("/tmp/rebuild-test"),
                dispatcher=Path("/tmp/dispatcher.py"),
                website_root=Path("/tmp/site"),
                job_id="abcdef1234567890",
            )
        self.assertFalse(generated[0]["force_regenerate"])
        self.assertEqual(published, [(Path("/tmp/rebuild-test"), Path("/tmp/site"))])
        self.assertIn("dashboard was republished", answer)


if __name__ == "__main__":
    unittest.main()
