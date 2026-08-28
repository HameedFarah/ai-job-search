"""HMA public vacancy-board adapter (first-party, unauthenticated JSON).

HMA exposes a true live vacancy board through its public first-party endpoint:

    GET https://hr.hma.sa/api/public-jobs

which returns ``{ok: true, jobs: [...], count: N}``. Each record carries
``id``, ``title``, ``department``, ``city``, ``description`` and ``status``.
Open roles use the Arabic status ``"مفتوحة"``.

Design choices (contract-first):

- The employer identity is fixed by this adapter, never taken from a remote
  self-claim; the canonical employer record lives in ``gcc-employers.v1.json``.
- Only ``open`` statuses are emitted; closed/other records are dropped so the
  pipeline never promotes a stale vacancy.
- No posting-date field is published by the endpoint, so precision is ``unknown``
  (never fabricated).
- Fail-closed: any malformed payload or non-200 discovery raises ``SourceError``;
  an empty board returns no jobs rather than invented ones.
- Never sends or submits; ``send_or_submit`` stays ``False`` at the report level.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso, unknown
from ..provenance import provenance as make_provenance

ENDPOINT = "https://hr.hma.sa/api/public-jobs"
CAREERS_PORTAL = "https://hr.hma.sa/"
_OPEN_STATUSES = {"مفتوحة", "open", "نشطة", "active", "published"}

_FIXTURE = "hma-public-jobs.json"


class HmaAdapter(SourceAdapter):
    source_id = "hma"
    source_name = "HMA Public Vacancy API"
    source_kind = "employer_page"
    official = True
    employer_name = "HMA"

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        endpoint = self._endpoint(company)
        payload = self._load(endpoint, offline)
        if not isinstance(payload, dict) or not payload.get("ok", False):
            raise SourceError("HMA public-jobs endpoint returned an unexpected payload")
        jobs_raw = payload.get("jobs")
        if not isinstance(jobs_raw, list):
            raise SourceError("HMA public-jobs payload missing 'jobs' list")
        requested = max(1, min(int(limit), 100))
        results: list[DiscoveryJob] = []
        for item in jobs_raw:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip()
            if status.lower() not in {s.lower() for s in _OPEN_STATUSES}:
                # The consultants scanner marks accepted official-source records
                # live, so a vacancy must carry affirmative open/active evidence.
                # Missing status therefore fails closed rather than becoming live.
                continue
            role = str(item.get("title") or "").strip()
            if not role:
                continue
            raw_id = str(item.get("id") or "").strip()
            if not raw_id:
                continue
            loc = str(item.get("city") or "").strip()
            if location and location.lower() not in loc.lower():
                continue
            description_html = str(item.get("description") or "")
            detail_url = f"{ENDPOINT}#{raw_id}"
            results.append(
                DiscoveryJob(
                    adapter_id=self.source_id,
                    company=self.employer_name,
                    role=role,
                    location=loc,
                    external_job_id=raw_id,
                    detail_url=detail_url,
                    application_url=CAREERS_PORTAL,
                    posted=unknown(self.source_id),
                    found_date=self._today(),
                    description_html=description_html,
                    description_text=html_to_text(description_html),
                    provenance=make_provenance(
                        source_id=self.source_id,
                        source_name=self.source_name,
                        source_kind=self.source_kind,
                        official=True,
                        extracted_from="HMA public-jobs API (hr.hma.sa/api/public-jobs)",
                        detail_url=detail_url,
                        raw_id=raw_id,
                    ),
                    extra={
                        "department": str(item.get("department") or ""),
                        "status": status,
                    },
                )
            )
            if len(results) >= requested:
                break
        return results

    def _endpoint(self, company: str) -> str:
        candidate = str(company or "").strip()
        if candidate.startswith("http://") or candidate.startswith("https://"):
            if candidate.rstrip("/").endswith("/api/public-jobs"):
                return candidate
            return candidate.rstrip("/") + "/api/public-jobs"
        return ENDPOINT

    def _load(self, url: str, offline: bool) -> Any:
        if offline:
            path = self._fixture_path(_FIXTURE)
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            return {"ok": True, "jobs": [], "count": 0}
        return network.fetch_json(url)

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
