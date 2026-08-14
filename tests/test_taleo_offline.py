from __future__ import annotations

import unittest
from unittest.mock import patch

from career_engine.sources.adapters.taleo import TaleoAdapter


class TaleoOfflineTests(unittest.TestCase):
    def test_portal_number_is_extracted_from_current_career_page(self) -> None:
        adapter = TaleoAdapter()
        with patch("career_engine.sources.adapters.taleo.network.fetch_text", return_value="portalNo: '101430233'"):
            with patch("career_engine.sources.adapters.taleo.network.request_json", return_value={"requisitionList": []}) as request:
                jobs = adapter.search(company="worleyparsons|ext", location="Saudi Arabia", limit=10)
        self.assertEqual(jobs, [])
        self.assertIn("lang=en&portal=101430233", request.call_args.args[0])
        self.assertEqual(request.call_args.kwargs["json_body"]["fieldData"]["fields"]["LOCATION"], "")
        self.assertEqual(request.call_args.kwargs["headers"]["X-Requested-With"], "XMLHttpRequest")

    def test_current_column_response_is_mapped(self) -> None:
        job = TaleoAdapter()._map_item(
            {"column": ["Project Director", "RIY-987", "Full-time", '["Saudi Arabia-Riyadh"]', "Sep 29, 2026"]},
            tenant="worleyparsons",
            career_section="ext",
        )
        self.assertEqual(job.role, "Project Director")
        self.assertEqual(job.external_job_id, "RIY-987")
        self.assertEqual(job.location, "Saudi Arabia-Riyadh")

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
