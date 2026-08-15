from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from career_engine.sources.adapters.eightfold_neom import NeomEightfoldAdapter
from career_engine.sources.adapters.official_html import OfficialHtmlAdapter
from career_engine.sources.adapters.phenom import PhenomAdapter
from career_engine.sources.base import SourceError
from career_engine.sources.consultants import scan_consultants


class OfficialHtmlRepairTests(unittest.TestCase):
    @patch("career_engine.sources.adapters.official_html.network.fetch_text")
    def test_saudconsult_verified_empty_marker_is_true_empty(self, fetch_text) -> None:
        fetch_text.return_value = "<main><h3>Latest Openings</h3><p>There are no openings in this category at the moment.</p></main>"
        jobs = OfficialHtmlAdapter().search(
            company="SaudConsult|https://saudconsult.com/jobs/|saudconsult",
            location="Saudi Arabia",
        )
        self.assertEqual(jobs, [])

    @patch("career_engine.sources.adapters.official_html.network.fetch_text")
    def test_unknown_shape_fails_closed_instead_of_fake_zero(self, fetch_text) -> None:
        fetch_text.return_value = "<html><body>Careers site changed completely</body></html>"
        with self.assertRaises(SourceError):
            OfficialHtmlAdapter().search(
                company="SaudConsult|https://saudconsult.com/jobs/|saudconsult",
                location="Saudi Arabia",
            )

    @patch("career_engine.sources.adapters.official_html.network.fetch_text")
    def test_meinhardt_wpjm_extracts_saudi_role(self, fetch_text) -> None:
        fetch_text.return_value = """
        <ul class='job_listings'>
          <li class='job_listing type-job_listing'>
            <a href='https://mjobs.meinhardtgroup.com/job/mechanical-engineer/'>
              <div class='position'><h3>Mechanical Engineer</h3></div>
              <div class='location'>Saudi Arabia</div>
            </a>
          </li>
          <li class='job_listing type-job_listing'>
            <a href='https://mjobs.meinhardtgroup.com/job/design-manager/'>
              <div class='position'><h3>Design Manager</h3></div>
              <div class='location'>Singapore</div>
            </a>
          </li>
        </ul>
        """
        jobs = OfficialHtmlAdapter().search(
            company="Meinhardt|https://mjobs.meinhardtgroup.com/|wpjm",
            location="Saudi Arabia",
            limit=25,
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].role, "Mechanical Engineer")
        self.assertEqual(jobs[0].company, "Meinhardt")
        self.assertEqual(jobs[0].location, "Saudi Arabia")

    @patch("career_engine.sources.adapters.official_html.network.fetch_text")
    def test_buro_tribepad_extracts_only_saudi_role(self, fetch_text) -> None:
        fetch_text.return_value = """
        <div class='vacancy'>
          <span>Riyadh, Saudi Arabia</span>
          <a href='/jobs/job/Associate-Director-Transportation/2281'>Associate Director – Transportation</a>
        </div>
        <div class='vacancy'>
          <span>Bath, United Kingdom</span>
          <a href='/jobs/job/Finance-Systems-Analyst/9999'>Finance Systems Analyst</a>
        </div>
        """
        jobs = OfficialHtmlAdapter().search(
            company="Buro Happold|https://vacancies.burohappold.com/jobs/search/-1/|tribepad",
            location="Saudi Arabia",
            limit=25,
        )
        self.assertEqual([job.role for job in jobs], ["Associate Director – Transportation"])
        self.assertEqual(jobs[0].external_job_id, "2281")

    @patch("career_engine.sources.adapters.official_html.network.fetch_text")
    def test_applytojob_current_empty_marker(self, fetch_text) -> None:
        fetch_text.return_value = "<main><p>There are no open positions at this time.</p></main>"
        jobs = OfficialHtmlAdapter().search(
            company="Dar Al-Handasah|https://daralhandasahshairandpartners.applytojob.com/apply|applytojob"
        )
        self.assertEqual(jobs, [])


