"""Source registry and capability matrix for discovery-only ingestion.

The registry is the single factual authority for which job sources the Career
Engine may use, how they are accessed, whether they are official, what posting
date precision they support and why blocked sources are blocked. Adapters,
probes, tests and documentation all derive from it so they cannot drift apart.

Status values:

- ``active`` - usable now by a probe/ingestion run.
- ``partial`` - usable, but with a documented limitation (identifier needed,
  API key absent, or best-effort upstream service).
- ``blocked`` - not usable as a core dependency; reason documented.
- ``experimental`` - retained for controlled testing only.

Legality/reliability notes are inline in each entry and expanded in
``docs/JOB_SOURCES.md``; they follow the reviewed decisions in the canonical
Vault connector-research report (2026-08-01) and the repository skill evidence
under ``.agents/skills/``.
"""

from __future__ import annotations

import os
from typing import Any

STATUS_ACTIVE = "active"
STATUS_PARTIAL = "partial"
STATUS_BLOCKED = "blocked"
STATUS_EXPERIMENTAL = "experimental"

_KIND_LABELS = {
    "ats_api": "official ATS public API",
    "ats_web": "official ATS web feed/page",
    "employer_page": "employer career page",
    "aggregator_api": "aggregator public API",
    "board_web": "job board web",
    "discovery": "search-engine discovery",
    "inbox": "mailbox alert inbox",
}

