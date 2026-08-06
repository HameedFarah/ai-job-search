"""Fail-closed routing policy for optional residential fallback."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from .base import SourceError

RESTRICTED_BOARD_DOMAINS = (
    "linkedin.com",
    "bayt.com",
    "naukrigulf.com",
    "gulftalent.com",
    "indeed.com",
    "indeed.ae",
    "foundit.in",
    "foundit.ae",
    "founditgulf.com",
    "gotogulf.com",
)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    url: str
    domain: str
    route: str  # vps | residential | denied
    allowed: bool
    reason: str
    proxy_required: bool

    def to_data(self) -> dict[str, object]:
        return asdict(self)


def _domain(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip(".").lower()
    if not host:
        raise SourceError(f"URL has no valid host: {url!r}")
    return host


def _matches(domain: str, configured: str) -> bool:
    configured = configured.lower().strip(".")
    return domain == configured or domain.endswith("." + configured)


def decide_route(
    url: str,
    *,
    residential_allowlist: set[str] | list[str] | tuple[str, ...],
    proxy_available: bool,
) -> RoutingDecision:
    domain = _domain(url)
    if any(_matches(domain, restricted) for restricted in RESTRICTED_BOARD_DOMAINS):
        return RoutingDecision(
            url=url,
            domain=domain,
            route="denied",
            allowed=False,
            reason="restricted job board is explicitly denied residential fallback",
            proxy_required=False,
        )
    allowlist = tuple(str(item).lower().strip(".") for item in residential_allowlist)
    if not any(_matches(domain, allowed) for allowed in allowlist):
        return RoutingDecision(
            url=url,
            domain=domain,
            route="vps",
            allowed=True,
            reason="domain is not approved for residential fallback; use the VPS route",
            proxy_required=False,
        )
    if not proxy_available:
        return RoutingDecision(
            url=url,
            domain=domain,
            route="denied",
            allowed=False,
            reason="approved residential route requested but proxy is unavailable; fail closed",
            proxy_required=True,
        )
    return RoutingDecision(
        url=url,
        domain=domain,
        route="residential",
        allowed=True,
        reason="public employer domain is explicitly allowlisted",
        proxy_required=True,
    )


def require_allowed(decision: RoutingDecision) -> RoutingDecision:
    if not decision.allowed:
        raise SourceError(decision.reason)
    return decision
