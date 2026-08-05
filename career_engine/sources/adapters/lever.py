"""Lever Postings API adapter (public, unauthenticated).

- List: ``GET https://api.lever.co/v0/postings/{company}?mode=json&limit=N``

Posting date: ``createdAt`` (millisecond epoch, exact day). Verified live
against Lever's own demo board ``leverdemo`` on 2026-08-05. The API does not
return a company name, so the probe-supplied identifier is used as the display
name unless the posting embeds one.
"""

from __future__ import annotations

import json
from typing import Any

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_ms_epoch
from ..provenance import provenance as make_provenance

LIST_TEMPLATE = "https://api.lever.co/v0/postings/{company}?mode=json"


class LeverAdapter(SourceAdapter):
    source_id = "lever"
    source_name = "Lever Postings API"
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
            raise SourceError("Lever company identifier is required")
        url = f"{LIST_TEMPLATE.format(company=slug)}&limit={max(1, min(limit, 50))}"
        payload = self._load(url, offline)
        if not isinstance(payload, list):
            raise SourceError(f"Unexpected Lever response shape for {slug!r}")
        results: list[DiscoveryJob] = []
        for item in payload:
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
        raise SourceError("Lever detail lookup requires the full listing mode=json; use search()")

    def _load(self, url: str, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("lever-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.fetch_json(url)

    def _map_job(self, item: dict[str, Any], slug: str) -> DiscoveryJob:
        title = str(item.get("text") or item.get("title") or "").strip()
        company = str(item.get("company") or item.get("companyName") or "").strip() or slug
        categories = item.get("categories") or {}
        location = str(categories.get("location") or item.get("workplaceType") or "").strip()
        urls = item.get("urls") or {}
        detail_url = str(item.get("hostedUrl") or urls.get("host") or "").strip()
        apply_url = str(item.get("applyUrl") or urls.get("apply") or "").strip() or detail_url
        external_id = str(item.get("id") or "")
        description_parts: list[str] = []
        for key in ("description", "descriptionBody", "opening"):
            if item.get(key):
                description_parts.append(str(item[key]))
        lists = item.get("lists")
        if isinstance(lists, dict):
            for key in ("position", "qualifications", "responsibilities", "niceToHave"):
                values = lists.get(key)
                if isinstance(values, list) and values:
                    description_parts.append(f"{key}: " + "; ".join(str(v) for v in values))
        elif isinstance(lists, list):
            for entry in lists:
                if not isinstance(entry, dict):
                    continue
                heading = str(entry.get("text") or "").strip()
                content = entry.get("content")
                if isinstance(content, list):
                    description_parts.append(f"{heading}: " + "; ".join(str(v) for v in content))
                elif content:
                    description_parts.append(f"{heading}: {content}")
        description_html = "\n\n".join(part for part in description_parts if part.strip())
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=company,
            role=title,
            location=location,
            external_job_id=external_id,
            detail_url=detail_url,
            application_url=apply_url,
            posted=parse_ms_epoch(item.get("createdAt"), "Lever createdAt"),
            found_date=utc_today(),
            description_html=description_html,
            description_text=html_to_text(description_html),
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="Lever postings list (createdAt/description)",
                detail_url=detail_url,
                raw_id=external_id,
            ),
        )


def utc_today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
