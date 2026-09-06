"""Free first-party employment-route discovery for verified company websites.

This module deliberately does not validate mailbox deliverability. It only
establishes that a route is published by the verified official website.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Iterable
from urllib.parse import urljoin, urlsplit

import httpx

ATS_HOST_TOKENS = (
    "workday", "taleo", "successfactors", "oraclecloud", "greenhouse",
    "lever.co", "ashbyhq", "smartrecruiters", "avature", "icims", "brassring",
)
EMPLOYMENT_TERMS = (
    "career", "careers", "job", "jobs", "vacancy", "vacancies", "join us",
    "join our team", "work with us", "opportunities", "employment", "recruitment",
    "talent", "apply now", "submit cv", "send cv", "send your cv", "resume",
    "وظائف", "الوظائف", "فرص وظيفية", "فرص العمل", "التوظيف", "انضم إلينا",
    "انضم لفريقنا", "السيرة الذاتية", "قدم الآن", "تقديم طلب",
)
SOFT_404_TERMS = (
    "page not found", "404 not found", "not found", "the page you requested could not be found",
    "الصفحة غير موجودة", "عذراً الصفحة غير موجودة",
)
RECRUITMENT_LOCALS = {
    "hr", "career", "careers", "job", "jobs", "recruit", "recruitment",
    "talent", "hiring", "people", "peopleandculture", "humanresources",
}
GENERAL_LOCALS = {"info", "contact", "contactus", "hello", "office", "admin", "inquiry", "enquiry"}
COMMON_PATHS = (
    "/careers", "/career", "/jobs", "/vacancies", "/join-us", "/joinus",
    "/work-with-us", "/recruitment", "/ar/careers", "/ar/jobs",
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class EmploymentRoute:
    kind: str
    value: str
    source_url: str
    evidence: str
    email_kind: str = ""

    @property
    def is_portal(self) -> bool:
        return self.kind in {"ats", "careers_page"}

    @property
    def is_email(self) -> bool:
        return self.kind == "email"


class _HTMLCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.mailtos: list[str] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "a":
            href = attrs_dict.get("href", "").strip()
            self._anchor_href = href
            self._anchor_text = []
            if href.lower().startswith("mailto:"):
                self.mailtos.append(href[7:].split("?", 1)[0].strip())

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.text_parts.append(value)
            if self._anchor_href:
                self._anchor_text.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._anchor_href:
            self.links.append((self._anchor_href, " ".join(self._anchor_text).strip()))
            self._anchor_href = ""
            self._anchor_text = []


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _same_site(url: str, official_host: str) -> bool:
    host = _host(url)
    return bool(host and (host == official_host or host.endswith("." + official_host)))


def _is_ats_url(url: str) -> bool:
    host = _host(url)
    low = url.lower()
    return bool(host and any(token in host or token in low for token in ATS_HOST_TOKENS))


def _employment_score(title: str, text: str, url: str) -> int:
    haystack = " ".join([title, text[:12000], url]).lower()
    return sum(1 for term in EMPLOYMENT_TERMS if term in haystack)


def _looks_like_soft_404(title: str, text: str, status_code: int) -> bool:
    if status_code == 404:
        return True
    haystack = f"{title} {text[:2500]}".lower()
    if "404" in title.lower():
        return True
    return any(term in haystack for term in SOFT_404_TERMS)


def _extract_emails(text: str, mailtos: Iterable[str], official_host: str) -> list[tuple[str, str]]:
    candidates: list[str] = []
    candidates.extend(mailtos)
    candidates.extend(EMAIL_RE.findall(text))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in candidates:
        email = raw.strip().lower().strip(".,;:()[]<>\"'")
        if email in seen or "@" not in email:
            continue
        seen.add(email)
        local, domain = email.rsplit("@", 1)
        domain = domain.removeprefix("www.")
        if not (domain == official_host or domain.endswith("." + official_host)):
            continue
        if local in RECRUITMENT_LOCALS:
            out.append((email, "recruitment"))
        elif local in GENERAL_LOCALS:
            out.append((email, "general"))
    out.sort(key=lambda item: (0 if item[1] == "recruitment" else 1, item[0]))
    return out


def _parse_html(html: str) -> _HTMLCollector:
    parser = _HTMLCollector()
    parser.feed(html)
    return parser


def discover_employment_route(
    official_url: str,
    *,
    timeout_seconds: float = 8.0,
    client: httpx.Client | None = None,
) -> EmploymentRoute | None:
    """Return the strongest free employment route linked/published by the verified site.

    Priority is an official-site-linked ATS, then a qualified careers/apply page,
    then a recruitment mailbox, then a general official-domain mailbox. Any
    mailbox result remains only a candidate until the scanner validates it.
    """
    official_host = _host(official_url)
    if not official_host:
        return None
    root = f"https://{official_host}/"
    owns_client = client is None
    http = client or httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; REGA-enrichment/2.0)"},
    )
    try:
        try:
            home_resp = http.get(root)
            home_resp.raise_for_status()
        except Exception:
            home_resp = http.get(official_url)
            home_resp.raise_for_status()
        home_html = home_resp.text or ""
        home = _parse_html(home_html)
        home_text = " ".join(home.text_parts)

        normalized_links: list[tuple[str, str]] = []
        for href, label in home.links:
            if not href or href.startswith(("#", "javascript:", "tel:", "mailto:")):
                continue
            absolute = urljoin(str(home_resp.url), href)
            if absolute.startswith(("http://", "https://")):
                normalized_links.append((absolute, label))

        # External ATS is trusted only when the verified official site links to it.
        for url, label in normalized_links:
            if _is_ats_url(url) and _employment_score(label, label, url) >= 1:
                return EmploymentRoute(
                    kind="ats",
                    value=url,
                    source_url=str(home_resp.url),
                    evidence=f"Official homepage links to employment ATS: {label or url}",
                )

        candidate_urls: list[str] = []
        for url, label in normalized_links:
            if _same_site(url, official_host) and _employment_score(label, "", url) >= 1:
                candidate_urls.append(url)
        candidate_urls.extend(urljoin(root, path) for path in COMMON_PATHS)

        seen_urls: set[str] = set()
        for url in candidate_urls:
            clean = url.split("#", 1)[0]
            if clean in seen_urls:
                continue
            seen_urls.add(clean)
            try:
                response = http.get(clean)
                status = int(response.status_code)
                if status >= 400:
                    continue
                html = response.text or ""
            except Exception:
                continue
            parsed = _parse_html(html)
            text = " ".join(parsed.text_parts)
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            title = re.sub(r"<[^>]+>", " ", title_match.group(1)).strip() if title_match else ""
            if _looks_like_soft_404(title, text, status):
                continue
            if _employment_score(title, text, str(response.url)) < 2:
                continue

            parsed_links: list[tuple[str, str]] = []
            for href, label in parsed.links:
                if not href or href.startswith(("#", "javascript:", "tel:", "mailto:")):
                    continue
                parsed_links.append((urljoin(str(response.url), href), label))
            for target, label in parsed_links:
                if _is_ats_url(target):
                    return EmploymentRoute(
                        kind="ats",
                        value=target,
                        source_url=str(response.url),
                        evidence=f"Qualified official careers page links to ATS/application endpoint: {label or target}",
                    )

            application_signal = any(
                term in f"{title} {text[:12000]}".lower()
                for term in (
                    "apply", "submit cv", "send cv", "vacanc", "job opening", "open position",
                    "تقديم", "السيرة الذاتية", "وظائف", "فرص وظيفية",
                )
            )
            has_form = bool(re.search(r"<form\b", html, re.I))
            if application_signal or has_form:
                return EmploymentRoute(
                    kind="careers_page",
                    value=str(response.url),
                    source_url=str(response.url),
                    evidence="Qualified first-party careers/jobs page with candidate/application signals.",
                )

            page_emails = _extract_emails(text, parsed.mailtos, official_host)
            if page_emails:
                email, email_kind = page_emails[0]
                return EmploymentRoute(
                    kind="email",
                    value=email,
                    source_url=str(response.url),
                    evidence="Official employment page publishes a company-domain contact mailbox.",
                    email_kind=email_kind,
                )

        home_emails = _extract_emails(home_text, home.mailtos, official_host)
        if home_emails:
            email, email_kind = home_emails[0]
            return EmploymentRoute(
                kind="email",
                value=email,
                source_url=str(home_resp.url),
                evidence="Verified official homepage publishes a company-domain mailbox.",
                email_kind=email_kind,
            )
        return None
    finally:
        if owns_client:
            http.close()
