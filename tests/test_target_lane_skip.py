"""Target-lane skip policy regressions."""

from __future__ import annotations

import unittest

from career_engine.targeting import auto_skip_reason


class TargetLaneSkipTests(unittest.TestCase):
    def test_non_target_roles_are_terminal_skip_candidates(self) -> None:
        cases = [
            ("Civil Engineer", {"out_of_lane": True, "production": True, "has_management": False}, "non_target_out_of_lane_role"),
            ("Site Inspector", {"out_of_lane": True, "production": True, "has_management": False}, "non_target_out_of_lane_role"),
            ("Finance Manager", {"out_of_lane": True, "production": False, "has_management": True}, "non_target_out_of_lane_role"),
            ("Urban Designer", {"out_of_lane": False, "production": True, "has_management": False}, "non_target_production_individual_contributor"),
            ("Specialist - Reception and Retail", {"out_of_lane": False, "production": True, "has_management": False}, "non_target_service_or_admin_role"),
            ("Receptionist", {"out_of_lane": False, "production": False, "has_management": False}, "non_target_service_or_admin_role"),
        ]
        for role, calibration, expected in cases:
            with self.subTest(role=role):
                self.assertEqual(auto_skip_reason({"role": role}, {"calibration": calibration}), expected)

    def test_target_management_roles_are_not_auto_skipped(self) -> None:
        cases = [
            ("Design Manager", {"out_of_lane": False, "production": False, "has_management": True}),
            ("Urban Design Manager", {"out_of_lane": False, "production": True, "has_management": True}),
            ("Architectural Design Manager", {"out_of_lane": False, "production": False, "has_management": True}),
            ("Senior Project Manager", {"out_of_lane": False, "production": False, "has_management": True}),
            ("Construction Manager", {"out_of_lane": False, "production": False, "has_management": True}),
            ("Project Director", {"out_of_lane": False, "production": False, "has_management": True}),
        ]
        for role, calibration in cases:
            with self.subTest(role=role):
                self.assertEqual(auto_skip_reason({"role": role}, {"calibration": calibration}), "")


if __name__ == "__main__":
    unittest.main()
