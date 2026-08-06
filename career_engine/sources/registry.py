"""Canonical source registry and runtime capability matrix."""

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
    "aggregator_api": "licensed aggregator API",
    "board_web": "job board web",
    "discovery": "search-engine discovery",
    "inbox": "mailbox alert inbox",
}


def _entry(
    source_id: str,
    name: str,
    kind: str,
    priority: int,
    auth: str,
    posting_date: str,
    official: bool,
    status: str,
    *,
    blocked_reason: str = "",
    base_url: str = "",
    docs_url: str = "",
    notes: str = "",
    verified: bool = False,
    companies: list[str] | None = None,
    verified_at: str = "",
    secret_env: str = "",
    manual_only: bool = False,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "name": name,
        "kind": kind,
        "priority": priority,
        "auth": auth,
        "posting_date": posting_date,
        "official": official,
        "status": status,
        "blocked_reason": blocked_reason,
        "base_url": base_url,
        "docs_url": docs_url,
        "notes": notes,
        "probe": {
            "verified": verified,
            "companies": list(companies or []),
            "verified_at": verified_at,
        },
        "secret_env": secret_env,
        "manual_only": manual_only,
    }


_SOURCES: list[dict[str, Any]] = [
    _entry(
        "greenhouse", "Greenhouse Job Board API", "ats_api", 1, "none", "exact",
        True, STATUS_ACTIVE,
        base_url="https://boards-api.greenhouse.io/v1/boards/{board_token}",
        docs_url="https://developers.greenhouse.io/job-board.html",
        notes="Public official board API; board token required.",
        verified=True, companies=["careem", "tamara"], verified_at="2026-08-05",
    ),
    _entry(
        "lever", "Lever Postings API", "ats_api", 1, "none", "exact",
        True, STATUS_ACTIVE,
        base_url="https://api.lever.co/v0/postings/{company}?mode=json",
        docs_url="https://jobs.lever.co/",
        notes="Public official postings endpoint.",
        verified=True, companies=["leverdemo"], verified_at="2026-08-05",
    ),
    _entry(
        "ashby", "Ashby Public Posting API", "ats_api", 1, "none", "exact",
        True, STATUS_ACTIVE,
        base_url="https://api.ashbyhq.com/posting-api/job-board/{company}",
        docs_url="https://developers.ashbyhq.com/docs/",
        notes="Public official posting API.",
        verified=True, companies=["ramp", "linear"], verified_at="2026-08-05",
    ),
    _entry(
        "smartrecruiters", "SmartRecruiters Public Postings", "ats_api", 1,
        "none", "exact", True, STATUS_ACTIVE,
        base_url="https://api.smartrecruiters.com/v1/companies/{company_id}",
        docs_url="https://dev.smartrecruiters.com/",
        notes="Public official company postings API.",
        verified=True, companies=["SmartRecruiters"], verified_at="2026-08-05",
    ),
    _entry(
        "workable", "Workable Public Career Page/Feed", "ats_web", 2, "none",
        "approximate", True, STATUS_PARTIAL,
        blocked_reason=(
            "The current public account endpoint was verified for Qiddiya on 2026-08-06, "
            "but the existing adapter still targets older endpoints and must be migrated "
            "before live use."
        ),
        base_url="https://www.workable.com/api/accounts/{account}",
        docs_url="https://workable.com/",
        notes="Official employer source; adapter migration remains pending.",
        verified=True, companies=["qiddiya"], verified_at="2026-08-06",
    ),
    _entry(
        "jsonld", "Employer Career Pages (JobPosting JSON-LD / sitemaps)",
        "employer_page", 2, "none", "exact", True, STATUS_ACTIVE,
        base_url="<employer careers URL or sitemap URL>",
        docs_url="https://schema.org/JobPosting",
        notes="Official employer verification and ingestion source.",
    ),
    _entry(
        "brave_search", "Brave Search API", "discovery", 2,
        "api_key", "none", False, STATUS_PARTIAL,
        blocked_reason="Unavailable until BRAVE_SEARCH_API_KEY is configured.",
        base_url="https://api.search.brave.com/res/v1/web/search",
        docs_url="https://api-dashboard.search.brave.com/app/documentation/web-search/get-started",
        notes="Discovery-only; official employer or ATS verification is mandatory.",
        secret_env="BRAVE_SEARCH_API_KEY",
    ),
    _entry(
        "jooble", "Jooble REST API", "aggregator_api", 2,
        "api_key", "none", False, STATUS_PARTIAL,
        blocked_reason="Unavailable until JOOBLE_API_KEY is configured.",
        base_url="https://jooble.org/api/{api_key}",
        docs_url="https://help.jooble.org/en/support/solutions/articles/60001448238-rest-api-documentation",
        notes="Licensed discovery-only aggregator; never authoritative without official verification.",
        secret_env="JOOBLE_API_KEY",
    ),
    _entry(
        "careerjet", "Careerjet Publisher API", "aggregator_api", 2,
        "api_key + actual user IP/user-agent", "exact", False, STATUS_PARTIAL,
        blocked_reason=(
            "Manual user-triggered calls only. Requires API key, actual triggering user IP "
            "and user-agent."
        ),
        base_url="https://search.api.careerjet.net/v4/query",
        docs_url="https://www.careerjet.com/partners/api",
        notes=(
            "Discovery-only. CAREERJET_API_KEY is canonical; CAREERJET_AFFID is accepted "
            "only as a legacy secret-name alias."
        ),
        secret_env="CAREERJET_API_KEY|CAREERJET_AFFID",
        manual_only=True,
    ),
    _entry(
        "search_discovery", "DuckDuckGo Search Discovery -> Official Verification",
        "discovery", 3, "none", "none", False, STATUS_PARTIAL,
        blocked_reason="DuckDuckGo Instant Answer is best-effort and can return no vacancy URLs.",
        base_url="https://api.duckduckgo.com/?q=...&format=json",
        docs_url="https://duckduckgo.com/duckduckgo-help-pages/results/instant-answer-api/",
        notes="Discovery-only; official verification is mandatory.",
    ),
    _entry(
        "gcc_freehire", "Freehire Public API", "aggregator_api", 2, "none",
        "approximate", False, STATUS_ACTIVE,
        base_url="https://freehire.me",
        notes="Secondary discovery-only source without SLA; official verification is mandatory.",
    ),
    _entry(
        "inbox_gmail", "Gmail Recruiter / Job-Alert Inbox", "inbox", 3,
        "oauth connector", "unknown", False, STATUS_BLOCKED,
        blocked_reason=(
            "Repository-native Gmail authentication is unavailable; mailbox access must be "
            "provided by an authorized connector. Normalization remains available."
        ),
        base_url="<mailbox>",
        notes="Inbound discovery only; mailbox-derived data remains outside Git.",
    ),
    _entry(
        "linkedin_alerts", "LinkedIn Job Alerts", "inbox", 3,
        "authenticated email alert", "unknown", False, STATUS_PARTIAL,
        blocked_reason="Alerts must be configured manually in the authenticated LinkedIn account.",
        notes="Email-alert normalization only; no LinkedIn scraping.",
    ),
    _entry(
        "bayt_alerts", "Bayt Job Alerts", "inbox", 3,
        "authenticated email alert", "unknown", False, STATUS_PARTIAL,
        blocked_reason="Alerts must be configured manually in the authenticated Bayt account.",
        notes="Email-alert normalization only; no Bayt scraping.",
    ),
    _entry(
        "naukrigulf_alerts", "NaukriGulf Job Alerts", "inbox", 3,
        "authenticated email alert", "unknown", False, STATUS_PARTIAL,
        blocked_reason="Alerts must be configured manually in the authenticated NaukriGulf account.",
        notes="Email-alert normalization only; no NaukriGulf scraping.",
    ),
    _entry(
        "gulftalent_alerts", "GulfTalent Job Alerts", "inbox", 3,
        "authenticated email alert", "unknown", False, STATUS_PARTIAL,
        blocked_reason="Alerts must be configured manually in the authenticated GulfTalent account.",
        notes="Email-alert normalization only; no GulfTalent scraping.",
    ),
    _entry(
        "indeed_alerts", "Indeed Job Alerts", "inbox", 3,
        "authenticated email alert", "unknown", False, STATUS_PARTIAL,
        blocked_reason="Alerts must be configured manually in the authenticated Indeed account.",
        notes="Email-alert normalization only; no Indeed scraping.",
    ),
    _entry(
        "foundit_alerts", "Foundit Job Alerts", "inbox", 3,
        "authenticated email alert", "unknown", False, STATUS_PARTIAL,
        blocked_reason="Alerts must be configured manually in the authenticated Foundit account.",
        notes="Email-alert normalization only; no Foundit scraping.",
    ),
    _entry(
        "gotogulf_alerts", "Gotogulf Job Alerts", "inbox", 3,
        "authenticated email alert", "unknown", False, STATUS_PARTIAL,
        blocked_reason="Alerts must be configured manually in the authenticated Gotogulf account.",
        notes="Email-alert normalization only; no Gotogulf scraping.",
    ),
    _entry(
        "gcc_bayt", "Bayt.com", "board_web", 3, "none", "approximate",
        False, STATUS_BLOCKED,
        blocked_reason="Authenticated or anti-bot scraping is prohibited; use alerts only.",
        base_url="https://www.bayt.com",
        notes="Residential fallback is explicitly denied.",
    ),
    _entry(
        "gcc_naukrigulf", "NaukriGulf.com", "board_web", 3, "none",
        "approximate", False, STATUS_BLOCKED,
        blocked_reason="Authenticated or anti-bot scraping is prohibited; use alerts only.",
        base_url="https://www.naukrigulf.com",
        notes="Residential fallback is explicitly denied.",
    ),
    _entry(
        "gcc_gulftalent", "GulfTalent", "board_web", 3, "browser/account",
        "approximate", False, STATUS_BLOCKED,
        blocked_reason="Authenticated or anti-bot scraping is prohibited; use alerts only.",
        base_url="https://www.gulftalent.com",
        notes="Residential fallback is explicitly denied.",
    ),
    _entry(
        "board_indeed", "Indeed", "board_web", 3, "none", "approximate",
        False, STATUS_BLOCKED,
        blocked_reason="Authenticated or anti-bot scraping is prohibited; use alerts only.",
        base_url="https://www.indeed.com",
        notes="Residential fallback is explicitly denied.",
    ),
    _entry(
        "board_foundit", "Foundit", "board_web", 3, "account", "approximate",
        False, STATUS_BLOCKED,
        blocked_reason="Authenticated or anti-bot scraping is prohibited; use alerts only.",
        base_url="https://www.foundit.in",
        notes="Residential fallback is explicitly denied.",
    ),
    _entry(
        "board_gotogulf", "Gotogulf", "board_web", 3, "none", "approximate",
        False, STATUS_BLOCKED,
        blocked_reason="Direct board automation is not approved; use alerts only.",
        base_url="https://www.gotogulf.com",
        notes="Residential fallback is explicitly denied.",
    ),
    _entry(
        "linkedin_public", "LinkedIn Public Jobs", "board_web", 3,
        "session", "approximate", False, STATUS_BLOCKED,
        blocked_reason="Authenticated or anti-bot scraping is prohibited; use alerts only.",
        base_url="https://www.linkedin.com/jobs",
        notes="Residential fallback is explicitly denied.",
    ),
]

_BY_ID: dict[str, dict[str, Any]] | None = None


def _index() -> dict[str, dict[str, Any]]:
    global _BY_ID
    if _BY_ID is None:
        _BY_ID = {item["id"]: item for item in _SOURCES}
    return _BY_ID


def sources() -> list[dict[str, Any]]:
    return [dict(item) for item in _SOURCES]


def get_source(source_id: str) -> dict[str, Any]:
    item = _index().get(source_id)
    if item is None:
        raise KeyError(f"Unknown source id: {source_id!r}")
    return dict(item)


def _secret_configured(spec: str) -> bool:
    if not spec:
        return True
    return any(bool(os.environ.get(name, "").strip()) for name in spec.split("|"))


def runtime_source_status() -> list[dict[str, Any]]:
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
    return {
        "schema_version": 1,
        "purpose": "Career Engine discovery source registry (canonical, in-repo)",
        "no_send_policy": True,
        "official_verification_required_for_discovery_sources": True,
        "sources": _SOURCES,
        "capability_matrix": capability_matrix(),
        "runtime_status": runtime_source_status(),
    }
