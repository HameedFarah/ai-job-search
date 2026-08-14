"""Public Workday CXS job-search adapter.

Workday career sites expose an unauthenticated JSON search endpoint.  The
tenant/site are taken from an already verified official career URL; callers
must not derive them from discovery results.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso, unknown
from ..provenance import provenance as make_provenance


class WorkdayAdapter(SourceAdapter):
    source_id = "workday"
    source_name = "Workday public CXS jobs"
    source_kind = "ats_api"
    official = True

    def search(self, *, company: str, location: str | None = None,
               limit: int = 10, fetch_full: bool = False,
               offline: bool = False) -> list[DiscoveryJob]:
        parsed = urlsplit(company if "://" in company else f"https://{company}")
        parts = [p for p in parsed.path.split("/") if p]
        if not parsed.netloc or len(parts) < 1:
            raise SourceError("Workday official career URL must include tenant and site")
        tenant = parsed.netloc.split(".")[0]
        site = parts[0]
        url = f"https://{parsed.netloc}/wday/cxs/{tenant}/{site}/jobs"
        requested = max(1, min(limit, 100))
        jobs: list[DiscoveryJob] = []
        query = parse_qs(parsed.query)
        applied_facets: dict[str, list[str]] = {}
        country_ids = [value for value in query.get("locationCountry", []) if value]
        if country_ids:
            applied_facets["locationCountry"] = country_ids

        # Workday caps CXS search pages at 20.  A requested result limit is the
        # number of matching jobs we want back, not a global page-size request.
        # Use an official country facet when the verified career URL provides
        # one; otherwise try a location search and then bounded global pages.
        search_terms = [""] if applied_facets else [location or ""]
        if location and not applied_facets:
            search_terms.append("")
        seen_paths: set[str] = set()
        for search_text in search_terms:
            offset = 0
            while len(jobs) < requested and offset < 500:
                payload = self._load_page(
                    url,
                    offset=offset,
                    search_text=search_text,
                    applied_facets=applied_facets,
                    offline=offline,
                )
                postings = payload.get("jobPostings") or []
                if not postings:
                    break
                for item in postings:
                    detail = str(item.get("externalPath") or "")
                    if detail in seen_paths:
                        continue
                    bullets = item.get("bulletFields") or []
                    loc = str(
                        item.get("locationsText")
                        or item.get("location")
                        or (bullets[0] if bullets else "")
                    )
                    if location and not self._location_matches(location, loc):
                        continue
                    seen_paths.add(detail)
                    external_id = str(item.get("jobId") or "")
                    if not external_id:
                        external_id = str(
                            bullets[1]
                            if len(bullets) > 1
                            else detail.rsplit("_", 1)[-1]
                        )
                    detail_url = f"https://{parsed.netloc}{detail}" if detail.startswith("/") else detail
                    job = DiscoveryJob(
                        adapter_id=self.source_id, company=tenant,
                        role=str(item.get("title") or "").strip(), location=loc,
                        external_job_id=external_id, detail_url=detail_url,
                        application_url=detail_url, posted=unknown(self.source_id),
                        found_date=self._today(), description_html=str(item.get("jobDescription") or ""),
                        description_text=html_to_text(str(item.get("jobDescription") or "")),
                        provenance=make_provenance(source_id=self.source_id, source_name=self.source_name,
                            source_kind=self.source_kind, official=True,
                            extracted_from="official Workday CXS jobs endpoint", detail_url=detail_url,
                            raw_id=external_id),
                    )
                    if fetch_full and not offline and detail.startswith("/job/"):
                        try:
                            job = self._augment_detail(job, parsed.netloc, tenant, site, detail)
                        except SourceError:
                            pass
                    jobs.append(job)
                    if len(jobs) >= requested:
                        break
                offset += len(postings)
                total = int(payload.get("total") or payload.get("totalResults") or 0)
                if total and offset >= total:
                    break
            if jobs:
                break
        return jobs[:requested]

    def _load_page(
        self,
        url: str,
        *,
        offset: int,
        search_text: str,
        applied_facets: dict[str, list[str]],
        offline: bool,
    ):
        if offline:
            with open(self._fixture_path("workday-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.request_json(
            url,
            method="POST",
            json_body={
                "appliedFacets": applied_facets,
                "limit": 20,
                "offset": offset,
                "searchText": search_text,
            },
        )

    def _augment_detail(
        self,
        job: DiscoveryJob,
        host: str,
        tenant: str,
        site: str,
        external_path: str,
    ) -> DiscoveryJob:
        detail_url = f"https://{host}/wday/cxs/{tenant}/{site}{external_path}"
        payload = network.fetch_json(detail_url)
        info = payload.get("jobPostingInfo") or {}
        if not info:
            return job
        description = str(info.get("jobDescription") or "")
        job.description_html = description
        job.description_text = html_to_text(description)
        job.location = str(info.get("location") or job.location)
        job.external_job_id = str(info.get("jobReqId") or job.external_job_id)
        canonical_url = str(info.get("externalUrl") or job.detail_url)
        job.detail_url = canonical_url
        job.application_url = canonical_url
        if info.get("startDate"):
            job.posted = parse_iso(info.get("startDate"), "Workday startDate")
        job.provenance = make_provenance(
            source_id=self.source_id,
            source_name=self.source_name,
            source_kind=self.source_kind,
            official=True,
            extracted_from="official Workday CXS job detail endpoint",
            detail_url=canonical_url,
            raw_id=job.external_job_id,
        )
        return job

    @staticmethod
    def _location_matches(requested: str, actual: str) -> bool:
        wanted = requested.strip().lower()
        found = actual.strip().lower()
        if wanted in found:
            return True
        if wanted == "saudi arabia":
            return found.startswith("sa -") or " sau" in f" {found}" or "saudi arabia" in found
        return False

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
