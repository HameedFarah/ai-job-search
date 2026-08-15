"""Public Phenom CareerConnect adapter for branded employer career sites.

Adapted from Fighter90/career-ops-ui's maintained Phenom source contract.  The
branded host exposes a zero-auth ``POST /widgets`` refineSearch endpoint.  The
adapter is discovery-only, host-pins every request to the supplied HTTPS career
origin, paginates with hard caps, and filters location locally.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso, unknown
from ..provenance import provenance as make_provenance

_PAGE_SIZE = 100
_MAX_PAGES = 40
_REMOTE_RE = re.compile(r"remote|anywhere|distributed|home\s*office", re.I)
_MAIN_RE = re.compile(r'<(?:main|article)\b[^>]*>([\s\S]*?)</(?:main|article)>', re.I)


def _slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return value or "job"


class PhenomAdapter(SourceAdapter):
    source_id = "phenom"
    source_name = "Phenom public careers widget"
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
        name, origin, url_prefix = self._spec(company)
        if offline:
            return []
        requested = max(1, min(int(limit), 100))
        endpoint = f"{origin}/widgets"
        out: list[DiscoveryJob] = []
        seen: set[str] = set()
        total: int | None = None

        for page in range(_MAX_PAGES):
            payload = network.request_json(
                endpoint,
                method="POST",
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json_body={
                    "lang": "en_global",
                    "deviceType": "desktop",
                    "country": "global",
                    "pageName": "search-results",
                    "ddoKey": "refineSearch",
                    "sortBy": "",
                    "subsearch": "",
                    "from": page * _PAGE_SIZE,
                    "jobs": True,
                    "counts": True,
                    "all_fields": ["category", "country", "city"],
                    "size": _PAGE_SIZE,
                    "clearAll": False,
                    "jdsource": "facets",
                    "isSliderEnable": False,
                    "pageId": "page10",
                    "siteType": "external",
                    "keywords": "",
                    "global": True,
                    "selected_fields": {},
                    "locationData": {},
                },
                max_bytes=8 * 1024 * 1024,
            )
            if not isinstance(payload, dict):
                raise SourceError(f"{name} Phenom widget returned an unexpected payload")
            refine = payload.get("refineSearch")
            if not isinstance(refine, dict):
                raise SourceError(f"{name} Phenom widget did not expose refineSearch")
            data = refine.get("data")
            rows = data.get("jobs") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise SourceError(f"{name} Phenom widget did not expose jobs")
            if total is None and isinstance(refine.get("totalHits"), int):
                total = int(refine["totalHits"])

            for item in rows:
                if not isinstance(item, dict):
                    continue
                job = self._map(item, name=name, origin=origin, url_prefix=url_prefix)
                if job is None or job.detail_url in seen:
                    continue
                seen.add(job.detail_url)
                if location and not self._location_matches(location, job.location):
                    continue
                out.append(job)
                if len(out) >= requested:
                    break
            if len(out) >= requested or not rows or len(rows) < _PAGE_SIZE:
                break
            if total is not None and (page + 1) * _PAGE_SIZE >= total:
                break

        if fetch_full:
            for job in out:
                try:
                    detail = network.fetch_text(job.detail_url, max_bytes=4 * 1024 * 1024)
                    match = _MAIN_RE.search(detail)
                    job.description_text = html_to_text(match.group(1) if match else detail)
                except SourceError as exc:
                    job.extra["detail_fetch_error"] = str(exc)
        return out

    @staticmethod
    def _spec(value: str) -> tuple[str, str, str]:
        raw = str(value or "").strip()
        if "|" in raw:
            name, raw_url = [part.strip() for part in raw.split("|", 1)]
        else:
            name, raw_url = "", raw
        parsed = urlsplit(raw_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SourceError("Phenom adapter requires a public HTTPS branded career URL")
        origin = f"https://{parsed.hostname}"
        prefix = parsed.path.strip("/") or "global/en"
        return name or parsed.hostname, origin, prefix

    def _map(self, item: dict, *, name: str, origin: str, url_prefix: str) -> DiscoveryJob | None:
        raw_id = str(item.get("jobId") or "").strip()
        role = html_to_text(str(item.get("title") or "")).strip()
        if not raw_id or not role:
            return None
        loc = self._location(item)
        url = f"{origin}/{url_prefix}/job/{raw_id}/{_slugify(role)}"
        date_value = str(item.get("postedDate") or item.get("dateCreated") or "").strip()
        posted = parse_iso(date_value, "Phenom postedDate/dateCreated") if date_value else unknown(self.source_id)
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=name,
            role=role,
            location=loc,
            external_job_id=raw_id,
            detail_url=url,
            application_url=url,
            posted=posted,
            found_date=self._today(),
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="official branded Phenom /widgets refineSearch endpoint",
                detail_url=url,
                raw_id=raw_id,
            ),
            extra={"remote": bool(_REMOTE_RE.search(role) or _REMOTE_RE.search(loc))},
        )

    @staticmethod
    def _location(item: dict) -> str:
        direct = str(item.get("location") or item.get("cityStateCountry") or item.get("cityState") or "").strip()
        if direct:
            return html_to_text(direct)
        parts = [str(item.get(key) or "").strip() for key in ("city", "state", "country")]
        return ", ".join(dict.fromkeys(part for part in parts if part))

    @staticmethod
    def _location_matches(requested: str, actual: str) -> bool:
        wanted = requested.strip().lower()
        found = actual.strip().lower()
        if wanted in found:
            return True
        if wanted == "saudi arabia":
            return any(token in found for token in ("saudi arabia", "ksa", "riyadh", "jeddah", "jubail", "dhalm", "khobar", "dhahran"))
        return False

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
