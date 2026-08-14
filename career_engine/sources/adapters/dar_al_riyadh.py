"""Dar Al Riyadh public careers API adapter.

The employer careers application is a public Angular/Kendo site. Its job list
is loaded from the unauthenticated employer endpoint:

    GET /Api/PublishedOpportnitiesAPI/GetPublishedOpportunities

The response already includes role, location, full HTML job description,
posting date, experience, department and internal requisition id. No login or
browser session is required.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_iso
from ..provenance import provenance as make_provenance


class DarAlRiyadhAdapter(SourceAdapter):
    source_id = "dar_al_riyadh"
    source_name = "Dar Al Riyadh Public Careers API"
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
        base = company.strip() or "https://careers.daralriyadh.com/"
        parsed = urlsplit(base if "://" in base else f"https://{base}")
        if not parsed.netloc or not parsed.netloc.lower().endswith("daralriyadh.com"):
            raise SourceError("Dar Al Riyadh adapter requires the official daralriyadh.com careers host")
        origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
        list_url = f"{origin}/Api/PublishedOpportnitiesAPI/GetPublishedOpportunities"
        payload = self._load(list_url, offline)
        if not isinstance(payload, list):
            raise SourceError("Dar Al Riyadh careers API returned an unexpected payload")

        requested = max(1, min(int(limit), 100))
        results: list[DiscoveryJob] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            loc = str(item.get("JobLocation") or "").strip()
            if location and location.lower() not in loc.lower():
                continue
            role = str(item.get("Position") or "").strip()
            if not role:
                continue
            raw_id = str(item.get("MRF_Ref") or item.get("Id") or "").strip()
            if not raw_id:
                continue
            detail_url = f"{origin}/#/JobDetails/{raw_id}"
            description_html = str(item.get("JobDescription") or "")
            extra: dict[str, Any] = {
                "department": str(item.get("Department") or ""),
                "experience_from": item.get("ExperienceFrom"),
                "experience_to": item.get("ExperienceTo"),
                "target_date": str(item.get("TargetDate") or ""),
                "position_count": item.get("PositionCount"),
            }
            results.append(
                DiscoveryJob(
                    adapter_id=self.source_id,
                    company="Dar Al Riyadh",
                    role=role,
                    location=loc,
                    external_job_id=raw_id,
                    detail_url=detail_url,
                    application_url=detail_url,
                    posted=parse_iso(item.get("PostedDate"), "Dar Al Riyadh PostedDate"),
                    found_date=self._today(),
                    description_html=description_html,
                    description_text=html_to_text(description_html),
                    provenance=make_provenance(
                        source_id=self.source_id,
                        source_name=self.source_name,
                        source_kind=self.source_kind,
                        official=True,
                        extracted_from="Dar Al Riyadh public PublishedOpportunities API",
                        detail_url=detail_url,
                        raw_id=raw_id,
                    ),
                    extra=extra,
                )
            )
            if len(results) >= requested:
                break
        return results

    def _load(self, url: str, offline: bool) -> Any:
        if offline:
            with open(self._fixture_path("dar-al-riyadh-list.json"), encoding="utf-8") as handle:
                return json.load(handle)
        return network.fetch_json(url)

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
