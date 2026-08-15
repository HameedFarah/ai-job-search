"""Target-lane skip policy regressions."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from career_engine.core import _role_title_signals
from career_engine.targeting import auto_skip_reason


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = json.loads(
    (ROOT / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text(encoding="utf-8")
)


class TargetLaneSkipTests(unittest.TestCase):
    def test_non_target_roles_are_terminal_skip_candidates(self) -> None:
        expected = {
            "Civil Engineer": "non_target_out_of_lane_role",
            "Site Inspector": "non_target_out_of_lane_role",
            "Finance Manager": "non_target_out_of_lane_role",
            "Urban Designer": "non_target_production_individual_contributor",
            "Specialist - Reception and Retail": "non_target_service_or_admin_role",
            "Receptionist": "non_target_service_or_admin_role",
        }
        for role, reason in expected.items():
            with self.subTest(role=role):
                calibration = _role_title_signals(role, TAXONOMY)
                self.assertEqual(auto_skip_reason({"role": role}, {"calibration": calibration}), reason)

    def test_target_management_roles_are_not_auto_skipped(self) -> None:
        roles = [
            "Design Manager",
            "Urban Design Manager",
            "Architectural Design Manager",
            "Senior Project Manager",
            "Construction Manager",
            "Project Director",
        ]
        for role in roles:
            with self.subTest(role=role):
                calibration = _role_title_signals(role, TAXONOMY)
                self.assertEqual(auto_skip_reason({"role": role}, {"calibration": calibration}), "")


if __name__ == "__main__":
    unittest.main()
