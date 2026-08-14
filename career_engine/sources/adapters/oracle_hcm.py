"""Public Oracle Cloud HCM Candidate Experience adapter."""
from __future__ import annotations

import json
from urllib.parse import quote

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso, unknown
from ..provenance import provenance as make_provenance


def parse_identifier(value: str) -> tuple[str, str]:
    host, sep, site = value.strip().partition("|")
    host = host.removeprefix("https://").removeprefix("http://").rstrip("/")
    if not sep or not host or not site:
        raise SourceError("Oracle HCM identifier must be <host>|<siteNumber>")
    return host, site


class OracleHcmAdapter(SourceAdapter):
    source_id = "oracle_hcm"
    source_name = "Oracle Cloud HCM Candidate Experience"
    source_kind = "ats_api"
    official = True

    def search(self, *, company: str, location: str | None = None, limit: int = 10,
               fetch_full: bool = False, offline: bool = False) -> list[DiscoveryJob]:
        host, site = parse_identifier(company)
        requested = max(1, min(limit, 100))
        # Oracle applies the finder limit before client-side location filtering.
        # Enumerate a bounded wider page so a small requested result count does
        # not accidentally hide Saudi/GCC jobs behind global postings.
        finder = f"findReqs;siteNumber={site},limit={max(100, requested)},sortBy=POSTING_DATES_DESC"
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
               f"?onlyData=true&expand=requisitionList&finder={quote(finder, safe='')}")
        payload = self._load(url, "oracle-hcm-list.json", offline)
        items = (payload.get("items") or [{}])[0].get("requisitionList") or {}
        if isinstance(items, dict):
            items = items.get("items") or []
        jobs = [self._map(item, host, site) for item in items]
        if location:
            jobs = [job for job in jobs if location.lower() in job.location.lower()]
        jobs = jobs[:requested]
        if fetch_full:
            for job in jobs:
                try:
                    detail = self._detail(host, site, job.external_job_id, offline)
                    if detail:
                        job.description_html = "\n\n".join(str(detail.get(k) or "") for k in (
                            "ExternalDescriptionStr", "OrganizationDescriptionStr", "CorporateDescriptionStr"))
                        job.description_text = html_to_text(job.description_html)
                except SourceError:
                    pass
        return jobs

    def _load(self, url: str, fixture: str, offline: bool):
        if offline:
            with open(self._fixture_path(fixture), encoding="utf-8") as handle:
                return json.load(handle)
        return network.fetch_json(url)

    def _detail(self, host: str, site: str, job_id: str, offline: bool):
        finder = quote(f"ById;Id={job_id},siteNumber={site}", safe="")
        url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?onlyData=true&finder={finder}"
        payload = self._load(url, "oracle-hcm-detail.json", offline)
        return (payload.get("items") or [None])[0]

    def _map(self, item: dict, host: str, site: str) -> DiscoveryJob:
        job_id = str(item.get("Id") or item.get("id") or "")
        url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}"
        location = str(item.get("PrimaryLocation") or item.get("PrimaryLocationCountry") or "")
        return DiscoveryJob(
            adapter_id=self.source_id, company=host, role=str(item.get("Title") or ""),
            location=location, external_job_id=job_id, detail_url=url, application_url=url,
            posted=parse_iso(item.get("PostedDate"), "Oracle PostedDate") if item.get("PostedDate") else unknown(self.source_id),
            found_date=utc_today(), description_html=str(item.get("ShortDescriptionStr") or ""),
            description_text=html_to_text(str(item.get("ShortDescriptionStr") or "")),
            provenance=make_provenance(source_id=self.source_id, source_name=self.source_name,
                source_kind=self.source_kind, official=True,
                extracted_from="official Oracle recruitingCEJobRequisitions endpoint",
                detail_url=url, raw_id=job_id),
        )


def utc_today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
