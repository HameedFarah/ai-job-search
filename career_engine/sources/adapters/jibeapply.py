"""Public JibeApply / iCIMS branded JSON jobs adapter.

Jibe/iCIMS career sites commonly expose a zero-auth ``/api/jobs`` endpoint.
This adapter keeps the full description returned by that endpoint so Career
Engine can score the role, while reusing the same pagination contract as the
maintained Fighter90/career-ops-ui JibeApply integration.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import unknown
from ..provenance import provenance as make_provenance


class JibeApplyAdapter(SourceAdapter):
    source_id = "jibeapply"
    source_name = "JibeApply / iCIMS public jobs API"
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
        endpoint = self._endpoint(company)
        requested = max(1, min(int(limit), 100))
        payload = self._load(endpoint, offline=offline)
        rows = list(payload.get("jobs") or []) if isinstance(payload, dict) else []
        total = int(payload.get("totalCount") or len(rows)) if isinstance(payload, dict) else len(rows)
        page_size = len(rows) or int(payload.get("count") or 10) if isinstance(payload, dict) else 10
        page = 2
        while not offline and page_size > 0 and len(rows) < min(total, 1000) and page <= 100:
            next_url = endpoint + ("&" if "?" in endpoint else "?") + urlencode({"page": page})
            next_payload = network.fetch_json(next_url)
            fresh = list(next_payload.get("jobs") or []) if isinstance(next_payload, dict) else []
            if not fresh:
                break
            rows.extend(fresh)
            page += 1

        origin = urlunsplit((urlsplit(endpoint).scheme, urlsplit(endpoint).netloc, "", "", ""))
        results: list[DiscoveryJob] = []
        for item in rows:
            data = item.get("data") if isinstance(item, dict) else None
            data = data if isinstance(data, dict) else item if isinstance(item, dict) else {}
            role = str(data.get("title") or "").strip()
            slug = str(data.get("slug") or data.get("req_id") or "").strip()
            if not role or not slug:
                continue
            loc = str(data.get("full_location") or "").strip()
            if not loc:
                loc = ", ".join(str(x).strip() for x in (data.get("city"), data.get("country")) if x)
            if location and location.lower() not in loc.lower():
                continue
            description_html = str(data.get("description") or "")
            qualifications_html = str(data.get("qualifications") or "")
            if qualifications_html and qualifications_html not in description_html:
                description_html = f"{description_html}\n{qualifications_html}"
            detail_url = f"{origin}/jobs/{slug}"
            external_id = str(data.get("req_id") or slug).strip()
            company_name = str(data.get("hiring_organization") or data.get("brand") or "").strip()
            results.append(
                DiscoveryJob(
                    adapter_id=self.source_id,
                    company=company_name,
                    role=role,
                    location=loc,
                    external_job_id=external_id,
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
                        extracted_from="official JibeApply/iCIMS public /api/jobs endpoint",
                        detail_url=detail_url,
                        raw_id=external_id,
                    ),
                    extra={
                        "employment_type": data.get("employment_type") or "",
                        "categories": data.get("categories") or [],
                        "country_code": data.get("country_code") or "",
                    },
                )
            )
            if len(results) >= requested:
                break
        return results

    def _endpoint(self, raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            raise SourceError("JibeApply adapter requires an official careers/API URL")
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise SourceError("JibeApply adapter requires a public HTTPS careers/API URL")
        path = parsed.path.rstrip("/")
        if path.endswith("/api/jobs") or path == "/api/jobs":
            api_path = path
        elif parsed.netloc.lower().endswith(".jibeapply.com"):
            api_path = "/api" + (path if path.startswith("/") else f"/{path}")
        else:
            raise SourceError("Branded Jibe/iCIMS sources must provide their verified /api/jobs endpoint")
        return urlunsplit((parsed.scheme, parsed.netloc, api_path, parsed.query, ""))

    def _load(self, endpoint: str, *, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("jibeapply-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.fetch_json(endpoint)

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
