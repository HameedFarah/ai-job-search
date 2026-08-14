"""Oracle Taleo public job-board adapter.

Taleo exposes a public JSON search endpoint under each employer tenant:

    https://<tenant>.taleo.net/careersection/rest/jobboard/searchjobs

The request/response mapping follows the maintained MIT-licensed
``ever-jobs/ever-jobs`` Taleo source contract, adapted to Career Engine's
bounded stdlib networking, provenance and no-send model.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_date, unknown
from ..provenance import provenance as make_provenance

_TALEO_HOST_RE = re.compile(r"(?:^|\.)taleo\.net$", re.I)
_DESC_PATTERNS = (
    re.compile(r'<span[^>]+class="[^"]*jobdescription[^"]*"[^>]*>([\s\S]*?)</span>', re.I),
    re.compile(r'<div[^>]+class="[^"]*jobdescription[^"]*"[^>]*>([\s\S]*?)</div>', re.I),
    re.compile(r'<div[^>]+id="[^"]*requisitionDescription[^"]*"[^>]*>([\s\S]*?)</div>', re.I),
)


class TaleoAdapter(SourceAdapter):
    source_id = "taleo"
    source_name = "Oracle Taleo public job board"
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
        tenant, career_section = self._identifier(company)
        requested = max(1, min(int(limit), 100))
        if offline:
            import json
            with open(self._fixture_path("taleo-list.json"), encoding="utf-8") as handle:
                first_page = json.load(handle)
            pages = [first_page]
        else:
            pages = None

        endpoint = f"https://{tenant}.taleo.net/careersection/rest/jobboard/searchjobs"
        jobs: list[DiscoveryJob] = []
        page_no = 1
        while len(jobs) < requested:
            if pages is not None:
                payload = pages[page_no - 1] if page_no <= len(pages) else {"requisitionList": []}
            else:
                payload = network.request_json(
                    endpoint,
                    method="POST",
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json_body={
                        "multilineEnabled": False,
                        "sortingSelection": {
                            "sortBySelectionParam": "postedDate",
                            "ascendingSortingOrder": "false",
                        },
                        "pageNo": page_no,
                        "pageSize": 25,
                        "keyword": "",
                        "location": location or "",
                    },
                    max_bytes=4 * 1024 * 1024,
                )
            listings = payload.get("requisitionList") or [] if isinstance(payload, dict) else []
            if not listings:
                break
            for item in listings:
                if not isinstance(item, dict):
                    continue
                job = self._map_item(item, tenant=tenant, career_section=career_section)
                if not job.role:
                    continue
                if location and not self._location_matches(location, job.location):
                    continue
                if fetch_full and not offline:
                    try:
                        self._augment_detail(job)
                    except SourceError:
                        pass
                jobs.append(job)
                if len(jobs) >= requested:
                    break
            if len(listings) < 25 or pages is not None:
                break
            page_no += 1
            if page_no > 20:  # hard request cap: 500 listings per tenant
                break
        return jobs[:requested]

    def _identifier(self, value: str) -> tuple[str, str]:
        raw = str(value or "").strip()
        if not raw:
            raise SourceError("Taleo adapter requires an employer-specific tenant/career-section identifier")
        if "|" in raw and "://" not in raw:
            tenant, career_section = [part.strip() for part in raw.split("|", 1)]
            tenant = tenant.removesuffix(".taleo.net")
            if not tenant or not career_section:
                raise SourceError("Taleo identifier must be <tenant>|<careerSection>")
            return tenant, career_section
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
        if parsed.scheme != "https" or not parsed.hostname or not _TALEO_HOST_RE.search(parsed.hostname):
            raise SourceError("Taleo adapter requires an official HTTPS taleo.net URL")
        tenant = parsed.hostname[: -len(".taleo.net")]
        parts = [part for part in parsed.path.split("/") if part]
        career_section = ""
        if "careersection" in parts:
            idx = parts.index("careersection")
            if idx + 1 < len(parts):
                career_section = parts[idx + 1]
        if not career_section:
            raise SourceError("Taleo URL must include /careersection/<careerSection>/")
        return tenant, career_section

    def _map_item(self, item: dict, *, tenant: str, career_section: str) -> DiscoveryJob:
        title = str(item.get("title") or "").strip()
        contest_no = str(item.get("contestNo") or "").strip()
        contest_url = str(item.get("contestUrl") or "").strip()
        if contest_url.startswith("http"):
            detail_url = contest_url
        elif contest_no:
            detail_url = f"https://{tenant}.taleo.net/careersection/{career_section}/jobdetail.ftl?job={contest_no}"
        else:
            detail_url = f"https://{tenant}.taleo.net/careersection/{career_section}/jobsearch.ftl"
        location = str(item.get("primaryLocation") or "").strip()
        raw_date = item.get("postingDate") or item.get("openingDate")
        posted = parse_date(raw_date, "Taleo posting/opening date") if raw_date else unknown(self.source_id)
        raw_id = contest_no or detail_url
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=str(item.get("organization") or tenant).strip(),
            role=unescape(title),
            location=unescape(location),
            external_job_id=raw_id,
            detail_url=detail_url,
            application_url=detail_url,
            posted=posted,
            found_date=self._today(),
            description_html="",
            description_text="",
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="official Taleo public jobboard search endpoint",
                detail_url=detail_url,
                raw_id=raw_id,
            ),
            extra={"job_field": item.get("jobField") or "", "job_type": item.get("jobType") or ""},
        )

    def _augment_detail(self, job: DiscoveryJob) -> None:
        html = network.fetch_text(job.detail_url, max_bytes=4 * 1024 * 1024)
        description_html = ""
        for pattern in _DESC_PATTERNS:
            match = pattern.search(html)
            if match:
                description_html = match.group(1).strip()
                break
        if description_html:
            job.description_html = description_html
            job.description_text = html_to_text(description_html)

    @staticmethod
    def _location_matches(requested: str, actual: str) -> bool:
        wanted = requested.strip().lower()
        found = actual.strip().lower()
        if not wanted:
            return True
        if wanted in found:
            return True
        if wanted == "saudi arabia":
            return any(token in found for token in ("saudi arabia", "riyadh", "jeddah", "dammam", "khobar", "ksa"))
        return False

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
