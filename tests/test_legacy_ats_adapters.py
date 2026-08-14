from __future__ import annotations

import json
import unittest
from pathlib import Path

from career_engine.sources.adapters.successfactors import SuccessFactorsAdapter
from career_engine.sources.adapters.taleo import TaleoAdapter
from career_engine.sources.consultants import _adapter_for_row


class KhatibAlamiRoutingTests(unittest.TestCase):
    def test_current_successfactors_portal_is_used(self) -> None:
        adapter = SuccessFactorsAdapter()
        name, base = adapter._company_base(
            "Khatib & Alami|https://careers.khatibalami.com/"
        )
        self.assertEqual(name, "Khatib & Alami")
        self.assertEqual(base, "https://careers.khatibalami.com")


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
    def test_khatib_worley_routable_and_stale_abdullahal_record_absent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        rows = json.loads(
            (root / "projects/job-automation/config/consultants-bookmarks.v1.json").read_text(encoding="utf-8")
        )["bookmarks"]
        by_id = {row["id"]: row for row in rows}
        self.assertNotIn("sap-successfactors-abdullahal", by_id)

        ka = by_id["khatib-alami-successfactors"]
        self.assertEqual(ka["status"], "active")
        self.assertTrue(ka["scan"])
        self.assertEqual(ka["url"], "https://careers.khatibalami.com/")
        adapter, name, route, provider = _adapter_for_row(ka, offline=False)
        self.assertIsInstance(adapter, SuccessFactorsAdapter)
        self.assertEqual(name, "successfactors")
        self.assertEqual(route, "official_ats_api")
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
