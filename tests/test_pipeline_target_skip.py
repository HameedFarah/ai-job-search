"""Pipeline regression: clearly non-target jobs terminate before generation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from career_engine import pipeline


class _FakeTracker:
    def __init__(self) -> None:
        self.updates = []

    def ingest(self, payload, **kwargs):
        return {"job_id": "skip-role-001"}

    def update_job(self, job_id, fields, **kwargs):
        self.updates.append((job_id, fields, kwargs))


class PipelineTargetSkipTests(unittest.TestCase):
    def test_prepare_terminally_rejects_production_individual_contributor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tracker_base = root / "tracker"
            tracker_base.mkdir()
            paths = SimpleNamespace(repo_root=root / "repo", tracker_base=tracker_base)
            config = {"scoring": {"thresholds": {"high_priority": 70}}}
            tracker = _FakeTracker()
            normalized = {
                "source": "successfactors",
                "reference": "857334523",
                "source_url": "https://careers.example/job/857334523/",
                "company": "Example Company",
                "role": "Urban Designer",
                "location": "Riyadh",
                "posting_date": "2026-08-15",
                "posting_date_precision": "day",
                "posting_date_source": "source",
                "full_job_description": "Produce urban design packages and coordinate design information.",
                "jd_hash": "abc",
                "live_status": "live",
                "live_verified_at": "2026-08-15T10:00:00+00:00",
                "live_verification_source": "official",
                "requirements": [],
            }
            score = {
                "total": 82,
                "recommendation": "high_priority",
                "calibration": {
                    "out_of_lane": False,
                    "production": True,
                    "has_management": False,
                },
            }

            def should_not_generate(**kwargs):
                raise AssertionError("non-target role must not create a generation packet")

            with patch.object(pipeline, "load_config", return_value=(config, paths)), \
                    patch.object(pipeline, "load_bundle", return_value={"taxonomy": {}, "bundle_hash": "bundle-1"}), \
                    patch.object(pipeline, "normalize_job", return_value=normalized), \
                    patch.object(pipeline, "_load_tracker", return_value=tracker), \
                    patch.object(pipeline, "match_evidence", return_value={}), \
                    patch.object(pipeline, "score_fit", return_value=score), \
                    patch.object(pipeline, "decide_route", return_value={"route": "portal", "application_url": normalized["source_url"], "blocker": ""}), \
                    patch.object(pipeline, "validate_live_status", return_value=[]), \
                    patch.object(pipeline, "create_generation_packet", side_effect=should_not_generate):
                state = pipeline.prepare({"anything": True}, root=root)

        self.assertEqual(state["stage"], "rejected")
        self.assertEqual(state["skip_reason"], "non_target_production_individual_contributor")
        self.assertEqual(state["blockers"], [])
        self.assertNotIn("generation_packet", state["outputs"])
        self.assertEqual(tracker.updates[-1][1]["processing_status"], "rejected")
        self.assertEqual(tracker.updates[-1][1]["next_action"], "Skipped automatically as a non-target role")
        self.assertFalse(tracker.updates[-1][2]["requires_owner_review"])


if __name__ == "__main__":
    unittest.main()
