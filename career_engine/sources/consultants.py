"""Bounded official-source scan for the Consultants bookmark set.

Provider-specific ATS parsing is reused from the maintained career-ops-ui
checkout when a bookmark has an explicit supported ``ats`` value. Direct
employer pages continue through the native JSON-LD verifier. Every emitted job
is therefore backed by an official employer/ATS source; this module never sends
or submits anything.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .adapters.career_ops_managed import ManagedCareerOpsAdapter
from .base import SourceError, SourceUnavailable
from .cli import build_adapter
from .managed_providers import PROVIDERS
from .provenance import utc_now_iso

_ATS_ALIASES = {
    "sap_successfactors": "successfactors",
    "oracle_cx": "oraclecloud",
    "oraclecloud": "oraclecloud",
    "workday": "workday",
    "icims": "icims",
    "avature": "avature",
    "eightfold": "eightfold",
    "jobvite": "jobvite",
    "jibeapply": "jibeapply",
    "bamboohr": "bamboohr",
    "breezy": "breezy",
    "comeet": "comeet",
    "teamtailor": "teamtailor",
    "greenhouse": "greenhouse",
    "lever": "lever",
    "ashby": "ashby",
    "smartrecruiters": "smartrecruiters",
}


def _canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def _managed_provider(row: dict[str, Any]) -> str | None:
    raw = str(row.get("ats") or "").strip().lower()
    provider = _ATS_ALIASES.get(raw)
    return provider if provider in PROVIDERS else None


def _adapter_for_row(row: dict[str, Any], *, offline: bool):
    """Prefer verified native adapters, then pinned managed providers, then JSON-LD."""
    explicit = str(row.get("adapter") or "").strip()
    if explicit == "taleo":
        from .adapters.taleo import TaleoAdapter
        return TaleoAdapter(fixtures_dir=None), explicit, "official_ats_api", None
    if explicit:
        return build_adapter(explicit, offline=offline), explicit, "official_ats_api", None
    provider = _managed_provider(row)
    if provider:
        return ManagedCareerOpsAdapter(provider), f"managed_{provider}", "official_managed_ats", provider
    return build_adapter("jsonld", offline=offline), "jsonld", "official_page_jsonld", None


def _company_spec(row: dict[str, Any], provider: str | None) -> str:
    explicit = str(row.get("adapter_company") or "").strip()
    if explicit:
        return explicit
    if not provider:
        return str(row["url"])
    payload: dict[str, Any] = {
        "name": str(row.get("label") or row.get("employer_id") or "").strip(),
        "careers_url": str(row["url"]).strip(),
        "provider": provider,
    }
    for key in ("api", "tenant", "site", "siteNumber", "company_eid"):
        value = row.get(key)
        if value not in (None, ""):
            payload[key] = value
    return json.dumps(payload, ensure_ascii=False)


def scan_consultants(*, root: Path, limit: int = 25, offline: bool = False) -> dict[str, Any]:
    path = root / "projects/job-automation/config/consultants-bookmarks.v1.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["bookmarks"]
    report: dict[str, Any] = {
        "schema_version": 2,
        "generated_at": utc_now_iso(),
        "adapter": "official_employer_or_ats",
        "jobs": [],
        "sources": [],
        "duplicates_dropped": 0,
        "send_or_submit": False,
        "notes": [
            "Official ATS integrations are preferred when explicitly configured; direct employer pages use JSON-LD fallback.",
            "Managed ATS code is pinned and fails closed when the reviewed external checkout is unavailable or moved.",
        ],
    }
    seen_urls: set[str] = set()
    seen_jobs: set[str] = set()
    for row in rows:
        if row.get("status") != "active" or row.get("scan") is not True:
            continue
        explicit = str(row.get("adapter") or "").strip()
        provider = None if explicit else _managed_provider(row)
        adapter_name = explicit or (f"managed_{provider}" if provider else "jsonld")
        route = "official_ats_api" if explicit else ("official_managed_ats" if provider else "official_page_jsonld")
        source = {
            "source_id": row["id"],
            "source_name": row["label"],
            "attempted": False,
            "status": "skipped",
            "adapter": adapter_name,
            "route": route,
            "jobs_fetched": 0,
            "verified_authoritative": False,
            "error": "",
        }
        if row.get("duplicate_of"):
            source["error"] = f"duplicate_of:{row['duplicate_of']}"
            report["duplicates_dropped"] += 1
            report["sources"].append(source)
            continue
        canonical = _canonical_url(row["url"])
        if canonical in seen_urls:
            source["error"] = "duplicate canonical employer endpoint"
            report["duplicates_dropped"] += 1
            report["sources"].append(source)
            continue
        seen_urls.add(canonical)
        source["attempted"] = True
        source["status"] = "empty"
        try:
            adapter, adapter_name, route, selected_provider = _adapter_for_row(row, offline=offline)
            source["adapter"] = adapter_name
            source["route"] = route
            company = _company_spec(row, selected_provider)
            jobs = adapter.search(
                company=company,
                location=row.get("location"),
                limit=max(1, min(limit, 100)),
                fetch_full=True,
                offline=offline,
            )
            source["jobs_fetched"] = len(jobs)
            source["verified_authoritative"] = bool(jobs) and all(
                bool(j.provenance and j.provenance.official) for j in jobs
            )
            source["status"] = "ok" if jobs else ("parser-needed" if adapter_name == "jsonld" else "empty")
            for job in jobs:
                key = job.dedupe_key()
                if key in seen_jobs:
                    report["duplicates_dropped"] += 1
                    continue
                seen_jobs.add(key)
                item = job.to_scanner_job(live_status="live")
                item["consultant_source_id"] = row["id"]
                item["consultant_source_name"] = row["label"]
                report["jobs"].append(item)
        except SourceUnavailable as exc:
            source["status"] = "unavailable"
            source["error"] = str(exc)
        except SourceError as exc:
            source["status"] = "error"
            source["error"] = str(exc)
        report["sources"].append(source)

    active = [r for r in rows if r.get("status") == "active" and r.get("scan") is True]
    report["summary"] = {
        "active_records": len(active),
        "sources_attempted": sum(bool(s["attempted"]) for s in report["sources"]),
        "sources_skipped": sum(not bool(s["attempted"]) for s in report["sources"]),
        "managed_ats_attempted": sum(
            bool(s["attempted"]) and str(s["adapter"]).startswith("managed_")
            for s in report["sources"]
        ),
        "jobs_emitted": len(report["jobs"]),
        "duplicates_dropped": report["duplicates_dropped"],
        "send_or_submit": False,
    }
    return report
