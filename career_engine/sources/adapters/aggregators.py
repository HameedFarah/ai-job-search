"""Licensed discovery providers for the Career Engine.

Brave Search, Jooble and Careerjet are discovery-only. Their results never
become authoritative vacancy records until an official employer or ATS source
verifies the posting. Missing credentials make a source unavailable without
failing the wider scan. Careerjet additionally requires an explicit
user-triggered call and the triggering user's IP and user-agent.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, SourceUnavailable, html_to_text
from ..dates import parse_iso, unknown
from ..provenance import provenance as make_provenance

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
JOOBLE_ENDPOINT = "https://jooble.org/api/{api_key}"
CAREERJET_ENDPOINT = "https://search.api.careerjet.net/v4/query"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _stable_id(source_id: str, url: str, fallback: str = "") -> str:
    raw = f"{source_id}|{url}|{fallback}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _candidate_company(item: dict[str, Any], url: str) -> str:
    profile = item.get("profile")
    if isinstance(profile, dict):
        for key in ("long_name", "name"):
            value = str(profile.get(key) or "").strip()
            if value:
                return value
    value = str(item.get("company") or "").strip()
    if value:
        return value
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "Unknown employer"


def _discovery_job(
    *,
    adapter_id: str,
    source_name: str,
    source_kind: str,
    company: str,
    role: str,
    location: str,
    external_job_id: str,
    detail_url: str,
    description: str,
    extracted_from: str,
    posted=None,
    extra: dict[str, Any] | None = None,
) -> DiscoveryJob:
    return DiscoveryJob(
        adapter_id=adapter_id,
        company=company.strip() or "Unknown employer",
        role=role.strip() or "Unspecified role",
        location=location.strip(),
        external_job_id=external_job_id.strip() or _stable_id(adapter_id, detail_url, role),
        detail_url=detail_url.strip(),
        application_url="",
        posted=posted or unknown(adapter_id),
        found_date=_today(),
        description_html=description,
        description_text=html_to_text(description),
        provenance=make_provenance(
            source_id=adapter_id,
            source_name=source_name,
            source_kind=source_kind,
            official=False,
            extracted_from=extracted_from,
            detail_url=detail_url,
            raw_id=external_job_id,
            verification=(
                "discovery-only candidate; must be promoted through an official "
                "employer or ATS source before becoming authoritative"
            ),
        ),
        extra={
            "requires_official_verification": True,
            **(extra or {}),
        },
    )


class BraveSearchAdapter(SourceAdapter):
    source_id = "brave_search"
    source_name = "Brave Search API"
    source_kind = "discovery"
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
        key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if not key:
            raise SourceUnavailable("BRAVE_SEARCH_API_KEY is not configured")
        query = company.strip()
        if location:
            query = f"{query} {location.strip()}"
        params = {
            "q": query,
            "count": max(1, min(int(limit), 20)),
            "safesearch": "moderate",
        }
        payload = network.request_json(
            BRAVE_ENDPOINT + "?" + urllib.parse.urlencode(params),
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        results = ((payload or {}).get("web") or {}).get("results") or []
        jobs: list[DiscoveryJob] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or "").strip()
            jobs.append(
                _discovery_job(
                    adapter_id=self.source_id,
                    source_name=self.source_name,
                    source_kind=self.source_kind,
                    company=_candidate_company(item, url),
                    role=title,
                    location=location or "",
                    external_job_id=str(item.get("id") or _stable_id(self.source_id, url)),
                    detail_url=url,
                    description=description,
                    extracted_from="Brave Web Search API web.results",
                    extra={"query": query},
                )
            )
        return jobs


class JoobleAdapter(SourceAdapter):
    source_id = "jooble"
    source_name = "Jooble REST API"
    source_kind = "aggregator_api"
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
        key = os.environ.get("JOOBLE_API_KEY", "").strip()
        if not key:
            raise SourceUnavailable("JOOBLE_API_KEY is not configured")
        request_payload = {
            "keywords": company.strip(),
            "location": (location or "").strip(),
            "page": "1",
            "ResultOnPage": str(max(1, min(int(limit), 20))),
            "companysearch": "false",
        }
        try:
            payload = network.request_json(
                JOOBLE_ENDPOINT.format(api_key=urllib.parse.quote(key, safe="")),
                method="POST",
                json_body=request_payload,
                headers={"Accept": "application/json"},
            )
        except SourceError as exc:
            # The Jooble credential is part of the endpoint path. Never allow a
            # lower-level exception to echo that URL into logs or reports.
            raise SourceError("Jooble API request failed") from exc
        results = (payload or {}).get("jobs") or []
        jobs: list[DiscoveryJob] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("link") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            raw_id = str(item.get("id") or _stable_id(self.source_id, url))
            jobs.append(
                _discovery_job(
                    adapter_id=self.source_id,
                    source_name=self.source_name,
                    source_kind=self.source_kind,
                    company=str(item.get("company") or "Unknown employer"),
                    role=str(item.get("title") or "Unspecified role"),
                    location=str(item.get("location") or location or ""),
                    external_job_id=raw_id,
                    detail_url=url,
                    description=str(item.get("snippet") or ""),
                    extracted_from="Jooble REST API jobs",
                    posted=unknown("jooble.updated is not treated as posting date"),
                    extra={
                        "aggregator_source": str(item.get("source") or ""),
                        "upstream_updated": str(item.get("updated") or ""),
                        "job_type": str(item.get("type") or ""),
                    },
                )
            )
        return jobs


class CareerjetAdapter(SourceAdapter):
    source_id = "careerjet"
    source_name = "Careerjet Publisher API"
    source_kind = "aggregator_api"
    official = False

    def __init__(
        self,
        *,
        fixtures_dir: str | None = None,
        user_triggered: bool = False,
        user_ip: str = "",
        user_agent: str = "",
    ) -> None:
        super().__init__(fixtures_dir=fixtures_dir)
        self.user_triggered = bool(user_triggered)
        self.user_ip = user_ip.strip()
        self.user_agent = user_agent.strip()

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        api_key = (
            os.environ.get("CAREERJET_API_KEY", "").strip()
            or os.environ.get("CAREERJET_AFFID", "").strip()
        )
        if not api_key:
            raise SourceUnavailable(
                "CAREERJET_API_KEY is not configured "
                "(CAREERJET_AFFID is accepted only as a legacy secret-name alias)"
            )
        if not self.user_triggered:
            raise SourceError("Careerjet is manual-only; pass an explicit user-triggered flag")
        if not self.user_ip or not self.user_agent:
            raise SourceError(
                "Careerjet requires the triggering user's actual IP and user-agent; "
                "the probe was denied"
            )
        try:
            address = ipaddress.ip_address(self.user_ip)
        except ValueError as exc:
            raise SourceError("Careerjet user_ip is not a valid IP address") from exc
        if not address.is_global:
            raise SourceError(
                "Careerjet requires the triggering user's public IP address; "
                "loopback, private and reserved addresses are denied"
            )
        params = {
            "locale_code": os.environ.get("CAREERJET_LOCALE_CODE", "en_SA"),
            "keywords": company.strip(),
            "location": (location or "").strip(),
            "sort": "date",
            "page_size": max(1, min(int(limit), 100)),
            "user_ip": self.user_ip,
            "user_agent": self.user_agent,
        }
        token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
        try:
            payload = network.request_json(
                CAREERJET_ENDPOINT + "?" + urllib.parse.urlencode(params),
                headers={
                    "Authorization": f"Basic {token}",
                    "Accept": "application/json",
                    "User-Agent": self.user_agent,
                },
            )
        except SourceError as exc:
            # The request URL contains the user's IP and user-agent. Keep both
            # out of persisted error messages and source reports.
            raise SourceError("Careerjet API request failed") from exc
        if not isinstance(payload, dict) or payload.get("type") != "JOBS":
            return []
        jobs: list[DiscoveryJob] = []
        for item in (payload.get("jobs") or [])[:limit]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            posted = unknown("careerjet.date")
            raw_date = str(item.get("date") or "").strip()
            if raw_date:
                try:
                    posted = parse_iso(parsedate_to_datetime(raw_date).isoformat(), "careerjet.date")
                except (TypeError, ValueError, OverflowError):
                    posted = unknown("careerjet.date")
            jobs.append(
                _discovery_job(
                    adapter_id=self.source_id,
                    source_name=self.source_name,
                    source_kind=self.source_kind,
                    company=str(item.get("company") or "Unknown employer"),
                    role=str(item.get("title") or "Unspecified role"),
                    location=str(item.get("locations") or location or ""),
                    external_job_id=_stable_id(self.source_id, url),
                    detail_url=url,
                    description=str(item.get("description") or ""),
                    extracted_from="Careerjet Publisher API v4 jobs",
                    posted=posted,
                    extra={
                        "manual_user_triggered": True,
                        "salary": str(item.get("salary") or ""),
                    },
                )
            )
        return jobs
