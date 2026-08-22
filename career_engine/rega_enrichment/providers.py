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

    @property
    def usable(self) -> bool:
        return bool(self.value and self.source_url and self.evidence and self.status in {"candidate", "verified"})

def result(provider: str, status: str, *, value: str = "", source_url: str = "", evidence: str = "", cost_status: str = "unknown") -> ProviderResult:
    return ProviderResult(provider, status, value, source_url, evidence, datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), cost_status)

def recruitment_contact(provider: str, email: str, source_url: str, evidence: str, *, official: bool = False, cost_status: str = "unknown") -> ProviderResult:
    status = "verified" if official and email and source_url and evidence else "candidate"
    return result(provider, status, value=email if email else "", source_url=source_url, evidence=evidence if official else "", cost_status=cost_status)
