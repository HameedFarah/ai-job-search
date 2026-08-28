"""Focused tests for the six Saudi consultancy portals (HMA/Rwaq vacancy sources
and ASD/Advanced/Taiba/Madar portal-only routes).

Contract invariants asserted here:

- HMA and Rwaq are registered adapters and emit official-provenance jobs only
  from their public first-party endpoints; they never send or submit.
- Rwaq discovers the public Supabase credentials at runtime (no hard-coded key)
  and fails closed when discovery fails; it never invents records.
- Portal-only bookmarks (ASD, Advanced Consultancy Center, Taiba, Madar) are
  present in the canonical registry but are NOT scanned (route-only).
- The consultants scan distinguishes route-only vs vacancy sources and every
  emitted job carries official provenance with send_or_submit False.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from career_engine.sources.base import SourceError
from career_engine.sources.cli import build_adapter
from career_engine.sources.consultants import scan_consultants
from career_engine.sources.registry import capability_matrix, get_source, registry_payload

ROOT = Path(__file__).resolve().parents[1]

HMA_PAYLOAD = {
    "ok": True,
    "jobs": [
        {
            "id": "1",
            "title": "Senior Project Manager",
            "department": "PM",
            "city": "Riyadh",
            "description": "<p>Lead multidisciplinary delivery.</p>",
            "status": "مفتوحة",
        },
        {
            "id": "2",
            "title": "Receptionist",
            "department": "Admin",
            "city": "Jeddah",
            "description": "<p>Front desk.</p>",
            "status": "مفتوحة",
        },
        {
            "id": "3",
            "title": "Closed Role",
            "department": "X",
            "city": "Riyadh",
            "description": "<p>closed</p>",
            "status": "مغلقة",
        },
        {
            "id": "4",
            "title": "Unknown Status Role",
            "department": "X",
            "city": "Riyadh",
            "description": "<p>status missing</p>",
        },
    ],
    "count": 4,
}


class TestHmaSource(unittest.TestCase):
    def test_hma_adapter_is_registered(self) -> None:
        adapter = build_adapter("hma")
        assert adapter.source_id == "hma"
        assert adapter.official is True

    def test_hma_offline_returns_no_jobs(self) -> None:
        jobs = build_adapter("hma").search(
            company="https://hr.hma.sa/api/public-jobs", offline=True
        )
        assert jobs == []

    def test_hma_maps_open_jobs_filters_closed_and_emits_non_target_roles(self) -> None:
        with patch(
            "career_engine.sources.adapters.hma.network.fetch_json",
            return_value=HMA_PAYLOAD,
        ):
            jobs = build_adapter("hma").search(
                company="https://hr.hma.sa/api/public-jobs", offline=False
            )
        # Open roles kept (including a non-target role -> no portal-specific scoring);
        # the closed role is dropped so we never promote stale vacancies.
        assert {job.external_job_id for job in jobs} == {"1", "2"}
        for job in jobs:
            assert job.company == "HMA"
            assert job.provenance.official is True
            scanner = job.to_scanner_job(live_status="live")
            assert "send_or_submit" not in scanner
            assert scanner["live_status"] == "live"


class TestRwaqSource(unittest.TestCase):
    def test_rwaq_adapter_is_registered(self) -> None:
        adapter = build_adapter("rwaq")
        assert adapter.source_id == "rwaq"
        assert adapter.official is True

    def test_rwaq_offline_is_fail_closed_empty(self) -> None:
        jobs = build_adapter("rwaq").search(
            company="https://www.rwaqeng.com/careers", offline=True
        )
        assert jobs == []

    def test_rwaq_discovers_supabase_and_reads_public_jobs_only(self) -> None:
        html = '<html><body><script src="/static/app.js"></script></body></html>'
        js = 'const c = createClient("https://xyz.supabase.co","eyJabc.def.ghi");'
        jobs_payload = [
            {
                "id": 1,
                "job_number": 42,
                "title": "Design Manager",
                "location": "Riyadh",
                "status": "نشطة",
                "created_at": "2026-08-01T00:00:00",
            },
            {"id": 2, "job_number": 43, "title": "Closed Role", "status": "مغلقة"},
            {"id": 3, "job_number": 44, "title": "Unknown Status Role"},
        ]

        def fake_fetch_text(url: str, **_kwargs) -> str:
            if "app.js" in url:
                return js
            return html

        with patch(
            "career_engine.sources.adapters.rwaq.network.fetch_text",
            side_effect=fake_fetch_text,
        ) as fetch_text, patch(
            "career_engine.sources.adapters.rwaq.network.request_json",
            return_value=jobs_payload,
        ) as request_json:
            jobs = build_adapter("rwaq").search(
                company="https://www.rwaqeng.com/careers", offline=False
            )
        # HTML + one JS asset fetched; credentials discovered at runtime. Only
        # the first-party JS asset gets the narrow larger bound needed by the
        # current 4.9 MiB Rwaq application bundle.
        assert fetch_text.call_count == 2
        assert fetch_text.call_args_list[1].kwargs["max_bytes"] == 6 * 1024 * 1024
        # Only the public jobs table is queried, with the discovered anon key.
        call = request_json.call_args
        assert "/rest/v1/jobs" in call.args[0]
        assert call.kwargs["headers"]["apikey"] == "eyJabc.def.ghi"
        # Closed role dropped; active role kept with official provenance.
        assert len(jobs) == 1
        job = jobs[0]
        assert job.external_job_id == "42"
        assert job.location == "Riyadh"
        assert "job42" in job.detail_url
        assert job.provenance.official is True
        assert str(job.posted.value) == "2026-08-01"

    def test_rwaq_fails_closed_when_credentials_undiscoverable(self) -> None:
        html = '<html><body><script src="/static/app.js"></script></body></html>'
        js = "const c = createClient(); // no supabase url/key here"

        def fake_fetch_text(url: str, **_kwargs) -> str:
            if "app.js" in url:
                return js
            return html

        with patch(
            "career_engine.sources.adapters.rwaq.network.fetch_text",
            side_effect=fake_fetch_text,
        ):
            try:
                build_adapter("rwaq").search(
                    company="https://www.rwaqeng.com/careers", offline=False
                )
            except SourceError as exc:
                assert "failing closed" in str(exc)
            else:
                raise AssertionError("Rwaq must fail closed when credentials are undiscoverable")


class TestConsultantsPortalRoute(unittest.TestCase):
    def test_scan_includes_vacancy_sources_and_excludes_portal_only(self) -> None:
        report = scan_consultants(root=ROOT, offline=True, limit=3)
        source_ids = {s["source_id"] for s in report["sources"]}
        # Vacancy sources are scanned.
        assert "hma" in source_ids
        assert "rwaq" in source_ids
        # Portal-only routes are present in the registry but never scanned.
        for portal_only in (
            "asd-architects",
            "advanced-consultancy-center",
            "taiba-consulting",
            "madar-al-jazeera",
        ):
            assert portal_only not in source_ids
        # Every emitted job is official and the report never sends/submits.
        assert report["send_or_submit"] is False
        assert report["summary"]["active_records"] == 26
        for job in report["jobs"]:
            assert job["provenance"]["official"] is True
            assert "send_or_submit" not in job


class TestSourceRegistry(unittest.TestCase):
    def test_registry_includes_hma_and_rwaq_without_hardcoded_keys(self) -> None:
        hma = get_source("hma")
        rwaq = get_source("rwaq")
        for entry in (hma, rwaq):
            assert entry["official"] is True
            assert entry["status"] == "active"
            assert entry["auth"] == "none"
        # No Supabase project identity or anon key is baked into source config.
        assert "eyJ" not in rwaq["notes"]
        assert "eyJ" not in rwaq["base_url"]

    def test_capability_matrix_lists_new_sources(self) -> None:
        ids = {row["source_id"] for row in capability_matrix()}
        assert {"hma", "rwaq"} <= ids

    def test_registry_payload_declares_no_send(self) -> None:
        payload = registry_payload()
        assert payload["no_send_policy"] is True
