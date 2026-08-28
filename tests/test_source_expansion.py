"""Job-source expansion policy, normalization and adapter tests."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from career_engine.sources.adapters.aggregators import (
    BraveSearchAdapter,
    CareerjetAdapter,
    JoobleAdapter,
)
from career_engine.sources.alerts import (
    AlertListing,
    SUPPORTED_ALERT_SOURCES,
    normalize_alert_listings,
)
from career_engine.sources.base import SourceError, SourceUnavailable
from career_engine.sources.cli import run_probe
from career_engine.sources.consultants import scan_consultants
from career_engine.sources.registry import get_source, runtime_source_status
from career_engine.sources.routing import decide_route


class SourceExpansionTests(unittest.TestCase):
    def test_consultant_scan_preserves_policy_and_outcomes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = scan_consultants(root=root, offline=True, limit=3)
        # WSP and Dar Al Omran remain manual because their authoritative pages
        # block the bounded client; Omrania is an explicit Egis duplicate. Six
        # active duplicate records are skipped, leaving 18 attempted sources.
        self.assertEqual(report["summary"]["active_records"], 26)
        self.assertEqual(report["summary"]["sources_skipped"], 6)
        self.assertEqual(report["summary"]["sources_attempted"], 20)
        self.assertFalse(report["send_or_submit"])
        self.assertGreaterEqual(len(report["jobs"]), 1)
        self.assertIn("workday", {job["adapter"] for job in report["jobs"]})
        self.assertIn("oracle_hcm", {job["adapter"] for job in report["jobs"]})
        self.assertTrue(all({"source_id", "source_name", "attempted", "status", "adapter", "jobs_fetched", "verified_authoritative", "error"} <= set(row) for row in report["sources"]))

    def test_missing_provider_keys_make_sources_unavailable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SourceUnavailable):
                BraveSearchAdapter().search(company="Design Manager")
            with self.assertRaises(SourceUnavailable):
                JoobleAdapter().search(company="Design Manager")
            with self.assertRaises(SourceUnavailable):
                CareerjetAdapter(
                    user_triggered=True,
                    user_ip="1.1.1.1",
                    user_agent="test-agent",
                ).search(company="Design Manager")

    def test_missing_key_probe_is_gracefully_unavailable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            report = run_probe(adapter_id="brave_search", company="Design Manager")
        self.assertFalse(report["send_or_submit"])
        self.assertEqual(report["jobs"], [])
        self.assertEqual(report["sources"][0]["status"], "unavailable")

    def test_runtime_status_does_not_expose_secret_values(self) -> None:
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "not-a-real-key"}, clear=True):
            rows = {row["source_id"]: row for row in runtime_source_status()}
        self.assertTrue(rows["brave_search"]["configured"])
        self.assertTrue(rows["brave_search"]["runnable"])
        self.assertNotIn("not-a-real-key", json.dumps(rows))

    @patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": "x"}, clear=True)
    @patch("career_engine.sources.adapters.aggregators.network.request_json")
    def test_brave_records_remain_unverified(self, request_json) -> None:
        request_json.return_value = {
            "web": {"results": [{
                "title": "Design Manager - Example",
                "url": "https://jobs.example.com/1",
                "description": "Role description",
                "profile": {"long_name": "Example"},
            }]}
        }
        data = BraveSearchAdapter().search(
            company="Design Manager", location="Saudi Arabia"
        )[0].to_scanner_job()
        self.assertEqual(data["live_status"], "unverified")
        self.assertEqual(data["application_url"], "")
        self.assertFalse(data["provenance"]["official"])
        self.assertTrue(data["provenance"]["verification"].startswith("discovery-only"))

    @patch.dict(os.environ, {"JOOBLE_API_KEY": "x"}, clear=True)
    @patch("career_engine.sources.adapters.aggregators.network.request_json")
    def test_jooble_records_remain_unverified(self, request_json) -> None:
        request_json.return_value = {"jobs": [{
            "id": "1", "title": "Project Director", "company": "Example",
            "location": "Riyadh", "snippet": "Description",
            "link": "https://jooble.org/jdp/1",
            "updated": "2026-08-06T10:00:00",
        }]}
        data = JoobleAdapter().search(company="Project Director")[0].to_scanner_job()
        self.assertEqual(data["posting_date"], "unknown")
        self.assertFalse(data["provenance"]["official"])
        self.assertEqual(data["application_url"], "")

    @patch.dict(os.environ, {"CAREERJET_API_KEY": "x"}, clear=True)
    def test_careerjet_requires_user_trigger_and_public_identity(self) -> None:
        with self.assertRaisesRegex(SourceError, "manual-only"):
            CareerjetAdapter().search(company="Design Manager")
        with self.assertRaisesRegex(SourceError, "actual IP"):
            CareerjetAdapter(user_triggered=True).search(company="Design Manager")
        with self.assertRaisesRegex(SourceError, "public IP"):
            CareerjetAdapter(
                user_triggered=True, user_ip="127.0.0.1", user_agent="test-agent"
            ).search(company="Design Manager")

    @patch.dict(os.environ, {"CAREERJET_API_KEY": "x"}, clear=True)
    @patch("career_engine.sources.adapters.aggregators.network.request_json")
    def test_careerjet_manual_result_is_discovery_only(self, request_json) -> None:
        request_json.return_value = {"type": "JOBS", "jobs": [{
            "title": "Design Manager", "company": "Example",
            "date": "Wed, 06 Aug 2026 19:13:43 GMT",
            "description": "Description", "locations": "Riyadh",
            "url": "https://jobviewtrack.com/v2/1",
        }]}
        job = CareerjetAdapter(
            user_triggered=True, user_ip="1.1.1.1", user_agent="test-agent"
        ).search(company="Design Manager")[0]
        self.assertEqual(job.posted.value, "2026-08-06")
        self.assertTrue(job.extra["manual_user_triggered"])
        self.assertFalse(job.provenance.official)
        self.assertEqual(job.application_url, "")

    @patch.dict(os.environ, {"JOOBLE_API_KEY": "secret-key"}, clear=True)
    @patch("career_engine.sources.adapters.aggregators.network.request_json")
    def test_jooble_error_does_not_chain_secret_bearing_url(self, request_json) -> None:
        request_json.side_effect = SourceError("https://jooble.org/api/secret-key")
        with self.assertRaises(SourceError) as caught:
            JoobleAdapter().search(company="Design Manager")
        self.assertEqual(str(caught.exception), "Jooble API request failed")
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("secret-key", str(caught.exception))

    @patch.dict(os.environ, {"CAREERJET_API_KEY": "secret-key"}, clear=True)
    @patch("career_engine.sources.adapters.aggregators.network.request_json")
    def test_careerjet_error_does_not_chain_user_identity(self, request_json) -> None:
        request_json.side_effect = SourceError("user_ip=1.1.1.1&user_agent=private-agent")
        with self.assertRaises(SourceError) as caught:
            CareerjetAdapter(
                user_triggered=True, user_ip="1.1.1.1", user_agent="private-agent"
            ).search(company="Design Manager")
        self.assertEqual(str(caught.exception), "Careerjet API request failed")
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("1.1.1.1", str(caught.exception))
        self.assertNotIn("private-agent", str(caught.exception))

    def test_alert_normalizers_cover_all_approved_boards(self) -> None:
        self.assertEqual(set(SUPPORTED_ALERT_SOURCES), {
            "linkedin_alerts", "bayt_alerts", "naukrigulf_alerts",
            "gulftalent_alerts", "indeed_alerts", "foundit_alerts",
            "gotogulf_alerts",
        })
        for source_id in SUPPORTED_ALERT_SOURCES:
            jobs = normalize_alert_listings(
                source_id=source_id, message_id="m1",
                received_at="2026-08-06T12:00:00+03:00",
                listings=[AlertListing(
                    company="Example", role="Design Manager",
                    location="Riyadh", url="https://example.com/job/1",
                )],
            )
            self.assertEqual(len(jobs), 1)
            self.assertFalse(jobs[0].provenance.official)
            self.assertEqual(jobs[0].application_url, "")

    def test_restricted_boards_are_denied_residential_fallback(self) -> None:
        urls = (
            "https://www.linkedin.com/jobs/view/1",
            "https://www.bayt.com/en/job/1",
            "https://www.naukrigulf.com/job/1",
            "https://www.gulftalent.com/job/1",
            "https://www.indeed.com/viewjob?jk=1",
            "https://www.foundit.ae/job/1",
            "https://www.gotogulf.com/job/1",
        )
        for url in urls:
            decision = decide_route(
                url, residential_allowlist={"linkedin.com", "bayt.com", "foundit.ae"},
                proxy_available=True,
            )
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.route, "denied")

    def test_allowlisted_employer_fails_closed_when_proxy_is_down(self) -> None:
        decision = decide_route(
            "https://careers.example.com/job/1",
            residential_allowlist={"example.com"}, proxy_available=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.route, "denied")
        self.assertTrue(decision.proxy_required)

    def test_allowlisted_employer_uses_residential_when_available(self) -> None:
        decision = decide_route(
            "https://careers.example.com/job/1",
            residential_allowlist={"example.com"}, proxy_available=True,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.route, "residential")

    def test_non_allowlisted_domain_uses_normal_vps_route(self) -> None:
        decision = decide_route(
            "https://jobs.other.com/1",
            residential_allowlist={"example.com"}, proxy_available=False,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.route, "vps")

    def test_employer_registry_has_40_unique_valid_entries(self) -> None:
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((
            root / "projects/job-automation/config/gcc-employers.v1.json"
        ).read_text(encoding="utf-8"))
        employers = payload["employers"]
        self.assertEqual(len(employers), 57)
        self.assertEqual(len({item["id"] for item in employers}), 57)
        self.assertEqual(payload["policy"]["residential_allowlist_enabled_domains"], [])
        for item in employers:
            self.assertTrue(item["name"])
            self.assertTrue(item["official_domains"])
            self.assertTrue(all(
                "." in domain and "://" not in domain
                for domain in item["official_domains"]
            ))
            self.assertFalse(item["residential_fallback_allowed"])

    def test_route_check_registry_does_not_auto_enable_employer_domains(self) -> None:
        root = Path(__file__).resolve().parents[1]
        from career_engine.sources.cli import run_route_check
        decision = run_route_check(
            "https://careers.neom.com/job/1",
            allowlist_file=str(root / "projects/job-automation/config/gcc-employers.v1.json"),
            proxy_available=True,
        )
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["route"], "vps")

    def test_source_result_timestamp_path_is_exercised(self) -> None:
        from career_engine.sources.cli import _source_result
        row = _source_result("brave_search", status="unavailable")
        self.assertIn("T", row.fetched_at)

    def test_registry_marks_aggregators_discovery_only(self) -> None:
        for source_id in ("brave_search", "jooble", "careerjet"):
            entry = get_source(source_id)
            self.assertFalse(entry["official"])
            self.assertEqual(entry["status"], "partial")

    def test_registry_preserves_historical_source_evidence_additively(self) -> None:
        self.assertIn(
            "first_published/updated_at",
            get_source("greenhouse")["notes"],
        )
        workable = get_source("workable")
        history = workable["probe_history"]
        self.assertEqual(len(history), 2)
        self.assertFalse(history[0]["verified"])
        self.assertTrue(history[1]["verified"])
        self.assertEqual(history[1]["companies"], ["qiddiya"])


if __name__ == "__main__":
    unittest.main()