# id -> registry entry. Fields: id, name, kind, priority, auth, posting_date,
# official, status, blocked_reason, base_url, docs_url, notes, probe.
_SOURCES: list[dict[str, Any]] = [
    {
        "id": "workday",
        "name": "Workday public CXS jobs",
        "kind": "ats_api",
        "priority": 1,
        "auth": "none",
        "posting_date": "unknown",
        "official": True,
        "status": STATUS_ACTIVE,
        "blocked_reason": "",
        "base_url": "https://{tenant}.wd*.myworkdayjobs.com/{site}/wday/cxs/{tenant}/{site}/jobs",
        "docs_url": "https://developer.workday.com/",
        "notes": "Public unauthenticated CXS endpoint; tenant and site must come from an official employer URL.",
        "probe": {"verified": True, "companies": ["kbr.wd1.myworkdayjobs.com/kbr_careers"], "verified_at": "2026-08-14"},
    },
    {
        "id": "greenhouse",
        "name": "Greenhouse Job Board API",
        "kind": "ats_api",
        "priority": 1,
        "auth": "none",
        "posting_date": "exact",
        "official": True,
        "status": STATUS_ACTIVE,
        "blocked_reason": "",
        "base_url": "https://boards-api.greenhouse.io/v1/boards/{board_token}",
        "docs_url": "https://developers.greenhouse.io/job-board.html",
        "notes": (
            "Public, unauthenticated board API with first_published/updated_at and full HTML "
            "content. Official employer publication. Per-board token required."
        ),
        "probe": {
            "verified": True,
            "companies": ["careem", "tamara"],
            "verified_at": "2026-08-05",
        },
    },
    {
        "id": "lever",
        "name": "Lever Postings API",
        "kind": "ats_api",
        "priority": 1,
        "auth": "none",
        "posting_date": "exact",
        "official": True,
        "status": STATUS_ACTIVE,
        "blocked_reason": "",
        "base_url": "https://api.lever.co/v0/postings/{company}?mode=json",
        "docs_url": "https://jobs.lever.co/",
        "notes": (
            "Public postings endpoint; createdAt is a millisecond epoch. Verified live against "
            "Lever's own demo board (leverdemo). No GCC/KSA board identifier has been confirmed yet."
        ),
        "probe": {
            "verified": True,
            "companies": ["leverdemo"],
            "verified_at": "2026-08-05",
        },
    },
    {
        "id": "ashby",
        "name": "Ashby Public Posting API",
        "kind": "ats_api",
        "priority": 1,
        "auth": "none",
        "posting_date": "exact",
        "official": True,
        "status": STATUS_ACTIVE,
        "blocked_reason": "",
        "base_url": "https://api.ashbyhq.com/posting-api/job-board/{company}",
        "docs_url": "https://developers.ashbyhq.com/docs/",
        "notes": (
            "Public posting API with publishedAt/updatedAt and plain+HTML descriptions. "
            "Verified live against several boards (ramp, linear, notion, plaid, opensea). "
            "No GCC/KSA board identifier has been confirmed yet."
        ),
        "probe": {
            "verified": True,
            "companies": ["ramp", "linear"],
            "verified_at": "2026-08-05",
        },
    },
    {
        "id": "smartrecruiters",
        "name": "SmartRecruiters Public Postings",
        "kind": "ats_api",
        "priority": 1,
        "auth": "none",
        "posting_date": "exact",
        "official": True,
        "status": STATUS_ACTIVE,
        "blocked_reason": "",
        "base_url": "https://api.smartrecruiters.com/v1/companies/{company_id}",
        "docs_url": "https://dev.smartrecruiters.com/",
        "notes": (
            "Public company postings API; releasedDate is ISO with millisecond precision. "
            "Company id comes from careers.smartrecruiters.com/{company_id}. Verified live "
            "against SmartRecruiters' own board. Empty responses are returned for unknown "
            "identifiers rather than 404, so probes report 'empty board' explicitly."
        ),
        "probe": {
            "verified": True,
            "companies": ["SmartRecruiters"],
            "verified_at": "2026-08-05",
        },
    },
    {
        "id": "workable",
        "name": "Workable Public Career Page/Feed",
        "kind": "ats_web",
        "priority": 2,
        "auth": "none",
        "posting_date": "approximate",
        "official": True,
        "status": STATUS_PARTIAL,
        "blocked_reason": (
            "Live API endpoints (v3 accounts / v1 widget) returned HTTP 404 for every tested "
            "account identifier on 2026-08-05; the public feed may require a widget-enabled "
            "account or a changed endpoint. Adapter is verified offline against fixtures and "
            "falls back to embedded career-page data when present."
        ),
        "base_url": "https://apply.workable.com/{account}/",
        "docs_url": "https://workable.com/",
        "notes": (
            "Public career pages/feeds. published_on is a bare date (day precision). "
            "Treat as fallback/direct-source class per connector research."
        ),
        "probe": {
            "verified": True,
            "companies": ["qiddiya"],
            "verified_at": "2026-08-06",
        },
        "probe_history": [
            {
                "verified": False,
                "companies": [],
                "verified_at": "2026-08-05",
                "evidence": (
                    "Legacy v3 accounts and v1 widget endpoints returned HTTP 404 for all "
                    "tested account identifiers."
                ),
            },
            {
                "verified": True,
                "companies": ["qiddiya"],
                "verified_at": "2026-08-06",
                "evidence": (
                    "The current public account endpoint responded for Qiddiya; the existing "
                    "adapter still requires migration before live use."
                ),
            },
        ],
    },
    {
        "id": "jsonld",
        "name": "Employer Career Pages (JobPosting JSON-LD / sitemaps)",
        "kind": "employer_page",
        "priority": 2,
        "auth": "none",
        "posting_date": "exact",
        "official": True,
        "status": STATUS_ACTIVE,
        "blocked_reason": "",
        "base_url": "<employer careers URL or sitemap URL>",
        "docs_url": "https://schema.org/JobPosting",
        "notes": (
            "Parse application/ld+json JobPosting blocks (datePosted/validThrough) or job "
            "sitemaps. Highest-fidelity official source for employers without a public ATS API. "
            "No fixed company identifier; probe targets a supplied careers URL."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "brave_search",
        "name": "Brave Search API",
        "kind": "discovery",
        "priority": 2,
        "auth": "api_key",
        "posting_date": "none",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": "Unavailable until BRAVE_SEARCH_API_KEY is configured.",
        "base_url": "https://api.search.brave.com/res/v1/web/search",
        "docs_url": (
            "https://api-dashboard.search.brave.com/app/documentation/web-search/get-started"
        ),
        "notes": (
            "Licensed search discovery only. Results remain unverified and cannot become "
            "authoritative until an official employer page or ATS source verifies the vacancy."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "secret_env": "BRAVE_SEARCH_API_KEY",
        "manual_only": False,
    },
    {
        "id": "jooble",
        "name": "Jooble REST API",
        "kind": "aggregator_api",
        "priority": 2,
        "auth": "api_key",
        "posting_date": "none",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": "Unavailable until JOOBLE_API_KEY is configured.",
        "base_url": "https://jooble.org/api/{api_key}",
        "docs_url": (
            "https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation"
        ),
        "notes": (
            "Licensed aggregator used only for discovery. Jooble's updated field is retained as "
            "upstream metadata and is not treated as the vacancy posting date. Official employer "
            "or ATS verification is mandatory before authoritative ingestion."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "secret_env": "JOOBLE_API_KEY",
        "manual_only": False,
    },
    {
        "id": "careerjet",
        "name": "Careerjet Publisher API",
        "kind": "aggregator_api",
        "priority": 2,
        "auth": "api_key + actual user IP/user-agent",
        "posting_date": "exact",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": (
            "Manual user-triggered calls only. Requires an API key plus the actual triggering "
            "user IP address and user-agent; scheduled or synthetic-identity calls are denied."
        ),
        "base_url": "https://search.api.careerjet.net/v4/query",
        "docs_url": "https://www.careerjet.com/partners/api",
        "notes": (
            "Discovery-only. CAREERJET_API_KEY is canonical; CAREERJET_AFFID remains accepted "
            "only as a compatibility alias. Official employer or ATS verification is mandatory."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "secret_env": "CAREERJET_API_KEY|CAREERJET_AFFID",
        "manual_only": True,
    },
    {
        "id": "search_discovery",
        "name": "Search-Engine Discovery -> Official Verification",
        "kind": "discovery",
        "priority": 3,
        "auth": "api_key (Google/Bing); none (DuckDuckGo Instant Answer)",
        "posting_date": "none",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": (
            "Google Custom Search and Bing require API keys (not configured). DuckDuckGo Instant "
            "Answer API is public but best-effort. Candidates are NEVER ingested as official "
            "until verified against the employer's own page (JSON-LD or known ATS domain)."
        ),
        "base_url": "https://api.duckduckgo.com/?q=...&format=json",
        "docs_url": "https://duckduckgo.com/duckduckgo-help-pages/results/instant-answer-api/",
        "notes": (
            "Discovery-only by design: emits verification candidates, then jsonld.verify_official "
            "promotes verified candidates to official provenance before any ingest."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "inbox_gmail",
        "name": "Gmail Recruiter / Job-Alert Inbox",
        "kind": "inbox",
        "priority": 3,
        "auth": "oauth (connected mailbox)",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Repository-native Gmail/gws authentication reports invalid_grant (documented in "
            "CAREER_ENGINE_V1_IMPLEMENTATION.md). Requires a connected ChatGPT Gmail connector. "
            "The inbox contract is implemented for ingestion; live reading is blocked without "
            "an authorized mailbox. Mailbox data is runtime data and never committed."
        ),
        "base_url": "<mailbox>",
        "docs_url": "",
        "notes": (
            "Inbound alerts only (recruiter emails, job-alert digests). Never creates drafts or "
            "sends; used for discovery, then verified against the official source before ingest."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "linkedin_alerts",
        "name": "LinkedIn Job Alerts (Inbox Surface)",
        "kind": "inbox",
        "priority": 3,
        "auth": "connector session",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Requires a connected browser/session; fragile authenticated LinkedIn scraping is "
            "explicitly NOT a core dependency (connector-research 2026-08-01). LinkedIn is used "
            "as a discovery/alerts surface only, followed by official verification."
        ),
        "base_url": "",
        "docs_url": "",
        "notes": (
            "Inbox/alert ingestion contract only. Any LinkedIn-derived record must be verified "
            "against the official employer posting before it can be ingested as authoritative."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "alert_normalizer_available": True,
        "manual_configuration_required": True,
    },
    {
        "id": "bayt_alerts",
        "name": "Bayt Job Alerts (Inbox Surface)",
        "kind": "inbox",
        "priority": 3,
        "auth": "authenticated email alert",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": "Alerts must be configured manually in the authenticated Bayt account.",
        "base_url": "",
        "docs_url": "",
        "notes": (
            "Email-alert normalization only; no direct Bayt scraping. Every listing remains "
            "unverified until promoted through an official employer or ATS source."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "alert_normalizer_available": True,
        "manual_configuration_required": True,
    },
    {
        "id": "naukrigulf_alerts",
        "name": "NaukriGulf Job Alerts (Inbox Surface)",
        "kind": "inbox",
        "priority": 3,
        "auth": "authenticated email alert",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": (
            "Alerts must be configured manually in the authenticated NaukriGulf account."
        ),
        "base_url": "",
        "docs_url": "",
        "notes": (
            "Email-alert normalization only; no direct NaukriGulf scraping. Every listing "
            "requires official employer or ATS verification before authoritative ingestion."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "alert_normalizer_available": True,
        "manual_configuration_required": True,
    },
    {
        "id": "gulftalent_alerts",
        "name": "GulfTalent Job Alerts (Inbox Surface)",
        "kind": "inbox",
        "priority": 3,
        "auth": "authenticated email alert",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": (
            "Alerts must be configured manually in the authenticated GulfTalent account."
        ),
        "base_url": "",
        "docs_url": "",
        "notes": (
            "Email-alert normalization only; no direct GulfTalent scraping. Every listing "
            "requires official employer or ATS verification before authoritative ingestion."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "alert_normalizer_available": True,
        "manual_configuration_required": True,
    },
    {
        "id": "indeed_alerts",
        "name": "Indeed Job Alerts (Inbox Surface)",
        "kind": "inbox",
        "priority": 3,
        "auth": "authenticated email alert",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": "Alerts must be configured manually in the authenticated Indeed account.",
        "base_url": "",
        "docs_url": "",
        "notes": (
            "Email-alert normalization only; no direct Indeed scraping. Every listing remains "
            "unverified until promoted through an official employer or ATS source."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "alert_normalizer_available": True,
        "manual_configuration_required": True,
    },
    {
        "id": "foundit_alerts",
        "name": "Foundit Job Alerts (Inbox Surface)",
        "kind": "inbox",
        "priority": 3,
        "auth": "authenticated email alert",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": "Alerts must be configured manually in the authenticated Foundit account.",
        "base_url": "",
        "docs_url": "",
        "notes": (
            "Email-alert normalization only; no direct Foundit scraping. Every listing remains "
            "unverified until promoted through an official employer or ATS source."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "alert_normalizer_available": True,
        "manual_configuration_required": True,
    },
    {
        "id": "gotogulf_alerts",
        "name": "Gotogulf Job Alerts (Inbox Surface)",
        "kind": "inbox",
        "priority": 3,
        "auth": "authenticated email alert",
        "posting_date": "unknown",
        "official": False,
        "status": STATUS_PARTIAL,
        "blocked_reason": (
            "Alerts must be configured manually in the authenticated Gotogulf account."
        ),
        "base_url": "",
        "docs_url": "",
        "notes": (
            "Email-alert normalization only; no direct Gotogulf automation. Every listing "
            "requires official employer or ATS verification before authoritative ingestion."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
        "alert_normalizer_available": True,
        "manual_configuration_required": True,
    },
    {
        "id": "gcc_freehire",
        "name": "Freehire Public API (GCC/global aggregator)",
        "kind": "aggregator_api",
        "priority": 2,
        "auth": "none",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_ACTIVE,
        "blocked_reason": "",
        "base_url": "https://freehire.me",
        "docs_url": "",
        "notes": (
            "Shipped upstream (enabled in .agents/skills/freehire-search). Public JSON API, no "
            "authentication, tech-first facet filtering. Best-effort hosted service without SLA; "
            "treated as a secondary source, never the sole coverage for architecture roles."
        ),
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "gcc_bayt",
        "name": "Bayt.com (GCC/Jordan/MENA)",
        "kind": "board_web",
        "priority": 3,
        "auth": "none",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Cloudflare bot protection returns 403 on automated requests (fixture mode only; "
            "evidence in .agents/skills/bayt-search). Re-enable only after a live-verified, "
            "non-credentialed path exists and ToS/rate behavior is reviewed."
        ),
        "base_url": "https://www.bayt.com",
        "docs_url": "",
        "notes": "Fixture-mode evidence retained; do not present results as live listings.",
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "gcc_naukrigulf",
        "name": "NaukriGulf.com (GCC)",
        "kind": "board_web",
        "priority": 3,
        "auth": "none",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Connection timeout/blocked on automated requests (fixture mode only; evidence in "
            ".agents/skills/naukrigulf-search). Re-enable only after a live-verified path exists."
        ),
        "base_url": "https://www.naukrigulf.com",
        "docs_url": "",
        "notes": "Fixture-mode evidence retained; do not present results as live listings.",
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "gcc_gulftalent",
        "name": "GulfTalent (GCC)",
        "kind": "board_web",
        "priority": 3,
        "auth": "browser/account",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "No maintained connector found in reviewed upstream PRs/issues/forks; anti-bot, "
            "account and ToS risk (connector-research 2026-08-01). Do not build yet."
        ),
        "base_url": "https://www.gulftalent.com",
        "docs_url": "",
        "notes": "Use official/public search fallback; reassess only after measured coverage gap.",
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "board_indeed",
        "name": "Indeed",
        "kind": "board_web",
        "priority": 3,
        "auth": "none",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Strong anti-bot and policy risk; no supported maintained connector in the reviewed "
            "upstream set (connector-research 2026-08-01). Web-search fallback only."
        ),
        "base_url": "https://www.indeed.com",
        "docs_url": "",
        "notes": "No custom scraper now.",
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "board_foundit",
        "name": "Foundit",
        "kind": "board_web",
        "priority": 3,
        "auth": "account",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Authenticated or anti-bot scraping is not approved. Use authenticated email alerts "
            "as a discovery surface and verify against the official employer posting."
        ),
        "base_url": "https://www.foundit.in",
        "docs_url": "",
        "notes": "Residential fallback is explicitly denied.",
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "board_gotogulf",
        "name": "Gotogulf",
        "kind": "board_web",
        "priority": 3,
        "auth": "none",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Direct board automation is not approved. Use authenticated email alerts as a "
            "discovery surface and verify against the official employer posting."
        ),
        "base_url": "https://www.gotogulf.com",
        "docs_url": "",
        "notes": "Residential fallback is explicitly denied.",
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
    {
        "id": "linkedin_public",
        "name": "LinkedIn Public Jobs",
        "kind": "board_web",
        "priority": 3,
        "auth": "session for apply",
        "posting_date": "approximate",
        "official": False,
        "status": STATUS_BLOCKED,
        "blocked_reason": (
            "Fragile authenticated scraping must not be a core dependency (Vault index and "
            "connector-research 2026-08-01). Use the inbox-alert surface and manual pasted links "
            "instead; any record must be verified against the official employer posting."
        ),
        "base_url": "https://www.linkedin.com/jobs",
        "docs_url": "",
        "notes": "Discovery surface only; official verification required before ingest.",
        "probe": {"verified": False, "companies": [], "verified_at": ""},
    },
]

_BY_ID: dict[str, dict[str, Any]] | None = None


def _index() -> dict[str, dict[str, Any]]:
    global _BY_ID
    if _BY_ID is None:
        _BY_ID = {item["id"]: item for item in _SOURCES}
    return _BY_ID


def sources() -> list[dict[str, Any]]:
    """All registry entries (shared mutable copies are not returned)."""
    return [dict(item) for item in _SOURCES]


def get_source(source_id: str) -> dict[str, Any]:
    """Look up one registry entry; raise KeyError when unknown."""
    item = _index().get(source_id)
    if item is None:
        raise KeyError(f"Unknown source id: {source_id!r}")
    return dict(item)


def _secret_configured(spec: str) -> bool:
    if not spec:
        return True
    return any(bool(os.environ.get(name, "").strip()) for name in spec.split("|"))


def runtime_source_status() -> list[dict[str, Any]]:
    """Return value-free configured/runnable status for every registered source."""
    rows: list[dict[str, Any]] = []
    for item in sorted(_SOURCES, key=lambda entry: (entry["priority"], entry["id"])):
        configured = _secret_configured(item.get("secret_env", ""))
        runnable = item["status"] != STATUS_BLOCKED and configured
        rows.append(
            {
                "source_id": item["id"],
                "configured": configured,
                "runnable": runnable,
                "manual_only": bool(item.get("manual_only")),
                "status": (
                    "configured"
                    if runnable
                    else "blocked"
                    if item["status"] == STATUS_BLOCKED
                    else "unconfigured"
                ),
            }
        )
    return rows


def capability_matrix() -> list[dict[str, Any]]:
    """The capability matrix: one row per source, ordered by priority."""
    rows = []
    runtime = {item["source_id"]: item for item in runtime_source_status()}
    for item in sorted(_SOURCES, key=lambda entry: (entry["priority"], entry["id"])):
        rows.append(
            {
                "source_id": item["id"],
                "name": item["name"],
                "kind": _KIND_LABELS.get(item["kind"], item["kind"]),
                "priority": item["priority"],
                "auth_required": item["auth"],
                "posting_date_support": item["posting_date"],
                "official_source": item["official"],
                "status": item["status"],
                "blocked_reason": item["blocked_reason"],
                "probe_verified": bool(item.get("probe", {}).get("verified")),
                "configured": runtime[item["id"]]["configured"],
                "runnable": runtime[item["id"]]["runnable"],
                "manual_only": runtime[item["id"]]["manual_only"],
            }
        )
    return rows


def registry_payload() -> dict[str, Any]:
    """Full registry payload: schema, sources and capability matrix."""
    return {
        "schema_version": 1,
        "purpose": "Career Engine discovery source registry (canonical, in-repo)",
        "no_send_policy": True,
        "official_verification_required_for_discovery_sources": True,
        "sources": _SOURCES,
        "capability_matrix": capability_matrix(),
        "runtime_status": runtime_source_status(),
    }
