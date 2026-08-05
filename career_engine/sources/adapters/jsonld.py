"""Employer career page adapter: JobPosting JSON-LD and job sitemaps.

This adapter is the highest-fidelity official source for employers without a
public ATS API. It parses:

- ``<script type="application/ld+json">`` blocks containing a ``JobPosting``
  schema object (``datePosted``, ``validThrough``, ``hiringOrganization``,
  ``jobLocation``, ``title``, ``description``, ``url``, ``directApply``);
- XML job sitemaps (``urlset``) whose entries point at job pages.

It also provides ``verify_official`` - the verification gate that promotes a
discovery candidate (for example from a search engine) to an official
provenance only when the employer's own page embeds JobPosting JSON-LD or is
served by a known official ATS domain. Nothing unverified is ever ingested as
authoritative.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_date
from ..provenance import Provenance, provenance as make_provenance

_LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

# Official ATS host patterns for verification (host suffix match).
_KNOWN_ATS_HOSTS = (
    ".greenhouse.io",
    "boards-api.greenhouse.io",
    ".lever.co",
    "jobs.lever.co",
    ".ashbyhq.com",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "jobs.smartrecruiters.com",
    ".smartrecruiters.com",
    ".workdayjobs.com",
    ".icims.com",
    ".myworkdayjobs.com",
    ".successfactors.eu",
    ".successfactors.com",
)


def extract_ld_json_objects(html_text: str) -> list[dict[str, Any]]:
    """Return all parsed JSON-LD objects from ``application/ld+json`` blocks."""
    objects: list[dict[str, Any]] = []
    for match in _LD_JSON_RE.finditer(html_text):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            objects.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            objects.append(parsed)
    return objects


def find_job_postings(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return dicts whose @type is (or contains) ``JobPosting``."""
    postings: list[dict[str, Any]] = []
    for obj in objects:
        type_value = obj.get("@type")
        types = type_value if isinstance(type_value, list) else [type_value]
        if any(str(item) == "JobPosting" for item in types):
            postings.append(obj)
    return postings


def is_known_ats_host(url: str) -> bool:
    """True when the URL is served by a known official ATS/employer board host."""
    lowered = url.lower()
    for host in _KNOWN_ATS_HOSTS:
        if host.startswith(".") and host.lstrip(".") in lowered:
            return True
        if lowered.startswith(host):
            return True
    return False


