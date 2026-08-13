"""Bounded official-page scan for the Consultants bookmark set."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from .base import SourceError, SourceUnavailable
from .cli import build_adapter
from .provenance import utc_now_iso

def _canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))

def scan_consultants(*, root: Path, limit: int = 25, offline: bool = False) -> dict[str, Any]:
    path = root / "projects/job-automation/config/consultants-bookmarks.v1.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["bookmarks"]
    report: dict[str, Any] = {"schema_version": 1, "generated_at": utc_now_iso(), "adapter": "jsonld", "jobs": [], "sources": [], "duplicates_dropped": 0, "send_or_submit": False, "notes": ["Only official JobPosting JSON-LD jobs are emitted."]}
    seen_urls: set[str] = set(); seen_jobs: set[str] = set()
    for row in rows:
        if row.get("status") != "active" or row.get("scan") is not True: continue
        source = {"source_id": row["id"], "source_name": row["label"], "attempted": False, "status": "skipped", "adapter": "jsonld", "route": "official_page_jsonld", "jobs_fetched": 0, "verified_authoritative": False, "error": ""}
        if row.get("duplicate_of"):
            source["error"] = f"duplicate_of:{row['duplicate_of']}"; report["duplicates_dropped"] += 1; report["sources"].append(source); continue
        canonical = _canonical_url(row["url"])
        if canonical in seen_urls:
            source["error"] = "duplicate canonical employer endpoint"; report["duplicates_dropped"] += 1; report["sources"].append(source); continue
        seen_urls.add(canonical); source["attempted"] = True; source["status"] = "empty"
        try:
            adapter = build_adapter("jsonld", offline=offline)
            jobs = adapter.search(company=row["url"], limit=max(1, min(limit, 100)), fetch_full=True, offline=offline)
            source["jobs_fetched"] = len(jobs); source["verified_authoritative"] = bool(jobs) and all(bool(j.provenance and j.provenance.official) for j in jobs); source["status"] = "ok" if jobs else "empty"
            for job in jobs:
                key = job.dedupe_key()
                if key in seen_jobs: report["duplicates_dropped"] += 1; continue
                seen_jobs.add(key); item = job.to_scanner_job(live_status="live"); item["consultant_source_id"] = row["id"]; item["consultant_source_name"] = row["label"]; report["jobs"].append(item)
        except SourceUnavailable as exc:
            source["status"] = "unavailable"; source["error"] = str(exc)
        except SourceError as exc:
            source["status"] = "error"; source["error"] = str(exc)
        report["sources"].append(source)
    active = [r for r in rows if r.get("status") == "active" and r.get("scan") is True]
    report["summary"] = {"active_records": len(active), "sources_attempted": sum(s["attempted"] for s in report["sources"]), "sources_skipped": sum(not s["attempted"] for s in report["sources"]), "jobs_emitted": len(report["jobs"]), "duplicates_dropped": report["duplicates_dropped"], "send_or_submit": False}
    return report
