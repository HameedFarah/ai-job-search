import inspect
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from career_engine.pipeline import prepare
from tools import career_dashboard_assistant as assistant


class DashboardRebuildDocumentsStatusTest(unittest.TestCase):
    def test_rebuild_emits_actual_package_phases_and_omits_unsupported_eta(self):
        progress = []

        def generate(**kwargs):
            kwargs["progress_callback"]("generating")
            kwargs["progress_callback"]("validating")
            kwargs["progress_callback"]("rendering")
            return "generated_and_rendered"

        def refresh(repo, website_root, progress_callback=None):
            progress_callback("refreshing_metadata")
            progress_callback("publishing")

        with mock.patch.object(assistant, "_ensure_rebuild_generation_packet"), mock.patch.object(
            assistant, "_generate_application_package", side_effect=generate
        ), mock.patch.object(assistant, "_refresh_dashboard_site", side_effect=refresh):
            assistant.run_rebuild_documents(
                repo=Path("/tmp/rebuild-test"),
                dispatcher=Path("/tmp/dispatcher.py"),
                website_root=Path("/tmp/site"),
                job_id="abcdef1234567890",
                progress_callback=progress.append,
            )

        assert [item["phase"] for item in progress] == [
            "queued",
            "preparing",
            "generating",
            "validating",
            "rendering",
            "refreshing_metadata",
            "publishing",
            "complete",
        ]
        assert all(item["kind"] == "package_progress" for item in progress)
        assert all("label" in item and "percent" in item and "elapsed_seconds" in item for item in progress)
        assert all("eta_seconds" not in item for item in progress)
        assert progress[-1]["percent"] == 100

    def test_rebuild_uses_median_eta_only_with_enough_successful_history(self):
        now = datetime.now(timezone.utc)
        history = []
        for index, seconds in enumerate((100, 200, 300)):
            created = now - timedelta(hours=index + 1)
            history.append({
                "id": f"old-{index}",
                "createdAt": created.isoformat(),
                "updatedAt": (created + timedelta(seconds=seconds)).isoformat(),
                "data": {"request_type": "rebuild_documents", "state": "done"},
            })
        progress = []

        with mock.patch.object(assistant, "_ensure_rebuild_generation_packet"), mock.patch.object(
            assistant,
            "_generate_application_package",
            side_effect=lambda **kwargs: kwargs["progress_callback"]("rendering") or "rendered_existing",
        ), mock.patch.object(
            assistant, "_refresh_dashboard_site", side_effect=lambda repo, website_root, progress_callback=None: (
                progress_callback("refreshing_metadata"), progress_callback("publishing")
            )
        ):
            assistant.run_rebuild_documents(
                repo=Path("/tmp/rebuild-test"),
                dispatcher=Path("/tmp/dispatcher.py"),
                website_root=Path("/tmp/site"),
                job_id="abcdef1234567890",
                progress_callback=progress.append,
                historical_requests=history,
            )

        assert all("eta_seconds" in item for item in progress)
        assert progress[0]["eta_seconds"] >= 0

    def test_existing_package_skips_generation_and_completes_only_after_publish(self):
        progress = []
        published = []

        def generate(**kwargs):
            kwargs["progress_callback"]("rendering")
            return "rendered_existing"

        def refresh(repo, website_root, progress_callback=None):
            progress_callback("refreshing_metadata")
            published.append(True)
            progress_callback("publishing")

        with mock.patch.object(assistant, "_ensure_rebuild_generation_packet"), mock.patch.object(
            assistant, "_generate_application_package", side_effect=generate
        ), mock.patch.object(assistant, "_refresh_dashboard_site", side_effect=refresh):
            assistant.run_rebuild_documents(
                repo=Path("/tmp/rebuild-test"),
                dispatcher=Path("/tmp/dispatcher.py"),
                website_root=Path("/tmp/site"),
                job_id="abcdef1234567890",
                progress_callback=progress.append,
            )

        assert "generating" not in [item["phase"] for item in progress]
        assert "validating" not in [item["phase"] for item in progress]
        assert published == [True]
        assert progress[-1]["phase"] == "complete"

    def test_publish_failure_never_emits_terminal_complete(self):
        progress = []

        with mock.patch.object(assistant, "_ensure_rebuild_generation_packet"), mock.patch.object(
            assistant,
            "_generate_application_package",
            side_effect=lambda **kwargs: kwargs["progress_callback"]("rendering") or "rendered_existing",
        ), mock.patch.object(
            assistant,
            "_refresh_dashboard_site",
            side_effect=assistant.AssistantError("publish failed: here.now unavailable"),
        ):
            with self.assertRaises(assistant.AssistantError):
                assistant.run_rebuild_documents(
                    repo=Path("/tmp/rebuild-test"),
                    dispatcher=Path("/tmp/dispatcher.py"),
                    website_root=Path("/tmp/site"),
                    job_id="abcdef1234567890",
                    progress_callback=progress.append,
                )

        self.assertNotIn("complete", [item["phase"] for item in progress])

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

    def test_selected_resume_generation_request_uses_live_ai_requests_schema(self):
        path = Path("dashboard/career-review/site/assets/external-links.js")
        text = path.read_text(encoding="utf-8")
        start = text.index("async function ownerQueuePackageGeneration")
        end = text.index("const ownerBaseRenderOverlayTemplate", start)
        block = text[start:end]
        self.assertIn("request_type: 'edit_cv'", block)
        self.assertIn("prompt,", block)
        self.assertIn("state: 'pending'", block)
        self.assertNotIn("template_id:", block)
        self.assertIn("(${templateId})", block)
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
            side_effect=lambda repo, website_root, progress_callback=None: published.append((repo, website_root)),
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

    def test_progress_telemetry_patch_failure_does_not_abort_and_final_answer_wins(self):
        request = {"id": "request-1", "data": {"role_key": "tracker-1", "state": "pending"}}
        patches = []

        def patch_request(**kwargs):
            fields = kwargs["fields"]
            patches.append(fields)
            if fields.get("answer", "").startswith('{"kind":'):
                raise assistant.AssistantError("telemetry PATCH failed")

        def answer_request(**kwargs):
            kwargs["progress_callback"]({"kind": "package_progress", "phase": "generating"})
            return "tracker-1", "authoritative final answer", {
                "validation_status": "success",
                "owner_input_needed": False,
            }

        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            assistant, "archive_confirmed_submissions", return_value={"archived": 0, "existing": 0, "unresolved": []}
        ), mock.patch.object(
            assistant, "ai_request_records", return_value=[]
        ), mock.patch.object(
            assistant, "pending_requests", return_value=[request]
        ), mock.patch.object(
            assistant, "patch_request", side_effect=patch_request
        ), mock.patch.object(
            assistant, "answer_request", side_effect=answer_request
        ), mock.patch.object(
            assistant, "create_response_comment"
        ):
            result = assistant.process_once(
                repo=Path(temp_dir),
                dispatcher=Path(temp_dir) / "dispatcher.py",
                website_root=Path(temp_dir) / "site",
                slug="test-site",
                api_key="test-key",
                limit=1,
            )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(patches[-1]["state"], "done")
        self.assertEqual(patches[-1]["answer"], "authoritative final answer")


if __name__ == "__main__":
    unittest.main()
