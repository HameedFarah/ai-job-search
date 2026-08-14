from __future__ import annotations

import unittest

from career_engine.sources.adapters.taleo import TaleoAdapter


class TaleoOfflineTests(unittest.TestCase):
    def test_offline_fixture_maps_one_worley_job(self) -> None:
        jobs = TaleoAdapter().search(
            company="worleyparsons|ext",
            location="Saudi Arabia",
            limit=10,
            fetch_full=True,
            offline=True,
        )
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.company, "Worley")
        self.assertEqual(job.role, "Project Director")
        self.assertEqual(job.location, "Saudi Arabia-Riyadh")
        self.assertEqual(job.external_job_id, "RIY-987")
        self.assertTrue(job.provenance.official)


if __name__ == "__main__":
    unittest.main()