class StructuredRepairTests(unittest.TestCase):
    @patch("career_engine.sources.adapters.eightfold_neom.network.fetch_json")
    def test_neom_eightfold_true_empty_requires_successful_api(self, fetch_json) -> None:
        fetch_json.return_value = {"positions": [], "count": 0, "domain": "neom.com"}
        jobs = NeomEightfoldAdapter().search(
            company="NEOM|https://careers.neom.com/careers?domain=neom.com",
            location="Saudi Arabia",
        )
        self.assertEqual(jobs, [])

    @patch("career_engine.sources.adapters.eightfold_neom.network.fetch_json")
    def test_neom_eightfold_maps_official_position(self, fetch_json) -> None:
        fetch_json.return_value = {
            "positions": [{
                "id": "563087400000001",
                "name": "Senior Architect",
                "location": "NEOM, Saudi Arabia",
                "department": "Design",
                "t_create": 1786500000,
                "canonicalPositionUrl": "https://careers.neom.com/careers?domain=neom.com&pid=563087400000001",
            }],
            "count": 1,
        }
        jobs = NeomEightfoldAdapter().search(
            company="NEOM|https://careers.neom.com/careers?domain=neom.com",
            location="Saudi Arabia",
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].role, "Senior Architect")
        self.assertTrue(jobs[0].provenance.official)

    @patch("career_engine.sources.adapters.phenom.network.request_json")
    def test_phenom_filters_bechtel_to_saudi(self, request_json) -> None:
        request_json.return_value = {
            "refineSearch": {
                "status": 200,
                "totalHits": 2,
                "data": {"jobs": [
                    {"jobId": "283837", "title": "Reporting Specialist", "location": "Riyadh, Saudi Arabia", "postedDate": "2026-08-12T10:00:00.000+0000"},
                    {"jobId": "999999", "title": "US Role", "location": "Houston, TX, United States", "postedDate": "2026-08-12T10:00:00.000+0000"},
                ]},
            }
        }
        jobs = PhenomAdapter().search(
            company="Bechtel|https://jobs.bechtel.com/us/en",
            location="Saudi Arabia",
            limit=25,
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].company, "Bechtel")
        self.assertEqual(jobs[0].role, "Reporting Specialist")
        self.assertTrue(jobs[0].detail_url.startswith("https://jobs.bechtel.com/us/en/job/283837/"))


class ConsultantRegistryRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.payload = json.loads(
            (self.root / "projects/job-automation/config/consultants-bookmarks.v1.json").read_text(encoding="utf-8")
        )
        self.rows = {row["id"]: row for row in self.payload["bookmarks"]}

    def test_target_routes_are_explicit_and_no_bypass_is_enabled(self) -> None:
        self.assertEqual(self.rows["saudconsult"]["adapter"], "official_html")
        self.assertEqual(self.rows["al-othaim-investment"]["url"], "https://www.alothaiminvestment.com/careers")
        self.assertEqual(self.rows["omrania"]["duplicate_of"], "egis")
        self.assertEqual(self.rows["dar-al-handasah-apply"]["adapter"], "official_html")
        self.assertEqual(self.rows["neom"]["adapter"], "eightfold_neom")
        self.assertEqual(self.rows["bechtel-saudi-jobs"]["adapter"], "phenom")
        self.assertEqual(self.rows["buro-happold"]["adapter"], "official_html")
        self.assertEqual(self.rows["meinhardt"]["url"], "https://mjobs.meinhardtgroup.com/")
        self.assertEqual(self.rows["wsp"]["status"], "manual")
        self.assertFalse(self.rows["wsp"]["scan"])
        self.assertEqual(self.rows["dar-al-omran"]["status"], "manual")
        self.assertFalse(self.rows["dar-al-omran"]["scan"])
        self.assertEqual(self.rows["neom-virtual-career-fair"]["status"], "manual")
        self.assertFalse(self.rows["neom-virtual-career-fair"]["scan"])

    def test_offline_registry_summary_matches_repaired_states(self) -> None:
        report = scan_consultants(root=self.root, offline=True, limit=3)
        self.assertEqual(report["summary"]["active_records"], 24)
        self.assertEqual(report["summary"]["sources_skipped"], 6)
        self.assertEqual(report["summary"]["sources_attempted"], 18)
        self.assertFalse(report["send_or_submit"])


if __name__ == "__main__":
    unittest.main()
