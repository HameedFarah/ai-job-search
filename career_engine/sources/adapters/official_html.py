"""Bounded first-party HTML career-page adapter.

This adapter is intentionally small and preset-driven.  It is used only for
employer-owned career pages whose public HTML is the authoritative listing
surface and where a maintained ATS API is not a better fit.

Supported presets:

- ``saudconsult``: SaudConsult's current careers page;
- ``othaim``: Abdullah Al Othaim Investment careers page/sentinel;
- ``applytojob``: employer-owned ApplyToJob boards;
- ``tribepad``: Tribepad public vacancy pages (Buro Happold);
- ``wpjm``: WordPress Job Manager listing pages (Meinhardt).

The adapter never treats an unrecognised page as an empty board: if no vacancy
records are parsed and no preset-specific empty marker is present, it fails
closed with ``SourceError``.  Detail hydration happens only after location
filtering and limiting so a global board cannot fan out into unbounded detail
requests.
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urljoin, urlsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import unknown
from ..provenance import provenance as make_provenance

_A_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', re.I)
_H3_RE = re.compile(r'<h3\b[^>]*>([\s\S]*?)</h3>', re.I)
_LOCATION_CLASS_RE = re.compile(
    r'<(?:div|span|li)\b[^>]*class=["\'][^"\']*(?:location|job-location)[^"\']*["\'][^>]*>([\s\S]*?)</(?:div|span|li)>',
    re.I,
)
_JOB_LI_RE = re.compile(r'<li\b[^>]*class=["\'][^"\']*\bjob_listing\b[^"\']*["\'][^>]*>([\s\S]*?)</li>', re.I)
_WPJM_CONTAINER_RE = re.compile(
    r'<(?P<tag>li|article|div)\b[^>]*class=["\'][^"\']*\bjob[_-]listing\b[^"\']*["\'][^>]*>(?P<body>[\s\S]*?)</(?P=tag)>',
    re.I,
)
_WPJM_TITLE_RE = re.compile(r'<(?:h2|h3|h4)\b[^>]*>([\s\S]*?)</(?:h2|h3|h4)>', re.I)
_TRIBEPAD_TITLE_RE = re.compile(r'class=["\'][^"\']*\bjob-list-title\b[^"\']*["\'][^>]*>([\s\S]*?)</span>', re.I)
_TRIBEPAD_ADDRESS_RE = re.compile(r'itemprop=["\']address["\'][^>]*>([\s\S]*?)</span>', re.I)
_TRIBEPAD_NEXT_RE = re.compile(r'(?:Next results page|Page number\s+\d+)', re.I)
_MAX_TRIBEPAD_PAGES = 20
_MAIN_RE = re.compile(r'<(?:main|article)\b[^>]*>([\s\S]*?)</(?:main|article)>', re.I)
_ID_RE = re.compile(r'(?<!\d)(\d{2,})(?!\d)')

_EMPTY_MARKERS = {
    "saudconsult": (
        "there are no openings in this category at the moment",
        "no openings in this category",
        "لا توجد وظائف شاغرة",
    ),
    "othaim": ("no record found", "no jobs found", "no vacancies"),
    "applytojob": ("there are no open positions at this time", "no open positions"),
    "tribepad": ("there are currently no jobs", "no vacancies found", "no jobs found"),
    "wpjm": ("showing all 0 jobs", "no jobs found", "there are no listings matching"),
}


def _today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _clean(value: str) -> str:
    return html_to_text(unescape(value or "")).strip()


def _location_matches(requested: str | None, actual: str) -> bool:
    wanted = (requested or "").strip().lower()
    found = (actual or "").strip().lower()
    if not wanted:
        return True
    if wanted in found:
        return True
    if wanted == "saudi arabia":
        return any(token in found for token in (
            "saudi arabia", "kingdom of saudi arabia", "ksa", "riyadh", "jeddah",
            "dammam", "khobar", "jubail", "dhahran", "tabuk", "neom", "dhalm",
        ))
    return False


def _context_location(block: str) -> str:
    match = _LOCATION_CLASS_RE.search(block)
    if match:
        return _clean(match.group(1))
    text = _clean(block)
    # Prefer an explicit Saudi location when present; this keeps the parser
    # deterministic without fabricating a location from the requested filter.
    match = re.search(
        r'((?:Riyadh|Jeddah|Dammam|Khobar|Jubail|Dhahran|Tabuk|Dhalm|NEOM)(?:\s*,[^\n|]{0,45})?(?:Saudi Arabia|KSA)?)',
        text,
        re.I,
    )
    if match:
        return match.group(1).strip()
    if re.search(r'\bSaudi Arabia\b|\bKSA\b', text, re.I):
        return "Saudi Arabia"
    return ""


def _anchor_context(html: str, start: int, end: int) -> str:
    """Return the smallest plausible listing container around one job link.

    A broad character window can leak a neighbouring card's Saudi location into
    a global vacancy. Prefer the nearest enclosing list/article/section/div
    block; use a deliberately small fallback window only when the page has no
    useful container markup.
    """
    candidates: list[tuple[int, str]] = []
    for tag in ("li", "article", "section", "div"):
        opener = html.rfind(f"<{tag}", 0, start)
        if opener >= 0:
            candidates.append((opener, tag))
    for opener, tag in sorted(candidates, reverse=True):
        closer = html.find(f"</{tag}>", end)
        if closer >= end:
            return html[opener : closer + len(tag) + 3]
    return html[max(0, start - 240) : min(len(html), end + 240)]


class OfficialHtmlAdapter(SourceAdapter):
    source_id = "official_html"
    source_name = "First-party employer careers HTML"
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
        name, base, preset = self._spec(company)
        requested = max(1, min(int(limit), 100))

        if offline:
            # Offline consultant-registry tests exercise routing, not these live
            # sites. Return a deterministic empty page only for known presets;
            # parser behaviour itself is covered with mocked live HTML tests.
            pages = [(base, self._offline_html(preset))]
        elif preset == "tribepad":
            pages = self._fetch_tribepad_pages(base)
        else:
            pages = [(base, network.fetch_text(base, max_bytes=4 * 1024 * 1024))]

        rows: list[tuple[str, str, str, str]] = []
        saw_parseable_page = False
        for page_url, html in pages:
            page_rows = self._parse(page_url, html, preset)
            if page_rows:
                saw_parseable_page = True
                rows.extend(page_rows)
                continue
            lowered = _clean(html).lower()
            if any(marker in lowered for marker in _EMPTY_MARKERS[preset]):
                saw_parseable_page = True

        if not rows and not saw_parseable_page:
            raise SourceError(
                f"{name} careers page matched no {preset} vacancies and no verified empty-state marker"
            )

        jobs: list[DiscoveryJob] = []
        seen_urls: set[str] = set()
        for raw_id, role, loc, detail_url in rows:
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)
            if not _location_matches(location, loc):
                continue
            jobs.append(self._job(name, raw_id, role, loc, detail_url))
            if len(jobs) >= requested:
                break

        if fetch_full and not offline:
            for job in jobs:
                try:
                    detail = network.fetch_text(job.detail_url, max_bytes=4 * 1024 * 1024)
                    match = _MAIN_RE.search(detail)
                    job.description_text = _clean(match.group(1) if match else detail)
                except SourceError as exc:
                    job.extra["detail_fetch_error"] = str(exc)
        return jobs

    @staticmethod
    def _spec(value: str) -> tuple[str, str, str]:
        parts = [part.strip() for part in str(value or "").split("|", 2)]
        if len(parts) != 3 or not all(parts):
            raise SourceError("official_html company spec must be Company|https://careers-url|preset")
        name, base, preset = parts
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SourceError("official_html requires an employer-owned HTTPS careers URL")
        if preset not in _EMPTY_MARKERS:
            raise SourceError(f"unsupported official_html preset: {preset}")
        return name, base, preset

    def _fetch_tribepad_pages(self, base: str) -> list[tuple[str, str]]:
        pages: list[tuple[str, str]] = []
        root = base.rstrip("/")
        previous_urls: set[str] = set()
        for page in range(1, _MAX_TRIBEPAD_PAGES + 1):
            url = base if page == 1 else f"{root}/{page}"
            html = network.fetch_text(url, max_bytes=4 * 1024 * 1024)
            pages.append((url, html))
            rows = self._parse(url, html, "tribepad")
            urls = {row[3] for row in rows}
            if not rows:
                break
            if urls and urls == previous_urls:
                break
            previous_urls = urls
            if not _TRIBEPAD_NEXT_RE.search(_clean(html)):
                break
        return pages

    def _parse(self, base: str, html: str, preset: str) -> list[tuple[str, str, str, str]]:
        if preset == "wpjm":
            rows = self._parse_wpjm(base, html)
        else:
            rows = self._parse_anchors(base, html, preset)
        out: list[tuple[str, str, str, str]] = []
        seen: set[str] = set()
        for raw_id, role, loc, url in rows:
            if not role or not url or url in seen:
                continue
            seen.add(url)
            out.append((raw_id or url, role, loc, url))
        return out

    def _parse_wpjm(self, base: str, html: str) -> list[tuple[str, str, str, str]]:
        out: list[tuple[str, str, str, str]] = []
        parsed_spans: set[tuple[int, int]] = set()
        containers = list(_WPJM_CONTAINER_RE.finditer(html or ""))
        # Keep compatibility with the original narrow matcher if a malformed
        # or unusually nested legacy card is not captured by the generic one.
        containers.extend(_JOB_LI_RE.finditer(html or ""))
        for match in containers:
            span = match.span()
            if span in parsed_spans:
                continue
            parsed_spans.add(span)
            opening_tag = match.group(0).split(">", 1)[0]
            # WP Job Manager keeps expired listings in public archives. They are
            # historical evidence, not live vacancies.
            if re.search(r"\bstatus-expired\b", opening_tag, re.I):
                continue
            block = match.groupdict().get("body") or match.group(1)
            href_match = _A_RE.search(block)
            title_match = _WPJM_TITLE_RE.search(block)
            if not href_match or not title_match:
                continue
            url = urljoin(base, unescape(href_match.group(1)))
            parsed_base = urlsplit(base)
            parsed_url = urlsplit(url)
            if (parsed_url.scheme, parsed_url.netloc) != (parsed_base.scheme, parsed_base.netloc):
                continue
            query = parse_qs(parsed_url.query)
            is_path_job = bool(re.fullmatch(r"/job/[^/]+/?", parsed_url.path, re.I))
            query_id = (query.get("p") or [""])[0]
            is_query_job = query.get("post_type") == ["job_listing"] and bool(query_id)
            if not is_path_job and not is_query_job:
                continue
            role = _clean(title_match.group(1))
            loc = _context_location(block)
            slug = parsed_url.path.rstrip("/").split("/")[-1]
            raw_id = query_id if is_query_job else slug
            out.append((raw_id, role, loc, url))
        if not out:
            # Current themes may use nested div cards, which cannot be safely
            # matched with a regex spanning balanced markup. The anchor is a
            # bounded, role-specific unit and contains the title/location in
            # WPJM's current card markup.
            parsed_base = urlsplit(base)
            for match in _A_RE.finditer(html or ""):
                url = urljoin(base, unescape(match.group(1)))
                parsed_url = urlsplit(url)
                if (parsed_url.scheme, parsed_url.netloc) != (parsed_base.scheme, parsed_base.netloc):
                    continue
                query = parse_qs(parsed_url.query)
                is_path_job = bool(re.fullmatch(r"/job/[^/]+/?", parsed_url.path, re.I))
                query_id = (query.get("p") or [""])[0]
                is_query_job = query.get("post_type") == ["job_listing"] and bool(query_id)
                if not is_path_job and not is_query_job:
                    continue
                context = _anchor_context(html, match.start(), match.end())
                # Query-style URLs are generic WordPress routes. Accept them only
                # when the anchor is actually enclosed by a singular job_listing
                # card; a loose marketing/archive link is not a live vacancy.
                if is_query_job and not re.search(
                    r'class=["\'][^"\']*\bjob_listing\b[^"\']*["\']', context, re.I
                ):
                    continue
                if re.search(r"\bstatus-expired\b", context, re.I):
                    continue
                block = match.group(0)
                title_match = _WPJM_TITLE_RE.search(block)
                if not title_match:
                    continue
                role = _clean(title_match.group(1))
                loc = _context_location(block) or _context_location(context)
                slug = parsed_url.path.rstrip("/").split("/")[-1]
                raw_id = query_id if is_query_job else slug
                out.append((raw_id, role, loc, url))
        return out

    def _parse_anchors(self, base: str, html: str, preset: str) -> list[tuple[str, str, str, str]]:
        out: list[tuple[str, str, str, str]] = []
        for match in _A_RE.finditer(html or ""):
            href = unescape(match.group(1)).strip()
            inner = match.group(2)
            role = _clean(inner)
            if not href or not role:
                continue
            url = urljoin(base, href)
            path = urlsplit(url).path.lower()
            if preset == "tribepad":
                if "/jobs/job/" not in path:
                    continue
                title_match = _TRIBEPAD_TITLE_RE.search(inner)
                role = _clean(title_match.group(1)) if title_match else role
                address_match = _TRIBEPAD_ADDRESS_RE.search(inner)
                if address_match:
                    loc = _clean(address_match.group(1))
                else:
                    loc = _context_location(_anchor_context(html, match.start(), match.end()))
            else:
                loc = ""
            if preset == "applytojob" and "/apply/" not in path:
                continue
            if preset == "applytojob" and path.rstrip("/").endswith("/apply"):
                continue
            if preset == "saudconsult" and not any(token in path for token in ("/job/", "/jobs/", "/career")):
                continue
            if preset == "othaim" and not any(token in path for token in ("job", "career", "vacanc")):
                continue
            # Ignore generic navigation labels rather than promoting them as jobs.
            if role.lower() in {"jobs", "careers", "career", "apply", "apply now", "view jobs", "show more jobs"}:
                continue
            if preset != "tribepad":
                block = _anchor_context(html, match.start(), match.end())
                loc = _context_location(block)
            id_match = _ID_RE.search(urlsplit(url).path)
            raw_id = id_match.group(1) if id_match else urlsplit(url).path.rstrip("/").split("/")[-1]
            out.append((raw_id, role, loc, url))
        return out

    def _job(self, company: str, raw_id: str, role: str, location: str, url: str) -> DiscoveryJob:
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=company,
            role=role,
            location=location,
            external_job_id=raw_id,
            detail_url=url,
            application_url=url,
            posted=unknown(self.source_id),
            found_date=_today(),
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from="first-party employer careers HTML",
                detail_url=url,
                raw_id=raw_id,
            ),
        )

    @staticmethod
    def _offline_html(preset: str) -> str:
        markers = {
            "saudconsult": "There are no openings in this category at the moment.",
            "othaim": "No record found",
            "applytojob": "There are no open positions at this time.",
            "tribepad": "No jobs found",
            "wpjm": "Showing all 0 jobs",
        }
        return markers[preset]
