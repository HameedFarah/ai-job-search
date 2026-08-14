"""Public Workday CXS job-search adapter.

Workday career sites expose an unauthenticated JSON search endpoint.  The
tenant/site are taken from an already verified official career URL; callers
must not derive them from discovery results.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

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
        payload = self._load(url, offline)
        jobs = []
        for item in (payload.get("jobPostings") or [])[: max(1, min(limit, 100))]:
            loc = str(item.get("locationsText") or item.get("location") or "")
            if location and location.lower() not in loc.lower():
                continue
            external_id = str(item.get("bulletFields", [""])[0] or item.get("jobId") or "")
            detail = str(item.get("externalPath") or "")
            detail_url = f"https://{parsed.netloc}{detail}" if detail.startswith("/") else detail
            jobs.append(DiscoveryJob(
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
            ))
        return jobs

    def _load(self, url: str, offline: bool):
        if offline:
            with open(self._fixture_path("workday-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.request_json(url, method="POST", json_body={"limit": 100, "offset": 0})

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
