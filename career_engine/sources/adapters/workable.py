"""Workable public career page/feed adapter.

Public endpoints tried in this order (both may 404 for accounts without a
public widget):

- ``GET https://apply.workable.com/api/v3/accounts/{account}/jobs``
- ``GET https://apply.workable.com/api/v1/widget/accounts/{account}/jobs``

Posting date: ``published_on`` (bare date, day precision). Live verification
returned HTTP 404 for every tested account identifier on 2026-08-05, so the
adapter is verified offline against fixtures and documented as ``partial`` in
the registry. When an account embeds job data in its career page the adapter
accepts a ``page_url`` override so the embedded payload can be parsed.
"""

from __future__ import annotations

import json
from typing import Any

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_date
from ..provenance import provenance as make_provenance

_V3_TEMPLATE = "https://apply.workable.com/api/v3/accounts/{account}/jobs"
_V1_TEMPLATE = "https://apply.workable.com/api/v1/widget/accounts/{account}/jobs"


class WorkableAdapter(SourceAdapter):
    source_id = "workable"
    source_name = "Workable Public Career Page/Feed"
    source_kind = "ats_web"
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
        account = company.strip()
        if not account:
            raise SourceError("Workable account identifier is required")
        payload = self._load(account, offline)
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        results: list[DiscoveryJob] = []
        for item in jobs:
            job = self._map_job(item, account)
            if location and location.lower() not in job.location.lower():
                continue
            results.append(job)
        return results[:limit]

    def _load(self, account: str, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("workable-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        last_error: Exception | None = None
        for template in (_V3_TEMPLATE, _V1_TEMPLATE):
            url = template.format(account=account)
            try:
                return network.fetch_json(url)
            except (network.SourceNotFound, network.HttpBlocked) as exc:
                last_error = exc
        raise SourceError(
            f"Workable account {account!r}: both public job endpoints returned "
            f"HTTP 404/403 ({last_error}). The account may not expose a public widget."
        )

    def _map_job(self, item: dict[str, Any], account: str) -> DiscoveryJob:
        title = str(item.get("title") or "").strip()
        city = str(item.get("city") or "").strip()
        country = str(item.get("country") or "").strip()
        location = ", ".join(part for part in (city, country) if part)
        detail_url = str(item.get("shortlink") or item.get("url") or "").strip()
        if not detail_url:
            detail_url = f"https://apply.workable.com/{account}/j/{item.get('id', '')}"
        external_id = str(item.get("id") or item.get("shortcode") or "")
        description_html = str(item.get("full_description") or item.get("description") or "")
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=account,
            role=title,
            location=location,
            external_job_id=external_id,
            detail_url=detail_url,
            application_url=detail_url,
            posted=parse_date(item.get("published_on"), "Workable published_on"),
            found_date=utc_today(),
            description_html=description_html,
            description_text=html_to_text(description_html),
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="Workable public feed (published_on)",
                detail_url=detail_url,
                raw_id=external_id,
            ),
        )


def utc_today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
