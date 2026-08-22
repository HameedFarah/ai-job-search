"""Small provider adapter seam for career enrichment.

Adapters return evidence-bearing records and never promote a generic contact
to recruitment without explicit official-page evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class ProviderResult:
    provider: str
    status: str
    value: str = ""
    source_url: str = ""
    evidence: str = ""
    retrieved_at: str = ""
    cost_status: str = "unknown"
    official_recruitment: bool = False

    @property
    def usable(self) -> bool:
        """True when the enrichment record has enough provenance to retain/use as a lead."""
        return bool(self.value and self.source_url and self.evidence and self.status in {"candidate", "verified"})

    @property
    def outreach_ready(self) -> bool:
        """Only explicit official recruitment evidence may authorize the email route."""
        return bool(self.usable and self.status == "verified" and self.official_recruitment)

def result(
    provider: str,
    status: str,
    *,
    value: str = "",
    source_url: str = "",
    evidence: str = "",
    cost_status: str = "unknown",
    official_recruitment: bool = False,
) -> ProviderResult:
    return ProviderResult(
        provider,
        status,
        value,
        source_url,
        evidence,
        datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        cost_status,
        official_recruitment,
    )

def recruitment_contact(provider: str, email: str, source_url: str, evidence: str, *, official: bool = False, cost_status: str = "unknown") -> ProviderResult:
    status = "verified" if official and email and source_url and evidence else "candidate"
    return result(
        provider,
        status,
        value=email if email else "",
        source_url=source_url,
        evidence=evidence,
        cost_status=cost_status,
        official_recruitment=official and status == "verified",
    )
