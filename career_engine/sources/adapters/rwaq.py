"""Rwaq public vacancy-board adapter (runtime-discovered Supabase, fail-closed).

Rwaq is a true vacancy board: its public careers page is a React app whose
browser bundle embeds the public Supabase project URL and an *anonymous public*
client key. There is no stable, documented first-party REST endpoint to hard-code,
and the contract forbids baking the public anon key or Supabase project identity
into source or config.

Design choice (contract-first, robust first-party approach):

- At runtime, fetch the employer careers HTML, discover the current public JS
  asset(s), and extract the publicly embedded Supabase base URL + anon key from
  those assets. Nothing Supabase-specific is hard-coded.
- Query ONLY the public ``jobs`` read surface (``/rest/v1/jobs?select=*``) using
  the discovered anon key. Privileged/internal tables, authenticated APIs and
  employee/admin data are never touched.
- Fail-closed: if the public credentials cannot be discovered, or the read fails,
  raise ``SourceError`` and return no jobs. The route then degrades to
  portal-only with no invented data.
- Offline mode returns no jobs (no network, no fixtures required).
- The employer identity is fixed here; it is never taken from a remote self-claim.
- Never sends or submits.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso
from ..provenance import provenance as make_provenance

CAREERS_URL = "https://www.rwaqeng.com/careers"
_EMPLOYER_NAME = "Rwaq"
_ANON_KEY_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_SUPABASE_URL_RE = re.compile(r"https://[a-z0-9-]+\.supabase\.co")
_CREATE_CLIENT_RE = re.compile(
    r"createClient\(\s*[\"']([^\"']+\.supabase\.co[^\"']*)[\"']\s*,\s*[\"']("
    + _ANON_KEY_RE.pattern
    + r")[\"']"
)
_SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
_MAX_JS_ASSETS = 8
# Rwaq's current first-party application bundle is about 4.9 MiB. Keep the
# shared discovery network default at 2 MiB and widen only this bounded asset
# inspection path enough to read the public client configuration.
_JS_ASSET_MAX_BYTES = 6 * 1024 * 1024
_ACTIVE_STATUSES = {"نشطة", "active", "open", "مفتوحة", "published"}


class RwaqAdapter(SourceAdapter):
    source_id = "rwaq"
    source_name = "Rwaq Public Vacancy Board (runtime-discovered Supabase)"
    source_kind = "employer_page"
    official = True
    employer_name = _EMPLOYER_NAME

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        if offline:
            # No fixtures: the runtime-discovery path requires the live employer
            # assets and must never invent records offline.
            return []
        careers_url = self._careers_url(company)
        supabase_url, anon_key = self._discover_credentials(careers_url)
        rows = self._query_jobs(supabase_url, anon_key, limit)
        requested = max(1, min(int(limit), 200))
        results: list[DiscoveryJob] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().lower()
            if status not in {s.lower() for s in _ACTIVE_STATUSES}:
                # Missing status is not sufficient evidence that an official
                # vacancy is live; fail closed before the consultant scanner.
                continue
            role = str(item.get("title") or "").strip()
            if not role:
                continue
            job_number = item.get("job_number")
            raw_id = str(job_number if job_number is not None else item.get("id") or "").strip()
            if not raw_id:
                continue
            loc = str(item.get("location") or "").strip()
            if location and location.lower() not in loc.lower():
                continue
            description_html = str(item.get("description") or "")
            detail_url = (
                f"{careers_url.rstrip('/')}/job{job_number}"
                if job_number is not None
                else careers_url
            )
            results.append(
                DiscoveryJob(
                    adapter_id=self.source_id,
                    company=self.employer_name,
                    role=role,
                    location=loc,
                    external_job_id=raw_id,
                    detail_url=detail_url,
                    application_url=detail_url,
                    posted=parse_iso(item.get("created_at"), "Rwaq created_at"),
                    found_date=self._today(),
                    description_html=description_html,
                    description_text=html_to_text(description_html),
                    provenance=make_provenance(
                        source_id=self.source_id,
                        source_name=self.source_name,
                        source_kind=self.source_kind,
                        official=True,
                        extracted_from="Rwaq public Supabase jobs table (runtime-discovered public anon key)",
                        detail_url=detail_url,
                        raw_id=raw_id,
                    ),
                    extra={
                        "department": str(item.get("department") or ""),
                        "job_type": str(item.get("job_type") or ""),
                        "level": str(item.get("level") or ""),
                        "status": status,
                    },
                )
            )
            if len(results) >= requested:
                break
        return results

    def _careers_url(self, company: str) -> str:
        candidate = str(company or "").strip()
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate
        return CAREERS_URL

    def _discover_credentials(self, careers_url: str) -> tuple[str, str]:
        html_text = network.fetch_text(careers_url)
        assets = self._collect_js_assets(html_text, careers_url)
        combined: list[str] = []
        for asset in assets[:_MAX_JS_ASSETS]:
            try:
                combined.append(network.fetch_text(asset, max_bytes=_JS_ASSET_MAX_BYTES))
            except SourceError:
                continue
        blob = "\n".join(combined)
        match = _CREATE_CLIENT_RE.search(blob)
        if match:
            return match.group(1), match.group(2)
        url_hits = _SUPABASE_URL_RE.findall(blob)
        key_hits = _ANON_KEY_RE.findall(blob)
        if url_hits and key_hits:
            return url_hits[0], key_hits[0]
        raise SourceError(
            "Rwaq public Supabase credentials could not be discovered from the "
            "employer careers assets; failing closed rather than inventing data."
        )

    def _collect_js_assets(self, html_text: str, base_url: str) -> list[str]:
        assets: list[str] = []
        parsed = urlsplit(base_url)
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        for src in _SCRIPT_SRC_RE.findall(html_text):
            if not src.endswith(".js") and ".js?" not in src:
                continue
            if src.startswith("http://") or src.startswith("https://"):
                assets.append(src)
            elif src.startswith("//"):
                assets.append(f"{parsed.scheme}:{src}")
            elif src.startswith("/"):
                assets.append(f"{origin}{src}")
            else:
                assets.append(f"{origin}/{src.lstrip('/')}")
        # De-duplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for asset in assets:
            if asset not in seen:
                seen.add(asset)
                unique.append(asset)
        return unique

    def _query_jobs(self, supabase_url: str, anon_key: str, limit: int) -> list[dict[str, Any]]:
        rest_url = f"{supabase_url.rstrip('/')}/rest/v1/jobs?select=*&limit={int(limit)}"
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Accept": "application/json",
        }
        data = network.request_json(rest_url, headers=headers)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("jobs"), list):
            return data["jobs"]
        raise SourceError("Rwaq Supabase jobs read returned an unexpected payload")

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
