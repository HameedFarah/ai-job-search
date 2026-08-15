"""Regression coverage for Red Sea Global / SuccessFactors JD extraction."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from career_engine.sources.adapters.successfactors import (
    SuccessFactorsAdapter,
    _JobDescriptionTextParser,
)


class SuccessFactorsRedSeaRegressionTests(unittest.TestCase):
    def test_successfactors_accepts_block_jobdescription_root(self) -> None:
        parser = _JobDescriptionTextParser()
        parser.feed(
            """
            <div class="jobdescription">
              <h2>Job Purpose</h2>
              <p>Support reception and retail operations while maintaining service standards.</p>
              <h2>Job Responsibilities</h2>
              <ul><li>Coordinate daily guest-facing activities.</li><li>Maintain operational records.</li></ul>
              <h2>Qualification and Experience</h2>
              <p>Relevant professional experience is required.</p>
              <h2>Essential Skills</h2>
              <p>Communication and stakeholder coordination.</p>
            </div>
            """
        )
        text = parser.text()
        self.assertIn("Job Purpose", text)
        self.assertIn("Job Responsibilities", text)
        self.assertIn("Qualification and Experience", text)
        self.assertIn("Essential Skills", text)
        self.assertIn("Coordinate daily guest-facing activities", text)

    def test_successfactors_search_keeps_full_block_root_detail(self) -> None:
        adapter = SuccessFactorsAdapter()
        detail_html = """
          <div class="jobdescription">
            <h2>Job Purpose</h2><p>Support reception and retail operations.</p>
            <h2>Job Responsibilities</h2><p>Coordinate guest-facing activities and records.</p>
            <h2>Qualification and Experience</h2><p>Relevant experience required.</p>
            <h2>Essential Skills</h2><p>Communication and coordination.</p>
          </div>
        """
        with patch.object(
            adapter,
            "_enumerate",
            return_value=[(
                "857334523",
                "Specialist - Reception and Retail",
                "/job/Specialist-Reception-and-Retail/857334523/",
            )],
        ), patch.object(adapter, "_load_detail", return_value=detail_html):
            jobs = adapter.search(
                company="Red Sea Global|https://careers.theredsea.sa/",
                limit=1,
                fetch_full=True,
                offline=False,
            )

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.external_job_id, "857334523")
        self.assertGreater(len(job.description_text), 150)
        self.assertIn("Job Responsibilities", job.description_text)
        self.assertIn("Qualification and Experience", job.description_text)
        self.assertFalse(job.extra.get("detail_fetch_error"))


if __name__ == "__main__":
    unittest.main()
