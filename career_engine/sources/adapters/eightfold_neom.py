"""Host-pinned public Eightfold adapter for NEOM.

The maintained generic Eightfold implementation deliberately accepts only
``*.eightfold.ai`` hosts.  NEOM fronts the same zero-auth jobs API on the
branded ``careers.neom.com`` hostname, so Career Engine uses this deliberately
narrow adapter rather than weakening the generic host trust rule.

Public endpoint:
    GET https://careers.neom.com/api/apply/v2/jobs?domain=neom.com&start=N&num=10

No login, cookie, token, application or external mutation is used.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError
from ..dates import parse_ms_epoch, unknown
from ..provenance import provenance as make_provenance

_HOST = "careers.neom.com"
_PAGE_SIZE = 10
_MAX_PAGES = 200


class NeomEightfoldAdapter(SourceAdapter):
    source_id = "eightfold_neom"
    source_name = "NEOM branded Eightfold public jobs API"
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
        name, domain = self._spec(company)
        requested = max(1, min(int(limit), 100))
        if offline:
            return []

        out: list[DiscoveryJob] = []
        seen: set[str] = set()
        total: int | None = None
        for page in range(_MAX_PAGES):
            start = page * _PAGE_SIZE
            params = {"start": str(start), "num": str(_PAGE_SIZE)}
            if domain:
                params["domain"] = domain
            endpoint = f"https://{_HOST}/api/apply/v2/jobs?{urlencode(params)}"
            payload = network.fetch_json(endpoint, max_bytes=4 * 1024 * 1024)
            if not isinstance(payload, dict):
                raise SourceError("NEOM Eightfold API returned an unexpected payload")
            positions = payload.get("positions")
            if positions is None:
                positions = payload.get("jobs")
            if not isinstance(positions, list):
                raise SourceError("NEOM Eightfold API did not expose a positions list")
            if total is None and isinstance(payload.get("count"), int):
                total = int(payload["count"])

            for item in positions:
                if not isinstance(item, dict):
                    continue
                job = self._map(item, name=name, domain=domain)
                if job is None or job.detail_url in seen:
                    continue
                seen.add(job.detail_url)
                if location and not self._location_matches(location, job.location):
                    continue
                out.append(job)
                if len(out) >= requested:
                    return out

            if len(positions) < _PAGE_SIZE:
                break
            if total is not None and start + _PAGE_SIZE >= total:
                break
        return out

    @staticmethod
    def _spec(value: str) -> tuple[str, str | None]:
        raw = str(value or "").strip()
        name = "NEOM"
        if "|" in raw:
            name, raw = [part.strip() for part in raw.split("|", 1)]
        parsed = urlsplit(raw)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != _HOST:
            raise SourceError("NEOM Eightfold adapter is pinned to https://careers.neom.com")
        domain = (parse_qs(parsed.query).get("domain") or [None])[0]
        return name or "NEOM", domain

    def _map(self, item: dict, *, name: str, domain: str | None) -> DiscoveryJob | None:
        role = str(item.get("name") or item.get("posting_name") or "").strip()
        raw_id = str(item.get("id") or "").strip()
        if not role or not raw_id:
            return None
        canonical = str(item.get("canonicalPositionUrl") or "").strip()
        if canonical:
            parsed = urlsplit(canonical)
            detail_url = canonical if parsed.scheme == "https" and parsed.hostname else ""
        else:
            detail_url = ""
        if not detail_url:
            params = {"pid": raw_id}
            if domain:
                params["domain"] = domain
            detail_url = f"https://{_HOST}/careers?{urlencode(params)}"
        locations: list[str] = []
        flat = str(item.get("location") or "").strip()
        if flat:
            locations.append(flat)
        if isinstance(item.get("locations"), list):
            locations.extend(str(v).strip() for v in item["locations"] if str(v).strip())
        loc = " · ".join(dict.fromkeys(locations))
        posted = unknown(self.source_id)
        timestamp = item.get("t_create") or item.get("t_update")
        if timestamp not in (None, ""):
            try:
                posted = parse_ms_epoch(int(float(timestamp)) * 1000, "NEOM Eightfold t_create/t_update")
            except (TypeError, ValueError, OverflowError):
                posted = unknown(self.source_id)
        snippet = "\n".join(
            part for part in (
                str(item.get("department") or "").strip(),
                str(item.get("business_unit") or "").strip(),
            ) if part
        )
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=name,
            role=role,
            location=loc,
            external_job_id=raw_id,
            detail_url=detail_url,
            application_url=detail_url,
            posted=posted,
            found_date=self._today(),
            description_text=snippet,
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="NEOM branded Eightfold public jobs API",
                detail_url=detail_url,
                raw_id=raw_id,
            ),
        )

    @staticmethod
    def _location_matches(requested: str, actual: str) -> bool:
        wanted = requested.strip().lower()
        found = actual.strip().lower()
        if wanted in found:
            return True
        if wanted == "saudi arabia":
            return any(token in found for token in ("saudi arabia", "ksa", "riyadh", "neom", "tabuk", "jeddah"))
        return False

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
