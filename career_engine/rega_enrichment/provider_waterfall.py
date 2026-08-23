"""Independent provider waterfall for REGA enrichment.

Provider contacts remain candidate leads. Promotion requires separate evidence
from the verified company website and never mutates official REGA fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Callable, Iterable, Mapping

from .provider_clients import (
    AnymailFinderClient,
    OutscraperClient,
    ProviderBudget,
    TombaClient,
    ZeroBounceClient,
)
from .providers import ProviderResult, recruitment_contact


@dataclass
class WaterfallResult:
    provider_statuses: list[dict[str, str]] = field(default_factory=list)
    contacts: list[ProviderResult] = field(default_factory=list)

    @property
    def official_recruitment_contacts(self) -> list[ProviderResult]:
        return [contact for contact in self.contacts if contact.outreach_ready]


def run_waterfall(steps: Iterable[tuple[str, Callable[[], object]]]) -> WaterfallResult:
    """Run every provider independently; one failure never stops the pipeline."""
    output = WaterfallResult()
    for provider, step in steps:
        try:
            value = step()
            rows = value if isinstance(value, list) else [value]
            row_statuses = [
                str(row.get("status", ""))
                for row in rows
                if isinstance(row, dict) and row.get("status")
            ]
            if any(status in {"candidate", "verified", "valid", "success"} for status in row_statuses):
                provider_status = "success"
            elif row_statuses:
                provider_status = row_statuses[0]
            else:
                provider_status = "success"
            output.provider_statuses.append({"provider": provider, "status": provider_status})
            for row in rows:
                if not isinstance(row, dict) or not row.get("metadata", {}).get("email"):
                    continue
                output.contacts.append(recruitment_contact(
                    provider,
                    str(row["metadata"]["email"]),
                    str(row.get("source_url", "")),
                    str(row.get("evidence", "provider candidate; official evidence absent")),
                    cost_status=str(row.get("cost_status", "unknown")),
                ))
        except Exception as exc:
            output.provider_statuses.append({"provider": provider, "status": "network_failed", "error_type": type(exc).__name__})
    return output


def _provider_budget(allow_existing_credit: bool) -> ProviderBudget:
    """Return one-call/domain bounded budget for one provider invocation."""
    return ProviderBudget(
        allow_existing_credit=allow_existing_credit,
        max_calls=1,
        max_credits=1,
        max_domains=1,
    )


def run_configured_domain_waterfall(
    domain: str,
    *,
    allow_existing_credit: bool = False,
    env: Mapping[str, str] | None = None,
    clients: Mapping[str, object] | None = None,
) -> WaterfallResult:
    """Run bounded provider contact discovery for an already verified domain.

    The result is derived candidate evidence only. It never mutates official REGA
    fields and never validates provider-only addresses for outreach.
    """
    normalized_domain = domain.strip().lower().removeprefix("www.")
    if not normalized_domain or "." not in normalized_domain or any(ch.isspace() for ch in normalized_domain):
        raise ValueError("A verified official domain is required")

    values = os.environ if env is None else env
    configured = dict(clients or {})
    tomba = configured.get("tomba") or TombaClient(
        values.get("TOMBA_API_KEY", ""),
        values.get("TOMBA_API_SECRET", ""),
    )
    outscraper = configured.get("outscraper") or OutscraperClient(values.get("OUTSCRAPER_API_KEY", ""))
    anymail = configured.get("anymailfinder") or AnymailFinderClient(values.get("ANYMAILFINDER_API_KEY", ""))

    return run_waterfall([
        ("tomba", lambda: tomba.domain_search(normalized_domain, _provider_budget(allow_existing_credit))),
        ("outscraper", lambda: outscraper.domain_contacts(normalized_domain, _provider_budget(allow_existing_credit))),
        ("anymailfinder", lambda: anymail.company(normalized_domain, _provider_budget(allow_existing_credit))),
    ])


def validate_official_intended_contact(
    email: str,
    source_url: str,
    evidence: str,
    *,
    allow_existing_credit: bool = False,
    env: Mapping[str, str] | None = None,
    client: ZeroBounceClient | None = None,
) -> tuple[ProviderResult, list[dict[str, object]]]:
    """Validate only a separately verified official contact intended for outreach."""
    contact = promote_official_contact(email, source_url, evidence)
    if not contact.outreach_ready:
        return contact, []
    values = os.environ if env is None else env
    validator = client or ZeroBounceClient(values.get("ZEROBOUNCE_API_KEY", ""))
    validation = validator.validate(
        email,
        _provider_budget(allow_existing_credit),
        allow_existing_credit=allow_existing_credit,
        intended_outreach=True,
    )
    return contact, validation


def promote_official_contact(email: str, source_url: str, evidence: str) -> ProviderResult:
    """Authorize a route only with independently verified official-page evidence."""
    return recruitment_contact("rega-official", email, source_url, evidence, official=True)
