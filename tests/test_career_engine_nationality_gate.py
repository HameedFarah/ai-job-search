from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from career_engine.core import match_evidence, nationality_requirement_gate, normalize_job, score_fit


REPO = Path(__file__).resolve().parents[1]
RUNTIME_BUNDLE = json.loads(
    (REPO / "projects/job-automation/config/runtime-bundle.v1.json").read_text(encoding="utf-8")
)


def evaluate(role: str, description: str, *, nationalities=None):
    bundle = copy.deepcopy(RUNTIME_BUNDLE)
    if nationalities is not None:
        bundle["identity"] = {**bundle.get("identity", {}), "nationalities": nationalities}
    normalized = normalize_job({
        "company": "TestCo",
        "role": role,
        "location": "Riyadh, Saudi Arabia",
        "full_job_description": description,
    }, bundle["taxonomy"])
    return score_fit(normalized, match_evidence(normalized, bundle), bundle)


class NationalityGateTests(unittest.TestCase):
    def test_explicit_nationality_mandate_blocks_nonmatching_candidate(self):
        score = evaluate(
            "Construction Manager (Saudi National)",
            "We're looking for an experienced Construction Manager (Saudi National). Lead project delivery, construction coordination, client interfaces, quality controls and multidisciplinary site teams across major developments.",
        )
        self.assertLess(score["total"], 70)
        self.assertEqual(score["calibration"]["eligibility_blocker"], "mandatory_nationality_mismatch")
        self.assertTrue(score["gaps"][0].startswith("mandatory_nationality_mismatch:"))

    def test_explicit_mandate_matches_candidate_nationality(self):
        score = evaluate(
            "Design Manager - Infrastructure (Saudi National)",
            "Saudi nationals only. Lead infrastructure design management, multidisciplinary consultant coordination, client interfaces, technical assurance, project delivery and construction-stage design reviews across major programmes.",
            nationalities=["Saudi"],
        )
        self.assertNotIn("mandatory_nationality_mismatch", score["calibration"])
        self.assertTrue(score["calibration"]["target_management"])

    def test_controls_do_not_trigger_nationality_gate(self):
        for text in (
            "Construction Manager in Saudi Arabia.",
            "Experience with Saudi projects and Saudi building code.",
            "Saudi Council of Engineers registration preferred.",
            "Saudi experience required.",
        ):
            self.assertIsNone(nationality_requirement_gate(text))
        score = evaluate(
            "Construction Manager",
            "Construction Manager in Saudi Arabia. Lead construction delivery and multidisciplinary coordination with strong Saudi project experience, Saudi building-code knowledge, client management, quality assurance and site-stage technical reviews.",
        )
        self.assertNotIn("mandatory_nationality_mismatch", score["calibration"])

    def test_general_management_roles_keep_existing_classification(self):
        score = evaluate(
            "Construction Manager",
            "Lead construction delivery and multidisciplinary design coordination across complex projects, managing consultant interfaces, client stakeholders, programme controls, quality reviews, risk management and construction-stage issue resolution.",
        )
        self.assertNotIn("mandatory_nationality_mismatch", score["calibration"])
        self.assertTrue(score["calibration"]["target_management"])
        self.assertEqual(score["calibration"]["mismatch_multiplier"], 1.0)


if __name__ == "__main__":
    unittest.main()
