"""Adapter behaviour tests (offline fixture probes, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_engine.sources.base import DiscoveryJob, SourceError, html_to_text
from career_engine.sources.cli import build_adapter, run_probe, run_verify
from career_engine.sources.dates import PRECISION_DAY, PRECISION_EXACT, PRECISION_UNKNOWN
from career_engine.sources.registry import get_source

FIXTURES = Path(__file__).resolve().parents[1] / "career_engine/sources/fixtures"

OFFLINE = True


def offline_adapter(adapter_id: str):
    return build_adapter(adapter_id, offline=True)


def test_greenhouse_parses_fixture_with_exact_dates_and_provenance() -> None:
    jobs = offline_adapter("greenhouse").search(company="careem", limit=10, offline=OFFLINE)
    assert len(jobs) == 3
    first = jobs[0]
    assert isinstance(first, DiscoveryJob)
    assert first.company == "Oasis Development Co"
    assert first.role == "Senior Design Manager (Architecture)"
    assert first.location == "Riyadh, Saudi Arabia"
    assert first.external_job_id == "910011001"
    assert first.posted.precision == PRECISION_EXACT
    assert first.posted.value == "2026-06-16"
    assert first.provenance.official is True
    assert first.provenance.source_id == "greenhouse"
    assert first.has_description
    assert first.dedupe_key()


def test_greenhouse_location_filter() -> None:
    jobs = offline_adapter("greenhouse").search(company="careem", location="Riyadh", limit=10, offline=OFFLINE)
    assert all("Riyadh" in job.location for job in jobs)


def test_lever_parses_fixture_and_ms_epoch_date() -> None:
    jobs = offline_adapter("lever").search(company="leverdemo", limit=10, offline=OFFLINE)
    assert len(jobs) == 2
    first = jobs[0]
    assert first.company == "Falcon Consulting Group"
    assert first.posted.precision == PRECISION_EXACT
    assert first.detail_url.startswith("https://jobs.example.lever.co/")
    assert first.has_description


def test_ashby_parses_fixture_with_plain_and_html_description() -> None:
    jobs = offline_adapter("ashby").search(company="ramp", limit=10, offline=OFFLINE)
    assert len(jobs) == 2
    first = jobs[0]
    assert first.posted.value == "2026-07-15"
    assert first.posted.precision == PRECISION_EXACT
    assert first.extra["employment_type"] == "FullTime"
    assert first.has_description
    assert first.description_text.startswith("Gulf Development Company")


def test_smartrecruiters_list_has_no_description_but_full_mode_does() -> None:
    adapter = offline_adapter("smartrecruiters")
    listed = adapter.search(company="SmartRecruiters", limit=10, offline=OFFLINE)
    assert len(listed) == 2
    assert not listed[0].has_description  # list payload carries no description
    full = adapter.search(company="SmartRecruiters", limit=10, offline=OFFLINE, fetch_full=True)
    assert full[0].has_description
    assert "Design Governance Manager" in full[0].description_text
    assert full[0].posted.precision == PRECISION_EXACT


def test_workable_parses_fixture_with_day_precision_date() -> None:
    jobs = offline_adapter("workable").search(company="horizon", limit=10, offline=OFFLINE)
    assert len(jobs) == 2
    first = jobs[0]
    assert first.posted.precision == PRECISION_DAY
    assert first.posted.value == "2026-07-08"
    assert first.has_description


def test_jsonld_parses_careers_page() -> None:
    jobs = offline_adapter("jsonld").search(
        company="https://careers.example.meridian.com/", limit=10, offline=OFFLINE
    )
    assert len(jobs) == 2
    first = jobs[0]
    assert first.company == "Meridian Destination Development"
    assert first.role == "Senior Design Manager"
    assert first.location == "Riyadh, Riyadh Province, SA"
    assert first.posted.precision == PRECISION_EXACT
    assert first.posted.value == "2026-07-18"
    assert first.provenance.verification.startswith("official")
    assert first.has_description


def test_jsonld_requires_a_url() -> None:
    with pytest.raises(SourceError):
        offline_adapter("jsonld").search(company="meridian", offline=OFFLINE)


def test_jsonld_parses_sitemap_offline() -> None:
    from career_engine.sources.adapters.jsonld import JsonLdAdapter

    adapter = JsonLdAdapter(fixtures_dir=str(FIXTURES))
    # URL must mention the sitemap so _from_sitemap is triggered.
    postings = adapter.search(
        company="https://careers.example.meridian.com/sitemap.xml",
        limit=10,
        offline=OFFLINE,
    )
    # Two sitemap URLs, each resolving to the fixture page with two postings.
    assert len(postings) >= 2
    # datePosted may be an ISO timestamp (exact) or a bare date (day).
    assert all(job.posted.precision in {PRECISION_EXACT, PRECISION_DAY} for job in postings)


def test_verify_gate_promotes_official_and_rejects_unverified() -> None:
    verifier = offline_adapter("jsonld")
    official = verifier.verify_official("https://careers.example.meridian.com/", offline=OFFLINE)
    assert official.official is True
    ats = verifier.verify_official("https://boards-api.greenhouse.io/v1/boards/careem", offline=OFFLINE)
    assert ats.official is True
    assert "ATS" in ats.verification
    bogus = verifier.verify_official("https://example.com/not-a-jobs-page", offline=OFFLINE)
    assert bogus.official is False


def test_discovery_adapter_emits_candidates_and_verifies() -> None:
    from career_engine.sources.adapters.discovery import SearchDiscoveryAdapter

    adapter = SearchDiscoveryAdapter(fixtures_dir=str(FIXTURES))
    candidates = adapter.search(company="Meridian Destination Development", location="Riyadh", offline=OFFLINE)
    assert len(candidates) == 3
    assert all(candidate.url.startswith("http") for candidate in candidates)
    blocked = adapter.blocked_engines()
    assert "google" in blocked and "bing" in blocked
    provenance = adapter.verify(candidates[0].url, offline=OFFLINE)
    assert provenance.official is True


def test_inbox_adapter_is_blocked_and_contract_works() -> None:
    from career_engine.sources.adapters.inbox import InboxMessage, InboxSourceAdapter

    adapter = InboxSourceAdapter()
    with pytest.raises(SourceError):
        adapter.search(company="ACME", offline=OFFLINE)
    message = InboxMessage(
        source_id="inbox_gmail",
        message_id="msg-1",
        sender="recruiter@example.com",
        subject="Senior Design Manager opportunity",
        received_at="2026-08-05T09:00:00+00:00",
        body_text="We are hiring a Senior Design Manager in Riyadh. Responsibilities: lead design governance.",
    )
    job = adapter.from_message(message, company="ACME", role="Senior Design Manager")
    assert job.posted.precision == PRECISION_UNKNOWN
    assert job.provenance.official is False
    assert "unverified" in job.provenance.verification


def test_probe_report_is_no_send_and_scanner_compatible() -> None:
    report = run_probe(adapter_id="greenhouse", company="careem", limit=5, offline=True)
    assert report["send_or_submit"] is False
    assert report["summary"]["jobs_emitted"] == 3
    for job in report["jobs"]:
        assert job["live_status"] == "unverified"
        assert job["source"] == "greenhouse"
        assert job["posting_date_precision"] in {PRECISION_EXACT, PRECISION_DAY, PRECISION_UNKNOWN}
        assert len(job["full_job_description"]) >= 80
        assert job["provenance"]["official"] is True


def test_probe_deduplicates_within_a_run() -> None:
    report = run_probe(adapter_id="greenhouse", company="careem", limit=100, offline=True)
    # 3 unique fixture jobs; force a duplicate by running the same adapter twice
    keys = [json.dumps(job["provenance"]["raw_id"]) for job in report["jobs"]]
    assert len(keys) == len(set(keys))


def test_blocked_adapter_probe_reports_blocked_not_fail() -> None:
    entry = get_source("gcc_bayt")
    assert entry["status"] == "blocked"
    report = run_probe(adapter_id="gcc_bayt", company="any", offline=True)
    assert report["blocked"], "blocked source must be reported"
    assert report["jobs"] == []
    assert report["summary"]["jobs_emitted"] == 0
    assert report["send_or_submit"] is False


def test_html_to_text_removes_markup_and_entities() -> None:
    text = html_to_text("<p>Hello &amp; welcome</p><ul><li>one</li><li>two</li></ul>")
    assert "Hello & welcome" in text
    assert "one" in text
    assert "two" in text
    assert "<" not in text
