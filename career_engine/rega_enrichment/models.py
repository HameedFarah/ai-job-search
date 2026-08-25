"""REGA enrichment data models."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class CompanyRecord:
    company_id: str  # stable row id, 1-indexed string
    license_no: str
    english_name: str
    arabic_name: str
    location: str
    career_priority: str = ""
    research_status: str = ""

    @property
    def immutable_identity(self) -> dict[str, str]:
        return {
            "License No": self.license_no,
            "Arabic Name": self.arabic_name,
            "English Name": self.english_name,
            "English Location(s)": self.location,
        }

@dataclass(frozen=True)
class QuerySpec:
    company_id: str
    license_no: str
    query_id: str
    query_text: str
    template_id: str

    @staticmethod
    def make(company: CompanyRecord, template: str, template_id: str) -> "QuerySpec":
        raw = template.format(
            english=company.english_name.strip(),
            arabic=company.arabic_name.strip(),
            location=company.location.strip(),
        ).strip()
        # query_id is deterministic hash of license_no + raw query
        h = hashlib.sha256(f"{company.license_no}|{raw}".encode("utf-8")).hexdigest()[:12]
        qid = f"{company.company_id}:{h}"
        return QuerySpec(
            company_id=company.company_id,
            license_no=company.license_no,
            query_id=qid,
            query_text=raw,
            template_id=template_id,
        )

@dataclass
class CandidateResult:
    company_id: str
    license_no: str
    query_id: str
    url: str
    title: str
    description: str
    engine: str
    position: int | None = None
    retrieved_at: str = ""
    # verification fields filled later
    verification_status: str = "pending"  # confirmed | candidate | unconfirmed | rejected | not_found
    verification_method: str = ""
    verification_score: float = 0.0
    verification_evidence: str = ""

@dataclass
class EvidenceRecord:
    company_id: str
    license_no: str
    field: str
    value: str
    source_url: str
    source_type: str
    evidence_text: str
    verified_at: str
    confidence: str
    verification_method: str

@dataclass
class EnrichmentRow:
    company: CompanyRecord
    # Accepted verified fields — blank if unverified
    official_website: str = ""
    official_domain: str = ""
    linkedin_company_page: str = ""
    general_email: str = ""
    main_phone: str = ""
    careers_page: str = ""
    ats_url: str = ""
    ats_domain: str = ""
    recruitment_email: str = ""
    ttw_bd_email: str = ""
    procurement_email: str = ""
    supplier_registration_url: str = ""
    confidence: str = "not_found"  # for official_website
    assignment: str = "not_found"
    evidence: list[EvidenceRecord] = field(default_factory=list)
    rejected_candidates: list[CandidateResult] = field(default_factory=list)
    best_candidate: CandidateResult | None = None
    notes: str = ""

def distinctive_tokens(name: str, generic: set[str]) -> list[str]:
    # Keep tokens len>=3 for short brand names (NHC, RAFAL), len>=4 for longer,
    # but ensure at least one token survives for short names.
    raw = re.findall(r"[a-z0-9]+", name.lower())
    # First pass len>=4
    toks = [x for x in raw if len(x) >= 4 and x not in generic]
    if toks:
        return toks
    # Fallback for short names like NHC, or when all tokens are generic-length
    toks2 = [x for x in raw if len(x) >= 3 and x not in generic]
    if toks2:
        return toks2
    # Never fall back to sub-3-character fragments: tiny tokens can create
    # accidental hostname matches (for example "ra" inside unrelated domains).
    return []

def hostname_tokens(host: str) -> str:
    return re.sub(r"[^a-z0-9]", "", host.lower())
