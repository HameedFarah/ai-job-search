"""Deterministic Lever tenant discovery and validation.

This module discovers *tenant identifiers*, not jobs. Search-engine evidence can
suggest a Lever board URL, but only a validated Lever public Postings API board
with a credible employer identity may emit authoritative jobs.

Durable known-employer mappings live in the existing GCC employer registry.
There is intentionally no second Lever-company registry.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .base import DiscoveryJob, SourceError, SourceUnavailable

_GLOBAL_BOARD_HOST = "jobs.lever.co"
_EU_BOARD_HOST = "jobs.eu.lever.co"
_GLOBAL_API_HOST = "api.lever.co"
_EU_API_HOST = "api.eu.lever.co"
_LEVER_HOSTS = {
    _GLOBAL_BOARD_HOST: "global",
    _EU_BOARD_HOST: "eu",
    _GLOBAL_API_HOST: "global",
    _EU_API_HOST: "eu",
}
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LeverTenant:
    slug: str
    instance: str = "global"

    @property
    def identifier(self) -> str:
        return f"eu:{self.slug}" if self.instance == "eu" else self.slug

    @property
    def board_url(self) -> str:
        host = _EU_BOARD_HOST if self.instance == "eu" else _GLOBAL_BOARD_HOST
        return f"https://{host}/{self.slug}"

    @property
    def api_url(self) -> str:
        host = _EU_API_HOST if self.instance == "eu" else _GLOBAL_API_HOST
        return f"https://{host}/v0/postings/{self.slug}"


@dataclass(slots=True)
class TenantCandidate:
    tenant: LeverTenant
    employer_id: str = ""
    employer_name: str = ""
    employer_domain: str = ""
    market: str = ""
    discovery_source: str = ""
    identity_verified: bool = False

    def to_data(self) -> dict[str, Any]:
        data = asdict(self)
        data["tenant"] = self.tenant.slug
        data["instance"] = self.tenant.instance
        data["identifier"] = self.tenant.identifier
        data["board_url"] = self.tenant.board_url
        data.pop("tenant", None)
        # asdict() turns nested dataclass into a dict; restore the scalar fields.
        data.update(
            tenant=self.tenant.slug,
            instance=self.tenant.instance,
            identifier=self.tenant.identifier,
            board_url=self.tenant.board_url,
        )
        return data


def normalize_tenant(identifier: str) -> LeverTenant:
    """Normalize a slug, board URL, API URL or ``eu:<slug>`` identifier."""

    raw = str(identifier or "").strip()
    if not raw:
        raise SourceError("Lever tenant identifier is required")

    instance = "global"
    slug = raw
    if raw.lower().startswith("eu:"):
        instance = "eu"
        slug = raw[3:]
    elif "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        if host not in _LEVER_HOSTS:
            raise SourceError(f"Not a supported Lever public host: {host or 'unknown'}")
        instance = _LEVER_HOSTS[host]
        segments = [part for part in parsed.path.split("/") if part]
        if host in {_GLOBAL_API_HOST, _EU_API_HOST}:
            # /v0/postings/<tenant>[/<posting-id>]
            try:
                postings_index = segments.index("postings")
                slug = segments[postings_index + 1]
            except (ValueError, IndexError) as exc:
                raise SourceError("Lever API URL does not contain a tenant") from exc
        elif segments:
            slug = segments[0]
        else:
            raise SourceError("Lever job-board URL does not contain a tenant")

    slug = slug.strip().strip("/").lower()
    if not slug or _UUID_RE.fullmatch(slug):
        raise SourceError("Lever posting UUID is not a tenant identifier")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", slug):
        raise SourceError(f"Invalid Lever tenant identifier: {slug!r}")
    return LeverTenant(slug=slug, instance=instance)


def tenant_from_url(url: str) -> LeverTenant | None:
    try:
        return normalize_tenant(url)
    except SourceError:
        return None


def _registry_path(root: Path) -> Path:
    return root / "projects/job-automation/config/gcc-employers.v1.json"


def _taxonomy_path(root: Path) -> Path:
    return root / "projects/job-automation/config/requirements-taxonomy.v1.json"


def load_registry_candidates(root: Path) -> list[TenantCandidate]:
    """Load Lever mappings from the existing GCC employer registry authority."""

    payload = json.loads(_registry_path(root).read_text(encoding="utf-8"))
    candidates: list[TenantCandidate] = []
    for employer in payload.get("employers", []):
        ats = employer.get("ats") or {}
        identifier = ""
        discovery_source = ""
        if str(ats.get("adapter") or "").lower() == "lever":
            identifier = str(ats.get("identifier") or "").strip()
            discovery_source = "gcc_employer_registry.ats"
        if not identifier:
            for key in ("official_ats_url", "official_careers_url"):
                value = str(employer.get(key) or "").strip()
                if tenant_from_url(value):
                    identifier = value
                    discovery_source = f"gcc_employer_registry.{key}"
                    break
        if not identifier:
            continue
        tenant = normalize_tenant(identifier)
        domains = [str(item).strip().lower() for item in employer.get("official_domains", []) if str(item).strip()]
        candidates.append(
            TenantCandidate(
                tenant=tenant,
                employer_id=str(employer.get("id") or ""),
                employer_name=str(employer.get("name") or tenant.slug),
                employer_domain=domains[0] if domains else "",
                market=str(employer.get("market") or ""),
                discovery_source=discovery_source,
                identity_verified=bool(domains and employer.get("name")),
            )
        )
    return _dedupe_candidates(candidates)


def candidates_from_urls(urls: Iterable[str], *, discovery_source: str) -> list[TenantCandidate]:
    """Extract candidate tenant slugs from public Lever URLs.

    Search results stay discovery evidence only: identity is deliberately not
    upgraded here.
    """

    rows: list[TenantCandidate] = []
    for url in urls:
        tenant = tenant_from_url(url)
        if tenant is None:
            continue
        rows.append(TenantCandidate(tenant=tenant, discovery_source=discovery_source))
    return _dedupe_candidates(rows)


def merge_candidates(candidates: Iterable[TenantCandidate]) -> list[TenantCandidate]:
    """Deduplicate tenants, preferring the canonical identity-verified record."""

    merged: dict[tuple[str, str], TenantCandidate] = {}
    for candidate in candidates:
        key = (candidate.tenant.instance, candidate.tenant.slug)
        existing = merged.get(key)
        if existing is None or (candidate.identity_verified and not existing.identity_verified):
            merged[key] = candidate
    return [merged[key] for key in sorted(merged)]


def _dedupe_candidates(candidates: Iterable[TenantCandidate]) -> list[TenantCandidate]:
    return merge_candidates(candidates)


def load_taxonomy(root: Path) -> dict[str, Any]:
    return json.loads(_taxonomy_path(root).read_text(encoding="utf-8"))


def build_search_queries(root: Path, *, max_queries: int = 24) -> list[str]:
    """Build tenant-discovery queries from the existing target taxonomy.

    Role terms come from ``specialization.target_management_terms`` and
    geography terms from ``gcc_locations``; no Lever-only role taxonomy is
    introduced.
    """

    taxonomy = load_taxonomy(root)
    locations = [str(item).strip() for item in taxonomy.get("gcc_locations", []) if str(item).strip()]
    target_terms = [
        str(item).strip()
        for item in (taxonomy.get("specialization", {}).get("target_management_terms") or [])
        if str(item).strip()
    ]
    chunks = [target_terms[index:index + 4] for index in range(0, len(target_terms), 4)]
    # Three independent query pools: global geography, EU geography, and
    # role-aware (target-management) discovery. Role-aware queries are produced
    # for every GCC location and every role chunk so the central taxonomy is
    # reused rather than duplicated.
    geo_global = [f'site:jobs.lever.co "{loc}"' for loc in locations]
    geo_eu = [f'site:jobs.eu.lever.co "{loc}"' for loc in locations]
    role_queries: list[str] = []
    for location in locations:
        for chunk in chunks:
            if not chunk:
                continue
            roles = " OR ".join(f'"{term}"' for term in chunk)
            role_queries.append(f'site:jobs.lever.co "{location}" ({roles})')
    pools = [geo_global, geo_eu, role_queries]
    # Round-robin across the pools by depth so that, within ANY bounded budget,
    # the query list always interleaves global geography, EU geography and
    # role-aware discovery. A small default budget can therefore never exhaust
    # geography-only queries before role-aware queries run.
    ordered: list[str] = []
    max_depth = max((len(pool) for pool in pools), default=0)
    depth = 0
    while len(ordered) < max(1, int(max_queries)) and depth < max_depth:
        for pool in pools:
            if depth < len(pool):
                ordered.append(pool[depth])
        depth += 1
    seen: set[str] = set()
    unique: list[str] = []
    for query in ordered:
        if query in seen:
            continue
        seen.add(query)
        unique.append(query)
        if len(unique) >= max(1, int(max_queries)):
            break
    return unique


def discover_with_brave(root: Path, *, max_queries: int = 24, results_per_query: int = 10) -> dict[str, Any]:
    """Use the existing licensed Brave adapter only to discover Lever URLs."""

    from .adapters.aggregators import BraveSearchAdapter

    urls: list[str] = []
    queries = build_search_queries(root, max_queries=max_queries)
    try:
        adapter = BraveSearchAdapter()
        for query in queries:
            for job in adapter.search(company=query, limit=max(1, min(results_per_query, 20))):
                if tenant_from_url(job.detail_url):
                    urls.append(job.detail_url)
    except SourceUnavailable as exc:
        return {
            "status": "unavailable",
            "reason": str(exc),
            "queries_attempted": 0,
            "candidate_urls": [],
            "send_or_submit": False,
        }
    return {
        "status": "ok",
        "queries_attempted": len(queries),
        "candidate_urls": sorted(set(urls)),
        "send_or_submit": False,
    }


def is_target_geography(job: DiscoveryJob, taxonomy: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            str(job.location or ""),
            " ".join(str(item) for item in (job.extra.get("all_locations") or [])),
            str(job.extra.get("country") or ""),
        ]
    ).lower()
    return any(str(term).lower() in haystack for term in taxonomy.get("gcc_locations", []))


def target_lane_title_allowed(job: DiscoveryJob, taxonomy: dict[str, Any]) -> bool:
    """Reuse the existing target-management discovery gate; no Lever title rules."""

    from ..targeting import target_management_title_candidate

    return target_management_title_candidate(job.role, taxonomy)


def validate_candidate(
    candidate: TenantCandidate,
    *,
    root: Path,
    job_limit: int = 1000,
) -> dict[str, Any]:
    """Validate one tenant against Lever's public API and summarize target jobs."""

    from .adapters.lever import LeverAdapter

    taxonomy = load_taxonomy(root)
    try:
        jobs = LeverAdapter().search(
            company=candidate.tenant.identifier,
            limit=max(1, min(int(job_limit), 5000)),
            fetch_full=True,
        )
    except SourceError as exc:
        return {
            **candidate.to_data(),
            "api_verified": False,
            "verified": False,
            "active": False,
            "reason": str(exc),
            "jobs": 0,
            "gcc_jobs": 0,
            "relevant_jobs": 0,
            "relevant_job_records": [],
        }

    gcc_jobs = [job for job in jobs if is_target_geography(job, taxonomy)]
    relevant = [job for job in gcc_jobs if target_lane_title_allowed(job, taxonomy)]
    active = bool(candidate.identity_verified)
    reason = "" if active else "credible_employer_identity_not_verified"
    return {
        **candidate.to_data(),
        "api_verified": True,
        "verified": active,
        "active": active,
        "reason": reason,
        "jobs": len(jobs),
        "gcc_jobs": len(gcc_jobs),
        "relevant_jobs": len(relevant) if active else 0,
        "relevant_job_records": relevant if active else [],
    }
