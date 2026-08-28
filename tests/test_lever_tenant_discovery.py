from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from career_engine.sources.adapters.lever import LeverAdapter
from career_engine.sources.base import SourceError
from career_engine.sources.cli import run_lever_discovery
from career_engine.sources.lever_tenants import (
    TenantCandidate,
    build_search_queries,
    candidates_from_urls,
    load_registry_candidates,
    normalize_tenant,
    validate_candidate,
)

ROOT = Path(__file__).resolve().parents[1]


def _posting(job_id: str, title: str, location: str, *, country: str = "SA") -> dict:
    return {
        "id": job_id,
        "text": title,
        "categories": {"location": location, "allLocations": [location]},
        "createdAt": 1785000000000,
        "description": "Lead multidisciplinary design and construction delivery across complex projects. " * 3,
        "hostedUrl": f"https://jobs.lever.co/example/{job_id}",
        "applyUrl": f"https://jobs.lever.co/example/{job_id}/apply",
        "country": country,
    }


def test_normalize_tenant_supports_global_eu_and_posting_urls() -> None:
    assert normalize_tenant("aldar").identifier == "aldar"
    assert normalize_tenant("https://jobs.lever.co/aldar").identifier == "aldar"
    assert normalize_tenant("https://jobs.lever.co/aldar/1234").identifier == "aldar"
    assert normalize_tenant("https://api.lever.co/v0/postings/aldar?mode=json").identifier == "aldar"
    assert normalize_tenant("eu:example").identifier == "eu:example"
    assert normalize_tenant("https://jobs.eu.lever.co/example/1234").identifier == "eu:example"
    assert normalize_tenant("https://api.eu.lever.co/v0/postings/example").identifier == "eu:example"


def test_normalize_tenant_rejects_raw_posting_uuid() -> None:
    with pytest.raises(SourceError, match="posting UUID"):
        normalize_tenant("f6c0ea4a-a504-47b3-b160-6e00d93747a2")


def test_candidate_url_discovery_is_not_identity_verification() -> None:
    rows = candidates_from_urls(
        [
            "https://jobs.lever.co/flowlife/abc",
            "https://jobs.lever.co/flowlife/def",
            "https://example.com/not-lever",
        ],
        discovery_source="search",
    )
    assert len(rows) == 1
    assert rows[0].tenant.slug == "flowlife"
    assert rows[0].identity_verified is False


def test_registry_is_the_durable_tenant_mapping_authority() -> None:
    rows = {row.tenant.slug: row for row in load_registry_candidates(ROOT)}
    assert rows["aldar"].employer_name == "Aldar Properties"
    assert rows["aldar"].market == "AE"
    assert rows["aldar"].identity_verified is True
    assert rows["flowlife"].employer_name == "Flow"
    assert rows["flowlife"].market == "SA"
    assert rows["flowlife"].identity_verified is True


def test_search_queries_reuse_existing_taxonomy() -> None:
    queries = build_search_queries(ROOT, max_queries=200)
    assert any('site:jobs.lever.co "riyadh"' in query.lower() for query in queries)
    assert any('"design manager"' in query.lower() for query in queries)
    assert any('site:jobs.eu.lever.co' in query.lower() for query in queries)


def test_lever_query_budget_interleaves_geo_and_role_queries() -> None:
    # Regression: the default 24-query budget must not exhaust geography-only
    # queries before role-aware tenant discovery runs. All three query classes
    # must be present within the default budget.
    queries = build_search_queries(ROOT, max_queries=24)
    assert len(queries) == 24
    global_geo = [
        q for q in queries
        if "jobs.lever.co" in q.lower()
        and "jobs.eu.lever.co" not in q.lower()
        and "(" not in q
    ]
    eu_geo = [q for q in queries if "jobs.eu.lever.co" in q.lower()]
    role_aware = [q for q in queries if "(" in q]
    assert global_geo, "global geography discovery must be present within default budget"
    assert eu_geo, "EU geography discovery must be present within default budget"
    assert role_aware, "role-aware tenant discovery must not be starved within default budget"


