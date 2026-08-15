"""Host-pinned official career adapter for NEOM.

NEOM's canonical careers surface fronts Eightfold on ``careers.neom.com``.
When that API successfully reports zero positions, NEOM also has a separate
first-party Virtual Career Fair at ``candidatejourney.neom.com`` whose public
page explicitly labels current role names as ``Open positions`` and says
candidates can apply directly to available jobs.

The adapter therefore uses this order:

1. query the canonical branded Eightfold API;
2. if it returns positions, use only those positions;
3. only if the API explicitly returns ``count: 0``, query the public NEOM
   Career Fair and emit its listed open-position titles;
4. fail closed if either authoritative surface changes shape or is unavailable.

This keeps one NEOM source identity, avoids duplicate counting when Eightfold is
populated, and never logs in, registers, submits, bypasses access controls or
uses a non-NEOM discovery source.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_ms_epoch, unknown
from ..provenance import provenance as make_provenance

_HOST = "careers.neom.com"
_CAREER_FAIR_HOST = "candidatejourney.neom.com"
_CAREER_FAIR_URL = f"https://{_CAREER_FAIR_HOST}/"
_PAGE_SIZE = 10
_MAX_PAGES = 200
_OPEN_POSITIONS_RE = re.compile(r"open\s+positions\s*:? ?", re.I)
_SECTION_END_RE = re.compile(r"(?:#?\s*sponsors\b|frequently\s+asked\s+questions?)", re.I)
_LI_RE = re.compile(r"<li\b[^>]*>([\s\S]*?)</li>", re.I)
_ROLE_SAFE_RE = re.compile(r"^[A-Za-z0-9&/()+,.\-'–— ]{3,120}$")


class NeomEightfoldAdapter(SourceAdapter):
    source_id = "eightfold_neom"
    source_name = "NEOM official careers surfaces"
    source_kind = "official_career_surface"
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
            if total is None:
                count_value = payload.get("count")
                if not isinstance(count_value, int):
                    raise SourceError("NEOM Eightfold API did not expose an explicit position count")
                total = int(count_value)

            for item in positions:
                if not isinstance(item, dict):
                    continue
                job = self._map(item, name=name, domain=domain)
                if job is None or job.dedupe_key() in seen:
                    continue
                seen.add(job.dedupe_key())
                if location and not self._location_matches(location, job.location):
                    continue
                out.append(job)
                if len(out) >= requested:
                    return out

            if len(positions) < _PAGE_SIZE:
                break
            if total is not None and start + _PAGE_SIZE >= total:
                break

        if out:
            return out
        if total != 0:
            # The canonical API claimed jobs existed but none could be mapped or
            # matched. Never silently turn that state into a verified empty board.
            raise SourceError(
                f"NEOM Eightfold reported {total} positions but no verified jobs were emitted"
            )
        return self._career_fair_jobs(name=name, location=location, limit=requested)

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

    def _career_fair_jobs(
        self,
        *,
        name: str,
        location: str | None,
        limit: int,
    ) -> list[DiscoveryJob]:
        html = network.fetch_text(_CAREER_FAIR_URL, max_bytes=4 * 1024 * 1024)
        roles = self._parse_career_fair_roles(html)
        jobs: list[DiscoveryJob] = []
        for role in roles:
            job_location = "NEOM"
            if location and not self._location_matches(location, job_location):
                continue
            raw_id = "career-fair:" + re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
            jobs.append(
                DiscoveryJob(
                    adapter_id=self.source_id,
                    company=name,
                    role=role,
                    location=job_location,
                    external_job_id=raw_id,
                    detail_url=_CAREER_FAIR_URL,
                    application_url=_CAREER_FAIR_URL,
                    posted=unknown("NEOM Virtual Career Fair open-positions page"),
                    found_date=self._today(),
                    # The public page proves the title is open but does not expose
                    # a full JD without event registration. Leave the description
                    # empty so the central scanner retains Manual Review Needed.
                    description_text="",
                    provenance=make_provenance(
                        source_id=self.source_id,
                        source_name="NEOM Virtual Career Fair",
                        source_kind="employer_event_page",
                        official=True,
                        extracted_from="public NEOM Virtual Career Fair Open positions list",
                        detail_url=_CAREER_FAIR_URL,
                        raw_id=raw_id,
                    ),
                    extra={
                        "canonical_eightfold_count": 0,
                        "career_fair_fallback": True,
                        "full_jd_publicly_available": False,
                    },
                )
            )
            if len(jobs) >= limit:
                break
        return jobs

    @staticmethod
    def _parse_career_fair_roles(html: str) -> list[str]:
        marker = _OPEN_POSITIONS_RE.search(html or "")
        if marker is None:
            # Some front ends place the heading text in nested tags. Plain-text
            # extraction is a second, still deterministic, shape check.
            text = html_to_text(html)
            text_marker = _OPEN_POSITIONS_RE.search(text)
            if text_marker is None:
                raise SourceError("NEOM Career Fair did not expose an Open positions section")
            tail = text[text_marker.end():]
            end_match = _SECTION_END_RE.search(tail)
            segment = tail[: end_match.start()] if end_match else tail[:5000]
            candidates = [line.strip(" •\t") for line in segment.splitlines()]
        else:
            tail = html[marker.end():]
            end_match = _SECTION_END_RE.search(tail)
            section = tail[: end_match.start()] if end_match else tail[:20000]
            candidates = [html_to_text(value).strip() for value in _LI_RE.findall(section)]
            if not candidates:
                candidates = [line.strip(" •\t") for line in html_to_text(section).splitlines()]

        roles: list[str] = []
        seen: set[str] = set()
        blocked = {
            "open positions", "apply for jobs", "previous", "next", "login", "register",
            "platinum", "gold", "silver", "sponsors",
        }
        for candidate in candidates:
            role = re.sub(r"\s+", " ", candidate).strip(" :-")
            if not role or role.lower() in blocked:
                continue
            if not _ROLE_SAFE_RE.fullmatch(role):
                continue
            key = role.casefold()
            if key in seen:
                continue
            seen.add(key)
            roles.append(role)
        return roles

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
                source_name="NEOM branded Eightfold public jobs API",
                source_kind="ats_api",
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
