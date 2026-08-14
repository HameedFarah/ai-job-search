"""SmartRecruiters public company postings adapter (public, unauthenticated).

- List: ``GET https://api.smartrecruiters.com/v1/companies/{company_id}/postings?limit=N``
- Detail: ``GET https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{uuid}``

Posting date: ``releasedDate`` (ISO timestamp, exact day). Verified live
against company id ``SmartRecruiters`` (8 postings) on 2026-08-05. Note the
API returns an empty ``content`` (totalFound=0) rather than a 404 for unknown
identifiers, so the probe reports ``empty`` explicitly instead of failing.

The list response contains no description; when ``fetch_full`` is enabled the
adapter fetches each posting's detail endpoint (bounded by ``limit``) to build
a complete job description for the central engine.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso
from ..provenance import provenance as make_provenance

COMPANY_TEMPLATE = "https://api.smartrecruiters.com/v1/companies/{company_id}"


class SmartRecruitersAdapter(SourceAdapter):
    source_id = "smartrecruiters"
    source_name = "SmartRecruiters Public Postings"
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
        company_id = company.strip()
        if not company_id:
            raise SourceError("SmartRecruiters company id is required")
        requested = max(1, min(limit, 100))
        query = f"&q={quote(location)}" if location else ""
        results: list[DiscoveryJob] = []
        offset = 0
        page_size = min(100, max(20, requested))
        while len(results) < requested and offset < 500:
            url = (
                f"{COMPANY_TEMPLATE.format(company_id=company_id)}/postings"
                f"?limit={page_size}&offset={offset}{query}"
            )
            payload = self._load_list(url, offline)
            content = payload.get("content", []) if isinstance(payload, dict) else []
            if not content:
                break
            for item in content:
                job = self._map_job(item, company_id)
                if location and location.lower() not in job.location.lower():
                    continue
                results.append(job)
                if len(results) >= requested:
                    break
            offset += len(content)
            total = int(payload.get("totalFound") or 0) if isinstance(payload, dict) else 0
            if offset >= total:
                break
        if fetch_full:
            fetched: list[DiscoveryJob] = []
            for job in results[:limit]:
                try:
                    detail = self._load_detail(job.external_job_id, company_id, offline)
                    job = self._augment(job, detail)
                except SourceError:
                    pass  # keep the list-level record; detail is best-effort
                fetched.append(job)
            results = fetched
        return results[:limit]

    def _load_list(self, url: str, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("smartrecruiters-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.fetch_json(url)

    def _load_detail(self, uuid: str, company_id: str, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("smartrecruiters-detail.json"), encoding="utf-8") as handle:
                return json.load(handle)
        url = f"{COMPANY_TEMPLATE.format(company_id=company_id)}/postings/{uuid}"
        return network.fetch_json(url)

    def _map_job(self, item: dict[str, Any], company_id: str) -> DiscoveryJob:
        name = str(item.get("name") or "").strip()
        location_obj = item.get("location") or {}
        full_location = str(location_obj.get("fullLocation") or "").strip()
        location_parts = [str(location_obj.get("city") or ""), str(location_obj.get("region") or "")]
        country = str(location_obj.get("country") or "")
        if country and country.isalpha():
            location_parts.append(country.upper())
        location = full_location or ", ".join(part for part in location_parts if part)
        external_id = str(item.get("uuid") or item.get("id") or "")
        ref = str(item.get("refNumber") or "")
        job_url = f"https://jobs.smartrecruiters.com/{company_id}/{external_id}"
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=company_id,
            role=name,
            location=location,
            external_job_id=external_id,
            detail_url=job_url,
            application_url=job_url,
            posted=parse_iso(item.get("releasedDate"), "SmartRecruiters releasedDate"),
            found_date=utc_today(),
            description_html="",
            description_text="",
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="SmartRecruiters postings list (releasedDate)",
                detail_url=job_url,
                raw_id=external_id,
            ),
            extra={"ref_number": ref, "fetch_full_required": True},
        )

    def _augment(self, job: DiscoveryJob, detail: dict[str, Any]) -> DiscoveryJob:
        job_ad = detail.get("jobAd") or {}
        sections = job_ad.get("sections") or {}
        description = sections.get("jobDescription", {})
        qualifications = sections.get("qualifications", {})
        parts = [
            str(description.get("text") or "").strip(),
            str(qualifications.get("text") or "").strip(),
        ]
        text = "\n\n".join(part for part in parts if part).strip()
        job.description_html = text
        job.description_text = html_to_text(text)
        job.application_url = str(detail.get("applyUrl") or job.application_url)
        job.detail_url = str(detail.get("postingUrl") or job.detail_url)
        job.extra["ref_number"] = str(detail.get("refNumber") or job.extra.get("ref_number", ""))
        job.extra.pop("fetch_full_required", None)
        return job


def utc_today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
