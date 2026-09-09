"""Deterministic REGA employer-resolution helpers.

This module is intentionally side-effect free except for the optional public
Muqawil lookup. It does not send email, create drafts, mutate Google Sheets, or
call paid providers.

The scoring is an execution-priority heuristic only. It is not a statement of
employer quality, user fit, or hiring probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Mapping
from urllib.parse import urlsplit

import httpx

from .config import GENERIC_TOKENS
from .discovery import searxng_qwant_search
from .models import CompanyRecord, distinctive_tokens


MUQAWIL_HOSTS = {"muqawil.org", "www.muqawil.org"}
MUQAWIL_PATH_RE = re.compile(r"^/(?:en|ar)/contractors/\d+/\d+/?$")
SIZE_POINTS = {
    "big": 20,
    "large": 20,
    "medium": 12,
    "small": 4,
    "very small": 0,
}
REGION_POINTS = {
    "riyadh": 20,
    "jeddah": 12,
    "makkah": 8,
    "eastern prov.": 12,
    "eastern province": 12,
    "الرياض": 20,
    "جدة": 12,
    "مكة": 8,
    "الشرقية": 12,
}
ACTIVE_PROJECT_TERMS = (
    "under construction",
    "current project",
    "active project",
    "off-plan",
    "under-construction",
    "project footprint",
    "قيد التنفيذ",
    "جارى التنفيد",
    "جاري التنفيذ",
    "تحت الإنشاء",
    "مشروع",
)
HIRING_TERMS = (
    "careers",
    "career",
    "hiring",
    "vacancy",
    "vacancies",
    "recruitment",
    "jobs",
    "job",
    "وظائف",
    "توظيف",
    "التوظيف",
)
OPERATING_TERMS = (
    "active developer",
    "verified company",
    "official company",
    "official domain",
    "first-party",
    "developer licence",
    "developer license",
    "فعال",
    "مطور عقاري",
)
WEAK_STRUCTURE_TERMS = (
    "branch",
    "فرع",
    "شركة شخص واحد",
    "one person",
    "person one",
)
ARABIC_GENERIC_TOKENS = {
    "شركة", "شركه", "العقارية", "العقاري", "عقارية", "عقاري", "للتطوير", "تطوير",
    "للاستثمار", "الاستثمار", "استثمار", "للمقاولات", "المقاولات", "مقاولات",
    "المحدودة", "محدودة", "مجموعة", "السعودية", "السعوديه", "القابضة", "القابضه",
    "مؤسسة", "موسسة", "للتجارة", "التجارة", "العامة", "عامه",
}


@dataclass(frozen=True)
class MuqawilProfile:
    """Verified public Muqawil contractor profile metadata.

    Email is deliberately not exposed or promoted from this structure. Muqawil
    is used here as identity/company-size evidence only; campaign mailboxes must
    still pass the existing first-party/validator rules.
    """

    name: str
    url: str
    company_size: str = ""
    membership_number: str = ""
    status: str = ""
    city_region: str = ""
    evidence: str = ""


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.parts.append(value)


def _flat_text(html: str) -> str:
    parser = _TextCollector()
    parser.feed(html or "")
    return " | ".join(parser.parts)


def _normalize_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower())


def _arabic_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[\u0600-\u06FF]+", str(value or ""))
        if len(token) >= 3 and token not in ARABIC_GENERIC_TOKENS
    ]


def _muqawil_url(value: str) -> bool:
    parsed = urlsplit(str(value or ""))
    host = (parsed.hostname or "").lower()
    return host in MUQAWIL_HOSTS and bool(MUQAWIL_PATH_RE.match(parsed.path))


def _identity_matches(company: CompanyRecord, text: str) -> bool:
    low = str(text or "").lower()
    english = distinctive_tokens(company.english_name, GENERIC_TOKENS)
    if english:
        page_tokens = set(_normalize_words(low))
        hits = sum(1 for token in english if token in page_tokens)
        required = 2 if len(english) >= 2 else 1
        if hits >= required:
            return True

    arabic = _arabic_tokens(company.arabic_name)
    if arabic:
        hits = sum(1 for token in arabic if token in text)
        required = 2 if len(arabic) >= 3 else 1
        if hits >= required:
            return True
    return False


def parse_muqawil_profile(company: CompanyRecord, url: str, html: str) -> MuqawilProfile | None:
    """Parse a Muqawil detail page and require a strong company-identity match."""

    if not _muqawil_url(url):
        return None
    text = _flat_text(html)
    if not _identity_matches(company, text):
        return None

    size_match = re.search(
        r"Company Size(?: Based on Number of Employees)?\s*(?:\||:|-)?\s*"
        r"(Big|Large|Medium|Small|Very Small)(?:\s+Company Size)?",
        text,
        re.I,
    )
    membership_match = re.search(
        r"Membership Number\s*(?:\||:|-)?\s*([0-9]{5,})",
        text,
        re.I,
    )
    status_match = re.search(
        r"Status\s*(?:\||:|-)?\s*(Account Verified|Verified|Active)",
        text,
        re.I,
    )
    city_region_match = re.search(
        r"(?:City - Region|City\s*\|\s*Region)\s*(?:\||:|-)?\s*"
        r"([^|]{2,80}(?:\|[^|]{2,80})?)",
        text,
        re.I,
    )

    name = company.english_name.strip() or company.arabic_name.strip()
    heading_match = re.search(
        r"(?:Contractors\s*\|\s*)?([^|]{3,160})\s*\|\s*"
        r"(?:Platinum|Gold|Silver|Account Verified|Membership Number)",
        text,
        re.I,
    )
    if heading_match:
        candidate = re.sub(r"\s+", " ", heading_match.group(1)).strip()
        if _identity_matches(company, candidate):
            name = candidate

    company_size = re.sub(r"\s+", " ", size_match.group(1)).strip() if size_match else ""
    if company_size.lower() == "big":
        company_size = "Large"
    membership = membership_match.group(1) if membership_match else ""
    status = status_match.group(1) if status_match else ""
    city_region = re.sub(r"\s+", " ", city_region_match.group(1)).strip(" |") if city_region_match else ""

    evidence_bits = ["Muqawil identity match"]
    if company_size:
        evidence_bits.append(f"size={company_size}")
    if membership:
        evidence_bits.append(f"membership={membership}")
    if status:
        evidence_bits.append(f"status={status}")

    return MuqawilProfile(
        name=name,
        url=url,
        company_size=company_size,
        membership_number=membership,
        status=status,
        city_region=city_region,
        evidence="; ".join(evidence_bits),
    )


def discover_muqawil_profile(
    company: CompanyRecord,
    *,
    timeout_seconds: float = 8.0,
    client: httpx.Client | None = None,
) -> MuqawilProfile | None:
    """Find at most one strongly matched public Muqawil contractor profile.

    Discovery is free and bounded. Absence is neutral because not every REGA
    developer is a contractor registered on Muqawil.
    """

    queries: list[str] = []
    if company.english_name.strip():
        queries.append(f'site:muqawil.org/en/contractors "{company.english_name.strip()}"')
    if company.arabic_name.strip():
        queries.append(f'site:muqawil.org/ar/contractors "{company.arabic_name.strip()}"')

    candidates: list[str] = []
    seen: set[str] = set()
    for query in queries[:2]:
        for item in searxng_qwant_search(query, limit=3):
            url = str(item.get("url") or "").strip()
            if url in seen or not _muqawil_url(url):
                continue
            seen.add(url)
            candidates.append(url)
            if len(candidates) >= 3:
                break
        if len(candidates) >= 3:
            break

    if not candidates:
        return None

    owns_client = client is None
    http = client or httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Career-Engine-REGA/3.0)"},
    )
    try:
        for url in candidates:
            try:
                response = http.get(url)
                if int(response.status_code) >= 400:
                    continue
            except Exception:
                continue
            profile = parse_muqawil_profile(company, str(response.url), response.text or "")
            if profile is not None:
                return profile
    finally:
        if owns_client:
            http.close()
    return None


def _contains_any(haystack: str, terms: tuple[str, ...]) -> bool:
    low = haystack.lower()
    return any(term.lower() in low for term in terms)


def career_value_score(
    row: Mapping[str, str],
    *,
    muqawil: MuqawilProfile | None = None,
) -> int:
    """Return a deterministic 0-100 execution-priority score.

    This only chooses research order. Sparse evidence should not be interpreted
    as a negative statement about the company.
    """

    region = str(row.get("Region") or "").strip().lower()
    status = str(row.get("Source_Status") or "")
    verification = str(row.get("Source_Verification") or "")
    notes = str(row.get("Notes") or "")
    website = str(row.get("Address_or_Website") or "")
    company = " ".join(
        [
            str(row.get("Company_or_Office") or ""),
            str(row.get("Arabic_Name") or ""),
        ]
    )
    evidence_text = " ".join([status, verification, notes, website])

    score = 20
    score += REGION_POINTS.get(region, 0)

    if website.startswith(("http://", "https://")) and (
        "verified" in verification.lower()
        or "official" in verification.lower()
        or "official domain" in status.lower()
    ):
        score += 15

    if _contains_any(evidence_text, ACTIVE_PROJECT_TERMS):
        score += 20
    if _contains_any(evidence_text, HIRING_TERMS):
        score += 15
    if _contains_any(evidence_text, OPERATING_TERMS):
        score += 10

    if muqawil is not None:
        score += 5
        score += SIZE_POINTS.get(muqawil.company_size.lower(), 0)

    if _contains_any(company, WEAK_STRUCTURE_TERMS) and score < 55:
        score -= 3

    return max(0, min(100, score))


def priority_band(score: int) -> str:
    if score >= 55:
        return "A"
    if score >= 35:
        return "B"
    return "C"


def local_priority_key(row: Mapping[str, str]) -> tuple[int, int, str, str]:
    """Stable free preflight order before optional Muqawil enrichment."""

    score = career_value_score(row)
    band = priority_band(score)
    band_rank = {"A": 0, "B": 1, "C": 2}[band]
    return (
        band_rank,
        -score,
        str(row.get("Company_or_Office") or "").strip().lower(),
        str(row.get("Master_ID") or ""),
    )


TERMINAL_SOURCE_STATUSES = (
    "verified receiving email route",
    "verified careers/ats application route",
    "resolved - verified company; no usable employment route",
    "resolved - official mailbox not receiving",
    "resolved - official mailbox found; validation unavailable",
    "resolved - company already successfully contacted",
    "resolved - company already covered by active/sent outreach",
    "identity unconfirmed after automated rega resolution",
)


def is_terminal_resolution(row: Mapping[str, str]) -> bool:
    status = str(row.get("Source_Status") or "").strip().lower()
    return any(marker in status for marker in TERMINAL_SOURCE_STATUSES)
