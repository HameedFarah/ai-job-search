"""Scanner regressions for target-lane skipping before manual-review fallback."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from career_engine import scanner


TAXONOMY = json.loads(
    (Path(__file__).resolve().parents[1] / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text(encoding="utf-8")
)


class ScannerTargetSkipTests(unittest.TestCase):
    def test_short_non_target_role_skips_before_prepare_and_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "scan.json"
            input_path.write_text(json.dumps({"jobs": [{
                "company": "Example",
                "role": "Civil Engineer",
                "full_job_description": "Too short",
                "source_url": "https://example.com/job/1",
                "external_job_id": "1",
            }]}), encoding="utf-8")
            paths = SimpleNamespace(tracker_base=root / "tracker")
            rejected_state = {
                "job_id": "civil-1",
                "live_status": "unverified",
                "fit_score": {"total": None, "recommendation": "rejected"},
                "route": {"route": "skipped", "application_url": ""},
                "blockers": [],
                "warnings": [],
                "outputs": {},
                "stage": "rejected",
                "skip_reason": "non_target_out_of_lane_role",
            }
            with patch.object(scanner, "load_bundle", return_value={
                "bundle_hash": "bundle-1",
                "taxonomy": TAXONOMY,
                "config": {"daily_scanner": {
                    "minimum_score_for_generation": 70,
                    "maximum_generation_packets_per_scan": 5,
                }},
            }), patch.object(scanner, "load_config", return_value=({}, paths)), \
                    patch.object(scanner, "_reject_non_target_title", return_value=rejected_state) as reject, \
                    patch.object(scanner, "prepare_job") as prepare, \
                    patch.object(scanner, "_manual_review_for_insufficient_description") as manual_review:
                report = scanner.run_scan(input_path, root=root, scanner_id="hermes_scanner")

        prepare.assert_not_called()
        manual_review.assert_not_called()
        reject.assert_called_once()
        self.assertEqual(report["manual_review_needed"], [])
        self.assertEqual(report["results"][0]["processing_status"], "rejected")
        self.assertEqual(report["results"][0]["skip_reason"], "non_target_out_of_lane_role")
        self.assertEqual(report["statistics"]["manual_review_needed"], 0)

    def test_target_management_role_still_uses_normal_prepare_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "scan.json"
            input_path.write_text(json.dumps({"jobs": [{
                "company": "Example",
                "role": "Design Manager",
                "full_job_description": "A sufficiently detailed design-management role description for the normal preparation path.",
                "source_url": "https://example.com/job/2",
                "external_job_id": "2",
            }]}), encoding="utf-8")
            paths = SimpleNamespace(tracker_base=root / "tracker")
            prepared_state = {
                "job_id": "design-manager-1",
                "live_status": "live",
                "fit_score": {"total": 85, "recommendation": "high_priority"},
                "route": {"route": "portal", "application_url": "https://example.com/job/2"},
                "blockers": [],
                "warnings": [],
                "outputs": {"generation_packet": "/tmp/packet.json"},
                "stage": "generation_ready",
            }
            with patch.object(scanner, "load_bundle", return_value={
                "bundle_hash": "bundle-1",
                "taxonomy": TAXONOMY,
                "config": {"daily_scanner": {
                    "minimum_score_for_generation": 70,
                    "maximum_generation_packets_per_scan": 5,
                }},
            }), patch.object(scanner, "load_config", return_value=({}, paths)), \
                    patch.object(scanner, "_reject_non_target_title") as reject, \
                    patch.object(scanner, "prepare_job", return_value=prepared_state) as prepare:
                report = scanner.run_scan(input_path, root=root, scanner_id="hermes_scanner")

        reject.assert_not_called()
        prepare.assert_called_once()
        self.assertEqual(report["results"][0]["processing_status"], "generation_ready")
        self.assertEqual(report["statistics"]["generation_candidates"], 1)


if __name__ == "__main__":
    unittest.main()