def test_lever_adapter_paginates_with_skip_and_preserves_official_provenance() -> None:
    page1 = [_posting(str(index), f"Project Manager {index}", "Riyadh") for index in range(100)]
    page2 = [_posting(str(index), f"Project Director {index}", "Dubai", country="AE") for index in range(100, 125)]
    with patch(
        "career_engine.sources.adapters.lever.network.fetch_json",
        side_effect=[page1, page2],
    ) as fetch:
        jobs = LeverAdapter().search(company="aldar", limit=125, fetch_full=True)
    assert len(jobs) == 125
    assert fetch.call_count == 2
    assert "skip=0" in fetch.call_args_list[0].args[0]
    assert "limit=100" in fetch.call_args_list[0].args[0]
    assert "skip=100" in fetch.call_args_list[1].args[0]
    scanner = jobs[-1].to_scanner_job(live_status="live")
    assert scanner["external_job_id"] == "124"
    assert scanner["provenance"]["official"] is True
    # The no-send contract lives at the report level, not on individual job
    # records; a scanner job must never carry a send/submission flag.
    assert "send_or_submit" not in scanner
    assert scanner["live_status"] == "live"
    assert scanner["live_verified_at"]
    # Real report-level contract: DiscoveryReport always carries send_or_submit
    # False at both the top level and the summary, independent of job count.
    from career_engine.sources.base import DiscoveryReport

    report = DiscoveryReport(adapter="lever")
    report.jobs.append(scanner)
    payload = report.to_data()
    assert payload["send_or_submit"] is False
    assert payload["summary"]["send_or_submit"] is False


def test_lever_adapter_deduplicates_equivalent_description_fields() -> None:
    item = _posting("1", "Project Director", "Riyadh")
    item["descriptionBody"] = item["description"]
    job = LeverAdapter()._map_job(item, "example")
    sentence = "Lead multidisciplinary design and construction delivery across complex projects."
    assert job.description_text.count(sentence) == 3


def test_lever_adapter_normalizes_all_locations_string_or_list() -> None:
    item = _posting("1", "Project Director", "Riyadh")
    item["categories"]["allLocations"] = "Riyadh"
    assert LeverAdapter()._map_job(item, "example").extra["all_locations"] == ["Riyadh"]
    item["categories"]["allLocations"] = ["Riyadh", "Dubai"]
    assert LeverAdapter()._map_job(item, "example").extra["all_locations"] == ["Riyadh", "Dubai"]


def test_lever_adapter_uses_eu_public_instance() -> None:
    with patch("career_engine.sources.adapters.lever.network.fetch_json", return_value=[]) as fetch:
        LeverAdapter().search(company="eu:example", limit=10)
    assert fetch.call_args.args[0].startswith("https://api.eu.lever.co/v0/postings/example")


def test_validate_candidate_queries_api_but_rejects_unverified_identity() -> None:
    unknown = TenantCandidate(
        tenant=normalize_tenant("search-found"),
        discovery_source="search",
        identity_verified=False,
    )
    with patch.object(LeverAdapter, "search", return_value=[
        LeverAdapter()._map_job(_posting("1", "Project Director", "Riyadh"), "search-found")
    ]) as search:
        result = validate_candidate(unknown, root=ROOT, job_limit=100)
    assert search.called
    assert result["api_verified"] is True
    assert result["active"] is False
    assert result["reason"] == "credible_employer_identity_not_verified"
    assert result["relevant_job_records"] == []


def test_run_lever_discovery_emits_only_registry_verified_target_jobs() -> None:
    def fake_validate(candidate, *, root, job_limit):
        if candidate.tenant.slug == "aldar":
            job = LeverAdapter()._map_job(_posting("a1", "Executive Director - Design Management", "Abu Dhabi", country="AE"), "aldar")
            return {
                **candidate.to_data(),
                "api_verified": True,
                "verified": True,
                "active": True,
                "reason": "",
                "jobs": 47,
                "gcc_jobs": 47,
                "relevant_jobs": 1,
                "relevant_job_records": [job],
            }
        if candidate.tenant.slug == "flowlife":
            job = LeverAdapter()._map_job(_posting("f1", "Sr. Construction Manager", "Riyadh"), "flowlife")
            return {
                **candidate.to_data(),
                "api_verified": True,
                "verified": True,
                "active": True,
                "reason": "",
                "jobs": 43,
                "gcc_jobs": 15,
                "relevant_jobs": 1,
                "relevant_job_records": [job],
            }
        return {
            **candidate.to_data(),
            "api_verified": True,
            "verified": False,
            "active": False,
            "reason": "credible_employer_identity_not_verified",
            "jobs": 5,
            "gcc_jobs": 2,
            "relevant_jobs": 0,
            "relevant_job_records": [],
        }

    with patch("career_engine.sources.lever_tenants.validate_candidate", side_effect=fake_validate):
        report = run_lever_discovery(
            root=ROOT,
            candidate_urls=["https://jobs.lever.co/search-found/abc"],
            include_search=False,
        )
    assert report["send_or_submit"] is False
    assert report["summary"]["active_identity_verified_tenants"] == 2
    assert report["summary"]["published_vacancies"] == 90
    assert report["summary"]["relevant_jobs_emitted"] == 2
    assert {job["company"] for job in report["jobs"]} == {"Aldar Properties", "Flow"}
    assert all(job["provenance"]["official"] for job in report["jobs"])
    assert all(job["live_status"] == "live" for job in report["jobs"])
