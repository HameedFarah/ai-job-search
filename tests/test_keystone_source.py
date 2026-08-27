"""Keystone recruiter-board source regression tests."""

from __future__ import annotations

from career_engine.sources.adapters.keystone import KeystoneAdapter
from career_engine.sources.cli import build_adapter, run_probe
from career_engine.sources.registry import get_source


JOB_ID = "f6c0ea4a-a504-47b3-b160-6e00d93747a2"
JOB_URL = f"https://gokeystone.ai/jobs/{JOB_ID}"


def test_keystone_registry_is_nonofficial_partial_board() -> None:
    entry = get_source("keystone")
    assert entry["kind"] == "board_web"
    assert entry["priority"] == 2
    assert entry["auth"] == "none"
    assert entry["posting_date"] == "exact"
    assert entry["official"] is False
    assert entry["status"] == "partial"
    assert entry["manual_only"] is False
    assert "employer" in entry["blocked_reason"].lower()


def test_keystone_adapter_is_probe_runnable() -> None:
    adapter = build_adapter("keystone", offline=True)
    assert isinstance(adapter, KeystoneAdapter)
    assert adapter.official is False


def test_keystone_direct_job_probe_preserves_unverified_provenance() -> None:
    report = run_probe(
        adapter_id="keystone",
        company=JOB_URL,
        offline=True,
        limit=1,
        fetch_full=True,
    )
    assert report["send_or_submit"] is False
    assert report["offline_fixture"] is True
    assert report["summary"]["jobs_emitted"] == 1

    job = report["jobs"][0]
    assert job["external_job_id"] == JOB_ID
    assert job["source"] == "keystone"
    assert job["source_url"] == JOB_URL
    assert job["role"] == "QA/QC Lead - EPC / EPCC – Oil & Gas"
    assert job["location"] == "Dubai, United Arab Emirates"
    assert job["posting_date"].startswith("2026-08-19")
    assert job["posting_date_precision"] == "day"
    assert job["application_url"] == ""
    assert job["live_status"] == "unverified"
    assert job["live_verified_at"] == ""
    assert job["provenance"]["official"] is False
    assert "official employer/ATS verification required" in job["provenance"]["verification"]
    assert len(job["full_job_description"]) >= 80


def test_keystone_uuid_identifier_normalizes_to_public_job_url() -> None:
    adapter = KeystoneAdapter()
    assert adapter._detail_url(JOB_ID) == JOB_URL
    assert adapter._detail_url(JOB_URL + "?utm_source=test") == JOB_URL
    assert adapter._detail_url("https://example.com/jobs/" + JOB_ID) == ""


def test_keystone_board_link_extraction_is_bounded_and_deduped() -> None:
    adapter = KeystoneAdapter()
    page = (
        f'<a href="/jobs/{JOB_ID}">one</a>'
        f'<a href="https://www.gokeystone.ai/jobs/{JOB_ID}">duplicate</a>'
        '<a href="/jobs/1dd2dbea-ec25-45ac-bceb-4816cf803c0f">two</a>'
    )
    assert adapter._extract_detail_urls(page) == [
        JOB_URL,
        "https://gokeystone.ai/jobs/1dd2dbea-ec25-45ac-bceb-4816cf803c0f",
    ]