class JsonLdAdapter(SourceAdapter):
    source_id = "jsonld"
    source_name = "Employer Career Pages (JobPosting JSON-LD / sitemaps)"
    source_kind = "employer_page"
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
        """Fetch ``company`` as a careers/sitemap URL and parse its postings."""
        url = company.strip()
        if not url.startswith("http"):
            raise SourceError("jsonld adapter requires a careers URL or sitemap URL")
        html_text = self._fetch_text(url, offline)
        postings = find_job_postings(extract_ld_json_objects(html_text))
        if not postings and "sitemap" in url.lower():
            postings = self._from_sitemap(url, offline, html_text=html_text)
        results: list[DiscoveryJob] = []
        for obj in postings[:limit]:
            job = self._map_job(obj, url)
            if location and location.lower() not in job.location.lower():
                continue
            results.append(job)
        return results

    def fetch(
        self,
        external_job_id: str,
        *,
        token: str = "",
        detail_url: str = "",
        offline: bool = False,
    ) -> DiscoveryJob:
        if not detail_url:
            raise SourceError("jsonld fetch requires detail_url (the job posting URL)")
        html_text = self._fetch_text(detail_url, offline)
        for obj in find_job_postings(extract_ld_json_objects(html_text)):
            return self._map_job(obj, detail_url)
        raise SourceError(f"No JobPosting JSON-LD found on {detail_url}")

    def _fetch_text(self, url: str, offline: bool) -> str:
        if offline:
            # Offline mode serves fixtures only for the fixture's own example
            # domain, so the verification gate behaves realistically: other
            # URLs resolve to an empty page and stay unverified.
            if "sitemap" in url.lower():
                with open(self._fixture_path("jsonld-sitemap.xml"), encoding="utf-8") as handle:
                    return handle.read()
            if "careers.example.meridian.com" in url:
                with open(self._fixture_path("jsonld-page.html"), encoding="utf-8") as handle:
                    return handle.read()
            return "<!DOCTYPE html><html><body></body></html>"
        return network.fetch_text(url)

    def _from_sitemap(self, url: str, offline: bool, html_text: str) -> list[dict[str, Any]]:
        if offline:
            with open(self._fixture_path("jsonld-sitemap.xml"), encoding="utf-8") as handle:
                html_text = handle.read()
        try:
            root = ET.fromstring(html_text)
        except ET.ParseError as exc:
            raise SourceError(f"Invalid sitemap XML from {url}: {exc}") from exc
        namespace = root.tag.split("}")[0].strip("{")
        loc_tag = f"{{{namespace}}}loc" if namespace else "loc"
        urls = [element.text.strip() for element in root.iter(loc_tag) if element.text]
        postings: list[dict[str, Any]] = []
        for job_url in urls[:10]:
            try:
                page = self._fetch_text(job_url, offline)
            except SourceError:
                continue
            found = find_job_postings(extract_ld_json_objects(page))
            postings.extend(found)
        return postings

    def _map_job(self, obj: dict[str, Any], page_url: str) -> DiscoveryJob:
        title = str(obj.get("title") or "").strip()
        org = obj.get("hiringOrganization") or {}
        company = str(org.get("name") or obj.get("organization") or "") if isinstance(org, dict) else str(org or "")
        company = company.strip()
        location_obj = obj.get("jobLocation") or {}
        address = _extract_address(location_obj)
        detail_url = str(obj.get("url") or "").strip() or page_url
        external_id = str(obj.get("identifier") or "") if isinstance(obj.get("identifier"), str) else str(
            (obj.get("identifier") or {}).get("value", "") if isinstance(obj.get("identifier"), dict) else ""
        )
        description = str(obj.get("description") or "").strip()
        posted = parse_date(obj.get("datePosted"), "schema.org JobPosting datePosted")
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=company or "Unknown company",
            role=title or "Unknown role",
            location=address,
            external_job_id=external_id,
            detail_url=detail_url,
            application_url=detail_url if obj.get("directApply") is not False else "",
            posted=posted,
            found_date=utc_today(),
            description_html=description,
            description_text=html_to_text(description),
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="JobPosting JSON-LD (datePosted)",
                detail_url=detail_url,
                raw_id=external_id,
                verification="official employer career page (JobPosting JSON-LD)",
            ),
            extra={
                "employment_type": str(obj.get("employmentType") or ""),
                "valid_through": str(obj.get("validThrough") or ""),
            },
        )

    def verify_official(self, url: str, *, offline: bool = False) -> Provenance:
        """Verification gate: promote a candidate URL to official provenance.

        A URL is official when its employer page embeds a JobPosting JSON-LD
        block or when it is served from a known official ATS host. Candidates
        that fail are returned as unverified and must not be ingested.
        """
        if not url.startswith("http"):
            return _unverified(url, "not an http(s) URL")
        if is_known_ats_host(url):
            return _verified(url, "known official ATS host")
        try:
            html_text = self._fetch_text(url, offline)
        except SourceError as exc:
            return _unverified(url, f"fetch failed: {exc}")
        postings = find_job_postings(extract_ld_json_objects(html_text))
        if postings:
            return _verified(url, "employer page embeds JobPosting JSON-LD")
        return _unverified(url, "no JobPosting JSON-LD and no known ATS host")


def _extract_address(location_obj: Any) -> str:
    if isinstance(location_obj, str):
        return location_obj.strip()
    if not isinstance(location_obj, dict):
        return ""
    address = location_obj.get("address") or {}
    if isinstance(address, dict):
        parts = [
            str(address.get("addressLocality") or ""),
            str(address.get("addressRegion") or ""),
            str(address.get("addressCountry") or ""),
        ]
        return ", ".join(part for part in parts if part)
    if isinstance(address, str):
        return address
    name = location_obj.get("name")
    return str(name) if name else ""


def _verified(url: str, reason: str) -> Provenance:
    return make_provenance(
        source_id="jsonld",
        source_name="Employer Career Pages (JobPosting JSON-LD / sitemaps)",
        source_kind="employer_page",
        official=True,
        extracted_from="official verification gate",
        detail_url=url,
        verification=reason,
    )


def _unverified(url: str, reason: str) -> Provenance:
    return make_provenance(
        source_id="search_discovery",
        source_name="Search-Engine Discovery",
        source_kind="discovery",
        official=False,
        extracted_from="official verification gate",
        detail_url=url,
        verification=f"unverified discovery candidate: {reason}",
        fetch_success=False,
    )


def utc_today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
