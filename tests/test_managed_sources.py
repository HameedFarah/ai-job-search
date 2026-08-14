import json
import unittest
from pathlib import Path

from career_engine.sources.adapters.career_ops_managed import ManagedCareerOpsAdapter
from career_engine.sources.managed_cli import provider_payload, run_probe
from career_engine.sources.managed_providers import PROVIDERS, UPSTREAM_REF, UPSTREAM_REPO


class ManagedProviderManifestTests(unittest.TestCase):
    def test_priority_gcc_ats_are_present(self):
        for provider in ("workday", "successfactors", "oraclecloud", "icims", "avature", "eightfold"):
            self.assertIn(provider, PROVIDERS)
            self.assertEqual(PROVIDERS[provider]["priority"], 1)

    def test_global_reuse_providers_are_present(self):
        for provider in ("jobvite", "jibeapply", "bamboohr", "breezy", "comeet", "teamtailor"):
            self.assertIn(provider, PROVIDERS)

    def test_payload_is_pinned_and_no_send(self):
        payload = provider_payload()
        self.assertEqual(payload["upstream_repo"], UPSTREAM_REPO)
        self.assertEqual(payload["reviewed_ref"], UPSTREAM_REF)
        self.assertFalse(payload["automatic_upstream_activation"])
        self.assertFalse(payload["send_or_submit"])


class ManagedCompanySpecTests(unittest.TestCase):
    def test_url_spec(self):
        parsed = ManagedCareerOpsAdapter._parse_company_spec("https://example.wd5.myworkdayjobs.com/en-US/Search")
        self.assertEqual(parsed["careers_url"], "https://example.wd5.myworkdayjobs.com/en-US/Search")

    def test_name_pipe_url_spec(self):
        parsed = ManagedCareerOpsAdapter._parse_company_spec("Parsons|https://example.test/jobs")
        self.assertEqual(parsed, {"name": "Parsons", "careers_url": "https://example.test/jobs"})

    def test_json_spec(self):
        parsed = ManagedCareerOpsAdapter._parse_company_spec(
            json.dumps({"name": "Employer", "careers_url": "https://example.test/jobs", "siteNumber": "CX_1"})
        )
        self.assertEqual(parsed["siteNumber"], "CX_1")


class ManagedOfflineProbeTests(unittest.TestCase):
    def test_workday_offline_maps_to_career_engine_contract(self):
        payload = run_probe(
            provider="workday",
            company="Example Employer|https://example.wd5.myworkdayjobs.com/en-US/External",
            offline=True,
        )
        self.assertFalse(payload["send_or_submit"])
        self.assertEqual(payload["managed_upstream"]["repo"], UPSTREAM_REPO)
        self.assertEqual(payload["managed_upstream"]["ref"], UPSTREAM_REF)
        self.assertEqual(len(payload["jobs"]), 1)
        job = payload["jobs"][0]
        self.assertEqual(job["source"], "managed_workday")
        self.assertEqual(job["live_status"], "unverified")
        self.assertEqual(job["posting_date_precision"], "exact")
        self.assertTrue(job["source_url"].startswith("https://"))
        self.assertTrue(job["provenance"]["official"])

    def test_location_filter_is_honoured(self):
        payload = run_probe(
            provider="workday",
            company="Example Employer|https://example.wd5.myworkdayjobs.com/en-US/External",
            location="Amman",
            offline=True,
        )
        self.assertEqual(payload["jobs"], [])


class RunnerPresenceTests(unittest.TestCase):
    def test_node_bridge_is_versioned(self):
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "tools" / "career_ops_source_runner.mjs").is_file())


if __name__ == "__main__":
    unittest.main()
