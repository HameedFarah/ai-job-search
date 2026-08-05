"""Ashby Public Posting API adapter (public, unauthenticated).

- List: ``GET https://api.ashbyhq.com/posting-api/job-board/{company}``

Posting date: ``publishedAt`` (ISO timestamp with milliseconds, exact day).
Verified live against ``ramp`` (121 jobs), ``linear`` (25), ``notion`` (117),
``plaid`` (107) and ``opensea`` (1) on 2026-08-05. No GCC/KSA board
identifier has been confirmed yet; the adapter accepts any public board slug.
"""

from __future__ import annotations

import json
from typing import Any

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso
from ..provenance import provenance as make_provenance

LIST_TEMPLATE = "https://api.ashbyhq.com/posting-api/job-board/{company}"


class AshbyAdapter(SourceAdapter):
    source_id = "ashby"
    source_name = "Ashby Public Posting API"
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
        slug = company.strip()
        if not slug:
            raise SourceError("Ashby board identifier is required")
        url = f"{LIST_TEMPLATE.format(company=slug)}"
        payload = self._load(url, offline)
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        results: list[DiscoveryJob] = []
        for item in jobs:
            job = self._map_job(item, slug)
            if location and location.lower() not in job.location.lower():
                continue
            results.append(job)
        return results[:limit]

    def fetch(
        self,
        external_job_id: str,
        *,
        token: str = "",
        detail_url: str = "",
        offline: bool = False,
    ) -> DiscoveryJob:
        raise SourceError("Ashby detail lookup is not required; the posting API returns full records")

    def _load(self, url: str, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("ashby-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.fetch_json(url)

    def _map_job(self, item: dict[str, Any], slug: str) -> DiscoveryJob:
        title = str(item.get("title") or "").strip()
        location = str(item.get("location") or "").strip()
        detail_url = str(item.get("jobUrl") or "").strip()
        apply_url = str(item.get("applyUrl") or "").strip() or detail_url
        external_id = str(item.get("id") or "")
        published = parse_iso(item.get("publishedAt"), "Ashby publishedAt")
        description_html = str(item.get("descriptionHtml") or "")
        description_text = str(item.get("descriptionPlain") or "").strip() or html_to_text(description_html)
        department = (item.get("department") or {})
        team = (item.get("team") or {})
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=slug,
            role=title,
            location=location,
            external_job_id=external_id,
            detail_url=detail_url,
            application_url=apply_url,
            posted=published,
            found_date=utc_today(),
            description_html=description_html,
            description_text=description_text,
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="Ashby posting API (publishedAt/description)",
                detail_url=detail_url,
                raw_id=external_id,
            ),
            extra={
                "department": department.get("name", "") if isinstance(department, dict) else "",
                "team": team.get("name", "") if isinstance(team, dict) else "",
                "employment_type": str(item.get("employmentType") or ""),
                "is_remote": bool(item.get("isRemote")),
                "workplace_type": str(item.get("workplaceType") or ""),
            },
        )


def utc_today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
