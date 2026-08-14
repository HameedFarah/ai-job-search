"""Public SAP SuccessFactors Recruiting Marketing (RMK) adapter.

Branded SuccessFactors career sites expose an unauthenticated
``/tile-search-results/?startrow=N`` endpoint.  This adapter enumerates those
official tiles and, when requested, fetches the official detail page to retain
the full ``jobdescription`` block for Career Engine scoring.

The list/pagination pattern is adapted from the maintained
Fighter90/career-ops-ui SuccessFactors integration; normalization and detail
retention stay inside the central Career Engine.
"""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import unknown
from ..provenance import provenance as make_provenance

_TILE_RE = re.compile(r'<li class="job-tile job-id-(\d+)\b[\s\S]*?</li>', re.I)
_URL_RE = re.compile(r'data-url="([^"]+)"', re.I)
_TITLE_RE = re.compile(r'class="jobTitle-link[^"]*"[^>]*>([\s\S]*?)</a>', re.I)
_DESC_RE = re.compile(
    r'<span[^>]*class="[^"]*jobdescription[^"]*"[^>]*>([\s\S]*?)</span>',
    re.I,
)


class SuccessFactorsAdapter(SourceAdapter):
    source_id = "successfactors"
    source_name = "SAP SuccessFactors RMK public careers"
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
        company_name, base = self._company_base(company)
        requested = max(1, min(int(limit), 100))
        tiles = self._enumerate(base, requested=requested, offline=offline)
        results: list[DiscoveryJob] = []
        for raw_id, role, path in tiles:
            detail_url = urljoin(base.rstrip("/") + "/", path)
            description_html = ""
            if fetch_full:
                detail_html = self._load_detail(detail_url, offline=offline)
                match = _DESC_RE.search(detail_html)
                if match:
                    description_html = match.group(1).strip()
            job = DiscoveryJob(
                adapter_id=self.source_id,
                company=company_name,
                role=role,
                location="",
                external_job_id=raw_id,
                detail_url=detail_url,
                application_url=detail_url,
                posted=unknown(self.source_id),
                found_date=self._today(),
                description_html=description_html,
                description_text=html_to_text(description_html),
                provenance=make_provenance(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    source_kind=self.source_kind,
                    official=True,
                    extracted_from="official SuccessFactors RMK tile/detail pages",
                    detail_url=detail_url,
                    raw_id=raw_id,
                ),
            )
            # Only apply a location filter when the tenant actually exposes a
            # location. A blank RMK location must not turn a live board into a
            # false zero-result source.
            if location and job.location and location.lower() not in job.location.lower():
                continue
            results.append(job)
            if len(results) >= requested:
                break
        return results

    def _company_base(self, value: str) -> tuple[str, str]:
        raw = str(value or "").strip()
        if "|" in raw:
            name, raw_url = raw.split("|", 1)
            company_name = name.strip()
            raw = raw_url.strip()
        else:
            company_name = ""
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise SourceError("SuccessFactors adapter requires a public HTTPS careers URL")
        path = parsed.path.rstrip("/")
        path = re.sub(r"/(?:search|tile-search-results)$", "", path, flags=re.I)
        base = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        return company_name or parsed.netloc, base

    def _enumerate(self, base: str, *, requested: int, offline: bool) -> list[tuple[str, str, str]]:
        if offline:
            with open(self._fixture_path("successfactors-list.html"), encoding="utf-8") as handle:
                return self._parse_tiles(handle.read())[:requested]
        out: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        startrow = 0
        for _ in range(40):
            url = f"{base.rstrip('/')}/tile-search-results/?startrow={startrow}"
            html = network.fetch_text(url)
            page = self._parse_tiles(html)
            if not page:
                break
            fresh = 0
            for item in page:
                if item[0] in seen:
                    continue
                seen.add(item[0])
                out.append(item)
                fresh += 1
                if len(out) >= requested:
                    return out
            if fresh == 0:
                break
            startrow += len(page)
        return out

    def _load_detail(self, url: str, *, offline: bool) -> str:
        if offline:
            with open(self._fixture_path("successfactors-detail.html"), encoding="utf-8") as handle:
                return handle.read()
        return network.fetch_text(url, max_bytes=4 * 1024 * 1024)

    @staticmethod
    def _parse_tiles(html: str) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for match in _TILE_RE.finditer(html or ""):
            raw_id = match.group(1)
            block = match.group(0)
            url_match = _URL_RE.search(block)
            title_match = _TITLE_RE.search(block)
            if not url_match or not title_match:
                continue
            title = html_to_text(unescape(title_match.group(1))).strip()
            path = unescape(url_match.group(1)).strip()
            if title and path:
                out.append((raw_id, title, path))
        return out

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
