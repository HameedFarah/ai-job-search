from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from career_engine.sources.adapters.successfactors_xml import SuccessFactorsXmlAdapter
from career_engine.sources.adapters.taleo import TaleoAdapter
from career_engine.sources.consultants import _adapter_for_row


class SuccessFactorsXmlAdapterTests(unittest.TestCase):
    def test_feed_target_preserves_company_identifier_and_builds_xml_feed(self) -> None:
        adapter = SuccessFactorsXmlAdapter()
        name, feed_url, company_id = adapter._feed_target(
            "Khatib & Alami|https://career2.successfactors.eu/career?company=khatibalam&_s.crb=abc"
        )
        self.assertEqual(name, "Khatib & Alami")
        self.assertEqual(company_id, "khatibalam")
        query = parse_qs(urlsplit(feed_url).query)
        self.assertEqual(query["company"], ["khatibalam"])
        self.assertEqual(query["career_ns"], ["job_listing_summary"])
        self.assertEqual(query["resultType"], ["XML"])
        self.assertNotIn("_s.crb", query)

    def test_parse_standard_sap_job_feed(self) -> None:
        adapter = SuccessFactorsXmlAdapter()
        xml = """<?xml version='1.0' encoding='UTF-8'?>
<jobs>
  <job>
    <title>Senior Design Manager</title>
    <company>Khatib &amp; Alami</company>
    <city>Riyadh</city>
    <country>Saudi Arabia</country>
    <referencenumber>REQ-123</referencenumber>
    <description>&lt;p&gt;Lead multidisciplinary design delivery.&lt;/p&gt;</description>
    <url>https://career2.successfactors.eu/career?company=khatibalam&amp;career_job_req_id=REQ-123</url>
  </job>
</jobs>"""
        jobs = adapter._parse_feed(
            xml,
            company_name="Khatib & Alami",
            company_id="khatibalam",
            feed_url="https://career2.successfactors.eu/career?company=khatibalam&career_ns=job_listing_summary&resultType=XML",
        )
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.role, "Senior Design Manager")
        self.assertEqual(job.company, "Khatib & Alami")
        self.assertEqual(job.location, "Riyadh, Saudi Arabia")
        self.assertEqual(job.external_job_id, "REQ-123")
        self.assertIn("multidisciplinary design delivery", job.description_text)
        self.assertTrue(job.provenance.official)


class TaleoAdapterTests(unittest.TestCase):
    def test_identifier_accepts_compound_slug_and_official_url(self) -> None:
        adapter = TaleoAdapter()
        self.assertEqual(adapter._identifier("worleyparsons|ext"), ("worleyparsons", "ext"))
        self.assertEqual(
            adapter._identifier("https://worleyparsons.taleo.net/careersection/ext/moresearch.ftl"),
            ("worleyparsons", "ext"),
        )

    def test_map_item_builds_official_detail_url(self) -> None:
        adapter = TaleoAdapter()
        job = adapter._map_item(
            {
                "contestNo": "RIY-987",
                "title": "Project Director",
                "primaryLocation": "Saudi Arabia-Riyadh",
                "jobField": "Project Management",
                "postingDate": "2026-08-14",
                "organization": "Worley",
            },
            tenant="worleyparsons",
            career_section="ext",
        )
        self.assertEqual(job.company, "Worley")
        self.assertEqual(job.role, "Project Director")
        self.assertEqual(job.location, "Saudi Arabia-Riyadh")
        self.assertEqual(
            job.detail_url,
            "https://worleyparsons.taleo.net/careersection/ext/jobdetail.ftl?job=RIY-987",
        )
        self.assertTrue(job.provenance.official)


class ConsultantRegistryTests(unittest.TestCase):
    def test_new_adapters_are_routable_and_stale_abdullahal_record_is_absent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        rows = json.loads(
            (root / "projects/job-automation/config/consultants-bookmarks.v1.json").read_text(encoding="utf-8")
        )["bookmarks"]
        by_id = {row["id"]: row for row in rows}
        self.assertNotIn("sap-successfactors-abdullahal", by_id)

        ka = by_id["khatib-alami-successfactors"]
        self.assertEqual(ka["status"], "active")
        self.assertTrue(ka["scan"])
        adapter, name, route, provider = _adapter_for_row(ka, offline=False)
        self.assertIsInstance(adapter, SuccessFactorsXmlAdapter)
        self.assertEqual(name, "successfactors_xml")
        self.assertEqual(route, "official_ats_feed")
        self.assertIsNone(provider)

        worley = by_id["worley-taleo"]
        self.assertEqual(worley["status"], "active")
        self.assertTrue(worley["scan"])
        adapter, name, route, provider = _adapter_for_row(worley, offline=False)
        self.assertIsInstance(adapter, TaleoAdapter)
        self.assertEqual(name, "taleo")
        self.assertEqual(route, "official_ats_api")
        self.assertIsNone(provider)


if __name__ == "__main__":
    unittest.main()
