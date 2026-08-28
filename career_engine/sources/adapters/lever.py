"""Lever Postings API adapter (public, unauthenticated).

Uses the documented tenant-scoped public Postings API with ``skip``/``limit``
pagination on both Lever's global and EU public instances. Posting date comes
from ``createdAt`` (millisecond epoch, exact day). Live GCC boards verified on
2026-08-27 include Aldar Properties (``aldar``) and Flow (``flowlife``).

The API does not reliably return the employer display name, so the adapter
retains the tenant slug; the canonical employer registry upgrades that identity
before CareerTracker ingestion.
"""

from __future__ import annotations

import json
from typing import Any

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_ms_epoch
from ..lever_tenants import normalize_tenant
from ..provenance import provenance as make_provenance

PAGE_SIZE = 100


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
        tenant = normalize_tenant(company)
        requested = max(1, min(int(limit), 5000))
        results: list[DiscoveryJob] = []
        skip = 0
        while len(results) < requested:
            page_limit = min(PAGE_SIZE, requested)
            url = f"{tenant.api_url}?mode=json&skip={skip}&limit={page_limit}"
            payload = self._load(url, offline)
            if not isinstance(payload, list):
                raise SourceError(f"Unexpected Lever response shape for {tenant.slug!r}")
            if not payload:
                break
            for item in payload:
                job = self._map_job(item, tenant.slug, tenant.instance)
                if location and location.lower() not in job.location.lower():
                    continue
                results.append(job)
                if len(results) >= requested:
                    break
            skip += len(payload)
            if offline or len(payload) < page_limit:
                break
        return results[:requested]

    def fetch(
        self,
        external_job_id: str,
        *,
        token: str = "",
        detail_url: str = "",
        offline: bool = False,
    ) -> DiscoveryJob:
        raise SourceError("Lever detail lookup requires the full listing mode=json; use search()")

    @staticmethod
    def _all_locations(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _load(self, url: str, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("lever-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.fetch_json(url)

    def _map_job(self, item: dict[str, Any], slug: str, instance: str = "global") -> DiscoveryJob:
        title = str(item.get("text") or item.get("title") or "").strip()
        company = str(item.get("company") or item.get("companyName") or "").strip() or slug
        categories = item.get("categories") or {}
        location = str(categories.get("location") or item.get("workplaceType") or "").strip()
        urls = item.get("urls") or {}
        detail_url = str(item.get("hostedUrl") or urls.get("host") or "").strip()
        apply_url = str(item.get("applyUrl") or urls.get("apply") or "").strip() or detail_url
        external_id = str(item.get("id") or "")
        description_parts: list[str] = []
        seen_description_parts: set[str] = set()

        def append_description_part(value: Any) -> None:
            raw = str(value or "").strip()
            if not raw:
                return
            fingerprint = " ".join(html_to_text(raw).lower().split())
            if not fingerprint or fingerprint in seen_description_parts:
                return
            seen_description_parts.add(fingerprint)
            description_parts.append(raw)

        for key in ("description", "descriptionBody", "opening"):
            append_description_part(item.get(key))
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
            extra={
                "tenant": slug,
                "lever_instance": instance,
                "country": str(item.get("country") or "").strip(),
                "all_locations": self._all_locations(categories.get("allLocations")),
            },
        )


def utc_today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
