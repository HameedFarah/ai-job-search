"""Keystone AI public recruiter-board adapter.

Keystone is a recruitment marketplace, not an employer-owned careers site. Its
public job pages are useful discovery evidence, but they must never be promoted
to official employer provenance. The adapter therefore emits unverified
Career Engine records and leaves ``application_url`` blank until the normal
employer/ATS verification gate succeeds.

The public board is client-rendered. We support two bounded paths without
login/session automation:

* a concrete public Keystone job URL or UUID (reliable detail-page probe);
* best-effort extraction of public ``/jobs/<uuid>`` links from the board HTML.

No private API, authenticated session, browser automation, or reverse-
engineering of Keystone's internal application system is used.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import PostingDate, parse_date, unknown
from ..provenance import provenance

BASE_URL = "https://gokeystone.ai/jobs"
SOURCE_NAME = "Keystone AI Public Recruiter Jobs"
_UUID_RE = re.compile(
    r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_JOB_LINK_RE = re.compile(
    r"(?i)(?:https?://(?:www\.)?gokeystone\.ai)?/jobs/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
)
_JSONLD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_DATE_TEXT_RE = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(20\d{2})\b",
    re.IGNORECASE,
)


class KeystoneAdapter(SourceAdapter):
    source_id = "keystone"
    source_name = SOURCE_NAME
    source_kind = "board_web"
    official = False

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        """Discover Keystone jobs without authentication.

        ``company`` follows the generic probe interface. For Keystone it may be
        a concrete job URL/UUID (preferred for live probes), ``all``/``keystone``
        for best-effort board discovery, or text used to filter any links that
        are present in the public board HTML.
        """
        limit = max(1, min(int(limit), 100))
        query = (company or "").strip()
        direct_url = self._detail_url(query)
        if direct_url:
            detail_html = self._read_detail(direct_url, offline=offline)
            job = self._parse_detail(detail_html, direct_url)
            return [job] if self._matches(job, query="", location=location) else []

        board_html = self._read_board(offline=offline)
        urls = self._extract_detail_urls(board_html)
        if not urls:
            return []

        wanted = "" if query.lower() in {"", "all", "keystone", "gokeystone.ai"} else query
        jobs: list[DiscoveryJob] = []
        # Bound detail fetches even when the board embeds many historical URLs.
        for url in urls[: min(len(urls), max(limit * 3, limit))]:
            try:
                detail_html = self._read_detail(url, offline=offline)
                job = self._parse_detail(detail_html, url)
            except SourceError:
                continue
            if self._matches(job, query=wanted, location=location):
                jobs.append(job)
            if len(jobs) >= limit:
                break
        return jobs

    def _read_board(self, *, offline: bool) -> str:
        if offline:
            with open(self._fixture_path("keystone-board.html"), encoding="utf-8") as handle:
                return handle.read()
        return network.fetch_text(BASE_URL)

    def _read_detail(self, url: str, *, offline: bool) -> str:
        if offline:
            with open(self._fixture_path("keystone-job.html"), encoding="utf-8") as handle:
                return handle.read()
        return network.fetch_text(url)

    @staticmethod
    def _detail_url(value: str) -> str:
        text = value.strip()
        if _UUID_RE.fullmatch(text):
            return f"{BASE_URL}/{text.lower()}"
        if not text.startswith(("http://", "https://")):
            return ""
        parsed = urlparse(text)
        if (parsed.hostname or "").lower() not in {"gokeystone.ai", "www.gokeystone.ai"}:
            return ""
        match = _JOB_LINK_RE.search(parsed.path)
        return f"{BASE_URL}/{match.group(1).lower()}" if match else ""

    @staticmethod
    def _extract_detail_urls(page_html: str) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        decoded = html_lib.unescape(page_html or "")
        for match in _JOB_LINK_RE.finditer(decoded):
            url = f"{BASE_URL}/{match.group(1).lower()}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _parse_detail(self, page_html: str, url: str) -> DiscoveryJob:
        job_id_match = _JOB_LINK_RE.search(url)
        if not job_id_match:
            raise SourceError(f"Invalid Keystone job URL: {url}")
        job_id = job_id_match.group(1).lower()

        payload = self._jobposting_jsonld(page_html)
        if payload:
            role = self._clean(payload.get("title"))
            description_html = self._clean(payload.get("description"), preserve=True)
            location = self._jsonld_location(payload)
            hiring_org = payload.get("hiringOrganization")
            company = ""
            if isinstance(hiring_org, dict):
                company = self._clean(hiring_org.get("name"))
            posted = parse_date(payload.get("datePosted"), "keystone JobPosting.datePosted")
        else:
            role = self._heading(page_html)
            description_html = page_html
            location = ""
            company = ""
            posted = self._visible_date(page_html)

        description_text = html_to_text(description_html or page_html)
        if not role:
            raise SourceError(f"Keystone detail page did not expose a job title: {url}")
        if not description_text:
            raise SourceError(f"Keystone detail page did not expose a job description: {url}")

        # Do not invent an undisclosed client. Keystone is the verified recruiter
        # publication surface; a named hiring organization is retained when the
        # page explicitly supplies one.
        company = company or "Keystone AI (recruiter)"
        found_date = datetime.now(timezone.utc).date().isoformat()
        prov = provenance(
            source_id=self.source_id,
            source_name=self.source_name,
            source_kind=self.source_kind,
            official=False,
            extracted_from=(
                "public Keystone JobPosting JSON-LD"
                if payload
                else "public Keystone job detail HTML"
            ),
            detail_url=url,
            raw_id=job_id,
            verification=(
                "recruiter-board discovery only; official employer/ATS verification required"
            ),
            notes=[
                "Keystone is a recruiter/talent platform, not an employer-owned careers source.",
                "No authenticated Keystone session or private API was used.",
            ],
        )
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=company,
            role=role,
            location=location,
            external_job_id=job_id,
            detail_url=url,
            application_url="",
            posted=posted,
            found_date=found_date,
            description_html=description_html,
            description_text=description_text,
            provenance=prov,
            extra={
                "recruiter_board": True,
                "public_apply_route": url,
                "employer_official_verified": False,
            },
        )

    @staticmethod
    def _jobposting_jsonld(page_html: str) -> dict[str, Any] | None:
        for raw in _JSONLD_RE.findall(page_html or ""):
            try:
                value = json.loads(html_lib.unescape(raw).strip())
            except (json.JSONDecodeError, TypeError):
                continue
            for item in KeystoneAdapter._walk_json(value):
                type_value = item.get("@type")
                types = type_value if isinstance(type_value, list) else [type_value]
                if any(str(kind).lower() == "jobposting" for kind in types if kind):
                    return item
        return None

    @staticmethod
    def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            graph = value.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    yield from KeystoneAdapter._walk_json(item)
        elif isinstance(value, list):
            for item in value:
                yield from KeystoneAdapter._walk_json(item)

    @staticmethod
    def _jsonld_location(payload: dict[str, Any]) -> str:
        locations = payload.get("jobLocation")
        if not isinstance(locations, list):
            locations = [locations]
        parts: list[str] = []
        for location in locations:
            if not isinstance(location, dict):
                continue
            address = location.get("address")
            if not isinstance(address, dict):
                continue
            for key in ("addressLocality", "addressRegion", "addressCountry"):
                value = KeystoneAdapter._clean(address.get(key))
                if value and value not in parts:
                    parts.append(value)
        return ", ".join(parts)

    @staticmethod
    def _heading(page_html: str) -> str:
        match = _H1_RE.search(page_html or "")
        return html_to_text(match.group(1)) if match else ""

    @staticmethod
    def _visible_date(page_html: str) -> PostingDate:
        text = html_to_text(page_html)
        match = _DATE_TEXT_RE.search(text)
        if not match:
            return unknown("keystone visible posting date")
        try:
            parsed = datetime.strptime(" ".join(match.groups()), "%d %b %Y")
        except ValueError:
            return unknown("keystone visible posting date")
        return parse_date(parsed.strftime("%Y-%m-%d"), "keystone visible posting date")

    @staticmethod
    def _matches(job: DiscoveryJob, *, query: str, location: str | None) -> bool:
        if query:
            haystack = " ".join(
                [job.company, job.role, job.location, job.description_text]
            ).lower()
            if query.lower() not in haystack:
                return False
        if location and location.strip():
            if location.strip().lower() not in job.location.lower():
                return False
        return True

    @staticmethod
    def _clean(value: Any, *, preserve: bool = False) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return text if preserve else html_to_text(text)
