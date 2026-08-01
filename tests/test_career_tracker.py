import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "projects" / "job-automation" / "tracker.py"
SPEC = importlib.util.spec_from_file_location("career_tracker", MODULE_PATH)
tracker_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(tracker_module)

CareerTracker = tracker_module.CareerTracker
CSV_FIELDS = tracker_module.CSV_FIELDS
EVENT_FIELDS = tracker_module.EVENT_FIELDS


class CareerTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "projects" / "job-automation"
        self.tracker = CareerTracker(self.base)
        self.payload = {
            "source": "workable",
            "external_job_id": "20004876",
            "source_url": "https://example.invalid/jobs/20004876",
            "company": "Qiddiya Investment Company",
            "role": "Manager - Design Governance",
            "location": "Riyadh, Saudi Arabia",
            "posting_date": "2026-07-09",
            "full_job_description": "Implement and maintain design governance frameworks.",
            "normalized_requirements": ["Design governance", "Compliance audits"],
        }

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self):
        return self.tracker.ingest(
            self.payload,
            comment="Verified official employer posting",
            actor="chatgpt",
            source_refs=["official-workable-posting"],
        )

    def test_exact_csv_header_and_lowercase_layout(self):
        self.tracker.ensure_layout()
        with self.tracker.csv_path.open(newline="", encoding="utf-8") as handle:
            self.assertEqual(next(csv.reader(handle)), CSV_FIELDS)
        self.assertEqual(self.tracker.csv_path, self.base / "data" / "jobs.csv")
        self.assertEqual(self.tracker.events_path, self.base / "logs" / "events.jsonl")
        self.assertFalse((self.base / "Data").exists())
        self.assertFalse((self.base / "Logs").exists())
        self.assertFalse((self.base / "Artifacts").exists())

    def test_ingest_creates_csv_json_artifact_and_canonical_event(self):
        result = self.ingest()
        job_id = result["job_id"]
        self.assertEqual(result["result"], "created")
        self.assertTrue((self.base / "data" / "jobs" / f"{job_id}.json").exists())
        self.assertTrue((self.base / "artifacts" / job_id).is_dir())
        record = self.tracker.get_job(job_id)
        for key in (
            "full_job_description", "normalized_requirements", "provenance", "scoring",
            "evidence_matches", "processing_state", "generated_artifacts",
            "gmail_draft_reference", "history",
        ):
            self.assertIn(key, record)
        event = self.tracker.read_events()[0]
        self.assertEqual(list(event.keys()), EVENT_FIELDS)
        self.assertEqual(event["action"], "created")
        self.assertEqual(event["actor"], "chatgpt")

    def test_identical_reingest_deduplicates_and_preserves_history(self):
        first = self.ingest()
        before_text = self.tracker.events_path.read_text(encoding="utf-8")
        second = self.tracker.ingest(
            self.payload,
            comment="Re-observed unchanged posting during daily scan",
            actor="hermes",
        )
        self.assertEqual(second["result"], "duplicate")
        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(len(self.tracker.list_rows()), 1)
        after_text = self.tracker.events_path.read_text(encoding="utf-8")
        self.assertTrue(after_text.startswith(before_text))
        self.assertEqual([e["action"] for e in self.tracker.read_events()], ["created", "reviewed"])
        self.assertEqual(len(self.tracker.get_job(first["job_id"])["history"]), 2)

    def test_material_edits_require_comment(self):
        with self.assertRaises(ValueError):
            self.tracker.ingest(self.payload, comment="", actor="chatgpt")
        job_id = self.ingest()["job_id"]
        with self.assertRaises(ValueError):
            self.tracker.update_job(job_id, {"fit_score": 84}, comment="", actor="chatgpt")
        with self.assertRaises(ValueError):
            self.tracker.record_event(
                actor="system", entity_type="system", entity_id="test", action="failed",
                before={}, after={}, comment="",
            )

    def test_update_records_before_after_and_appends(self):
        job_id = self.ingest()["job_id"]
        prefix = self.tracker.events_path.read_text(encoding="utf-8")
        result = self.tracker.update_job(
            job_id,
            {"fit_score": 84, "priority": "high", "processing_status": "application_prepared"},
            comment="Scored against verified career evidence",
            actor="chatgpt",
            action="reviewed",
            requires_owner_review=True,
        )
        self.assertTrue(self.tracker.events_path.read_text(encoding="utf-8").startswith(prefix))
        event = result["event"]
        self.assertEqual(event["before"]["fit_score"], "")
        self.assertEqual(event["after"]["fit_score"], 84)
        self.assertTrue(event["requires_owner_review"])
        row = self.tracker.list_rows()[0]
        self.assertEqual(row["fit_score"], "84")
        self.assertEqual(self.tracker.get_job(job_id)["job"]["priority"], "high")

    def test_chatgpt_to_hermes_queue_transition(self):
        job_id = self.ingest()["job_id"]
        result = self.tracker.queue_for_hermes(
            job_id,
            comment="Owner approved continuation by Hermes recurring route",
            actor="chatgpt",
        )
        job = result["record"]["job"]
        self.assertEqual(job["owner"], "hermes")
        self.assertEqual(job["processing_status"], "queued_for_hermes")
        self.assertEqual(result["event"]["action"], "queued")
        self.assertEqual(result["event"]["before"]["owner"], "chatgpt")

    def test_rejection_failure_and_retry_retain_reasons(self):
        job_id = self.ingest()["job_id"]
        self.tracker.update_job(
            job_id,
            {"processing_status": "rejected", "next_action": "None"},
            comment="Rejected because the role requires an unsupported mandatory licence",
            actor="hermes", action="rejected",
        )
        self.tracker.record_event(
            actor="system", entity_type="job", entity_id=job_id, action="failed",
            before={"attempt": 1}, after={"error": "renderer timeout"},
            comment="PDF generation failed after the bounded renderer timeout",
            requires_owner_review=False,
        )
        self.tracker.record_event(
            actor="system", entity_type="job", entity_id=job_id, action="retried",
            before={"error": "renderer timeout"}, after={"corrective_action": "used single-column fallback"},
            comment="Retried with the simpler supported renderer",
            requires_owner_review=False,
        )
        actions = [event["action"] for event in self.tracker.read_events(job_id)]
        self.assertEqual(actions, ["created", "rejected", "failed", "retried"])
        record_history = self.tracker.get_job(job_id)["history"]
        self.assertEqual([event["action"] for event in record_history], actions)
        self.assertIn("unsupported mandatory licence", record_history[1]["comment"])
        self.assertEqual(record_history[-1]["after"]["corrective_action"], "used single-column fallback")

    def test_claim_change_can_preserve_previous_wording_and_evidence(self):
        event = self.tracker.record_event(
            actor="chatgpt", entity_type="claim", entity_id="claim-1", action="updated",
            before={"wording": "Reduced costs by 47%", "evidence_status": "unverified"},
            after={"wording": "Applied value engineering to reduce cost", "evidence_status": "supported"},
            comment="Removed an unsupported percentage while retaining the verified activity",
            source_refs=["verified-career-profile"], confidence="high",
        )
        self.assertEqual(event["before"]["wording"], "Reduced costs by 47%")
        self.assertEqual(event["after"]["evidence_status"], "supported")

    def test_resume_event_records_required_generation_provenance(self):
        event = self.tracker.record_event(
            actor="chatgpt", entity_type="resume", entity_id="resume-job-1", action="generated",
            before={},
            after={
                "source_job": "job-1", "template_version": "ats-one-column-v1",
                "renderer": "ReportLab", "evidence_snapshot": "verified-career-profile-2026-08-01",
            },
            comment="Generated from the verified evidence snapshot",
            requires_owner_review=True,
        )
        self.assertEqual(set(event["after"]), {"source_job", "template_version", "renderer", "evidence_snapshot"})


if __name__ == "__main__":
    unittest.main()
