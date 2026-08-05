"""Greenhouse Job Board API adapter (public, unauthenticated).

Reference: https://developers.greenhouse.io/job-board.html

- List: ``GET /v1/boards/{board_token}/jobs?content=true&per_page=N``
- Detail: ``GET /v1/boards/{board_token}/jobs/{id}``

Posting date: ``first_published`` (ISO timestamp, exact day). Verified live
against GCC boards ``careem`` (25 jobs) and ``tamara`` (40 jobs) on
2026-08-05.
"""

from __future__ import annotations

import json
from typing import Any

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso
from ..provenance import provenance as make_provenance

LIST_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseAdapter(SourceAdapter):
    source_id = "greenhouse"
    source_name = "Greenhouse Job Board API"
    source_kind = "ats_api"
    official = True

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        token = company.strip()
        if not token:
            raise SourceError("Greenhouse board token (company identifier) is required")
        url = f"{LIST_TEMPLATE.format(token=token)}?content=true&per_page={max(1, min(limit, 100))}"
        payload = self._load(url, offline)
        jobs = payload.get("jobs", [])
        results: list[DiscoveryJob] = []
        for item in jobs:
            job = self._map_job(item, token)
            if location and location.lower() not in job.location.lower():
                continue
            results.append(job)
        return results[:limit]

    def fetch(
        self,
        external_job_id: str,
        *,
        token: str,
        detail_url: str = "",
        offline: bool = False,
    ) -> DiscoveryJob:
        url = f"{LIST_TEMPLATE.format(token=token)}/jobs/{external_job_id}"
        payload = self._load(url, offline, raw=True)
        return self._map_job(payload, token)

    def _load(self, url: str, offline: bool, raw: bool = False) -> Any:
        if offline:
            with open(self._fixture_path("greenhouse-list.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            return data if raw else data
        return network.fetch_json(url)

    def _map_job(self, item: dict[str, Any], token: str) -> DiscoveryJob:
        location_obj = item.get("location") or {}
        title = str(item.get("title") or item.get("name") or "").strip()
        company = str(item.get("company_name") or "").strip() or token
        detail_url = str(item.get("absolute_url") or "")
        external_id = str(item.get("id") or "")
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=company,
            role=title,
            location=str(location_obj.get("name") or "").strip(),
            external_job_id=external_id,
            detail_url=detail_url,
            application_url=detail_url,
            posted=parse_iso(item.get("first_published"), "Greenhouse first_published"),
            found_date=network_utc_today(),
            description_html=str(item.get("content") or ""),
            description_text=html_to_text(item.get("content")),
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="Greenhouse jobs list (first_published/content)",
                detail_url=detail_url,
                raw_id=external_id,
            ),
            extra={"requisition_id": str(item.get("requisition_id") or "")},
        )


def network_utc_today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
