#!/usr/bin/env python3
"""Unify every Career Engine intake/status surface into the canonical tracker.

The canonical operational record remains projects/job-automation/data/jobs.csv +
per-job JSON + append-only events. here.now Site Data, legacy dashboard JSON,
submission archives, scanners and chat/manual intake are inputs/evidence only.

This utility is intentionally runtime-only with respect to personal data: it
reads/writes the ignored tracker/runtime paths and never commits live records.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_DEFAULT = Path("/home/hameedo/projects/ai-job-search")
DEFAULT_SLUG = "gilded-timber-xfj7"
APPLIED_VALUES = {
    "applied", "submitted", "sent", "application_submitted", "email_sent",
    "submitted_pending_response", "email_sent_owner_confirmed",
}
INACTIVE_VALUES = {
    "closed", "deleted", "expired", "inactive", "removed", "unavailable",
    "withdrawn", "cancelled", "canceled", "rejected",
}
READY_VALUES = {
    "awaiting_owner_approval", "owner_review_ready", "ready_for_review",
    "generated_content_valid", "rendered", "render_complete", "packaged",
}
SUBMISSION_EVENTS = {"application_submitted", "email_sent_owner_confirmed"}
STAGE_TO_PROCESSING = {
    "found": "ingested",
    "manual_review_needed": "manual_review_needed",
    "ready_review": "awaiting_owner_approval",
    "inactive": "inactive",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def text_key(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def url_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.netloc.lower().removeprefix("www.")
        path = re.sub(r"/+", "/", parsed.path).rstrip("/")
        # LinkedIn identity is the numeric job id, independent of locale/title.
        match = re.search(r"/jobs/view/(?:[^/?#]*-)?(\d+)(?:/|$)", path, flags=re.I)
        if "linkedin.com" in host and match:
            return f"linkedin-job:{match.group(1)}"
        return f"{host}{path}".lower()
    except Exception:
        return raw.rstrip("/").lower()


def load_tracker(repo: Path) -> Any:
    path = repo / "projects/job-automation/tracker.py"
    spec = importlib.util.spec_from_file_location("career_tracker_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to import canonical tracker from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.CareerTracker(repo / "projects/job-automation")


def data_of(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("data")
    return value if isinstance(value, dict) else record


def read_json(path: Path, fallback: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def load_api_key() -> str:
    raw = os.environ.get("HERENOW_API_KEY", "").strip()
    if not raw:
        credential = Path.home() / ".herenow" / "credentials"
        if credential.is_file():
            raw = credential.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError("HERENOW_API_KEY is not configured")
    if raw.startswith("{"):
        parsed = json.loads(raw)
        raw = str(parsed.get("apiKey") or parsed.get("api_key") or parsed.get("key") or parsed.get("token") or parsed.get("secret") or "").strip()
    if not raw:
        raise RuntimeError("Unable to parse here.now API key")
    return raw


class HereNow:
    def __init__(self, slug: str, api_key: str):
        self.slug = slug
        self.api_key = api_key
        self.base = f"https://here.now/api/v1/publishes/{slug}/data"

    def _request(self, url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"here.now HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"here.now request failed: {exc.reason}") from exc
        return json.loads(raw.decode("utf-8")) if raw else {}

    def records(self, collection: str, limit: int = 1000) -> list[dict[str, Any]]:
        result = self._request(f"{self.base}/{collection}?limit={max(1, min(limit, 1000))}")
        return list(result.get("records") or [])

    def patch(self, collection: str, record_id: str, fields: dict[str, Any]) -> None:
        self._request(
            f"{self.base}/{collection}/{urllib.parse.quote(record_id)}",
            method="PATCH",
            payload=fields,
        )


def record_route(record: dict[str, Any]) -> str:
    state = record.get("processing_state") or {}
    route = state.get("route") or {}
    return norm(route.get("route") or record.get("job", {}).get("route"))


def has_generated_package(repo: Path, job_id: str) -> bool:
    artifact = repo / "projects/job-automation/artifacts" / job_id
    if not artifact.is_dir():
        return False
    names = [path.name.lower() for path in artifact.iterdir() if path.is_file()]
    return any(("cv" in name or "resume" in name) and (name.endswith(".pdf") or name.endswith(".docx")) for name in names)


def canonical_stage(record: dict[str, Any], repo: Path) -> str:
    job = record.get("job") or {}
    processing = norm(job.get("processing_status"))
    application = norm(job.get("application_status"))
    state = record.get("processing_state") or {}
    live = norm(state.get("live_status") or record.get("live_status"))
    if processing == "superseded":
        return "superseded"
    if application in APPLIED_VALUES or processing == "applied":
        return "applied"
    if processing in INACTIVE_VALUES or application in INACTIVE_VALUES or live in INACTIVE_VALUES:
        return "inactive"
    if processing == "manual_review_needed":
        return "manual_review_needed"
    if processing in READY_VALUES or has_generated_package(repo, str(job.get("job_id", ""))):
        return "ready_review"
    return "found"


def application_bucket(record: dict[str, Any]) -> str:
    job = record.get("job") or {}
    application = norm(job.get("application_status"))
    processing = norm(job.get("processing_status"))
    if application not in APPLIED_VALUES and processing != "applied":
        return ""
    if application in {"sent", "email_sent", "email_sent_owner_confirmed"}:
        return "sent_email"
    if application in {"submitted", "application_submitted", "submitted_pending_response"}:
        return "submitted_portal"
    return "sent_email" if record_route(record) == "email" else "submitted_portal"


def tracker_records(tracker: Any) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(tracker.jobs_dir.glob("*.json")):
        payload = read_json(path, {})
        job_id = str((payload.get("job") or {}).get("job_id") or path.stem)
        if job_id:
            records[job_id] = payload
    return records


def repair_tracker_layout(tracker: Any, *, apply: bool) -> dict[str, Any]:
    rows = tracker.list_rows()
    by_row = {row.get("job_id", ""): row for row in rows if row.get("job_id")}
    records = tracker_records(tracker)
    orphan_rows = sorted(set(by_row) - set(records))
    orphan_json = sorted(set(records) - set(by_row))
    reconstructed: list[str] = []
    if apply:
        for job_id in orphan_rows:
            row = dict(by_row[job_id])
            row.setdefault("processing_status", "ingested")
            row.setdefault("application_status", "not_submitted")
            record = {
                "job": row,
                "full_job_description": "",
                "normalized_requirements": [],
                "provenance": {"source": row.get("source", "legacy"), "source_url": row.get("source_url", ""), "reconstructed_from": "orphan_jobs_csv_row"},
                "scoring": {"fit_score": row.get("fit_score", ""), "rationale": [], "gaps": []},
                "evidence_matches": [],
                "processing_state": {"owner": row.get("owner", "system"), "status": row.get("processing_status", "ingested")},
                "generated_artifacts": [],
                "gmail_draft_reference": None,
                "history": [],
            }
            tracker._save_job_and_row(record)
            tracker.record_event(
                actor="system", entity_type="job", entity_id=job_id, action="created",
                before={}, after={"job": row},
                comment="Canonical tracker repair: reconstructed missing per-job JSON from preserved jobs.csv row.",
                confidence="medium", requires_owner_review=True,
            )
            reconstructed.append(job_id)
        # Per-job JSON is the current-record authority inside the tracker. Rebuild
        # the CSV index deterministically after preserving/reconstructing orphans.
        records = tracker_records(tracker)
        canonical_rows = [tracker._row_from_record(records[job_id]) for job_id in sorted(records)]
        tracker._write_rows(canonical_rows)
    return {"orphan_csv_rows": orphan_rows, "orphan_json_records": orphan_json, "reconstructed": reconstructed}


def build_indexes(records: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    urls: dict[str, str] = {}
    external: dict[str, str] = {}
    text: dict[str, str] = {}
    for job_id, record in records.items():
        job = record.get("job") or {}
        if norm(job.get("processing_status")) == "superseded":
            continue
        for candidate in (
            job.get("source_url"),
            (record.get("processing_state") or {}).get("route", {}).get("application_url"),
            record.get("application_url"),
        ):
            key = url_key(candidate)
            if key:
                urls.setdefault(key, job_id)
        ext = text_key(job.get("external_job_id"))
        if ext:
            external.setdefault(ext, job_id)
        ident = "|".join((text_key(job.get("company")), text_key(job.get("role")), text_key(job.get("location"))))
        if ident.strip("|"):
            text.setdefault(ident, job_id)
    return {"urls": urls, "external": external, "text": text}


def resolve_existing(indexes: dict[str, dict[str, str]], item: dict[str, Any]) -> str:
    for candidate in (item.get("application_url"), item.get("source_url"), item.get("url")):
        key = url_key(candidate)
        if key and key in indexes["urls"]:
            return indexes["urls"][key]
    ext = text_key(item.get("external_job_id"))
    if ext and ext in indexes["external"]:
        return indexes["external"][ext]
    ident = "|".join((text_key(item.get("company")), text_key(item.get("role")), text_key(item.get("location"))))
    if ident.strip("|") and ident in indexes["text"]:
        return indexes["text"][ident]
    return ""


def create_stub(tracker: Any, item: dict[str, Any], *, source_label: str, comment: str) -> str:
    company = str(item.get("company") or "Unknown employer").strip()
    role = str(item.get("role") or "Unknown role").strip()
    location = str(item.get("location") or "").strip()
    source_url = str(item.get("source_url") or item.get("application_url") or item.get("url") or "").strip()
    external_job_id = str(item.get("external_job_id") or "").strip()
    identity = url_key(source_url) or external_job_id or f"{text_key(company)}|{text_key(role)}|{text_key(location)}"
    digest = hashlib.sha256(f"canonical-stub|{identity}".encode("utf-8")).hexdigest()[:20]
    now = utc_now()
    decision = norm(item.get("decision"))
    status = "rejected" if decision == "do_not_pursue" else "manual_review_needed"
    score = item.get("score", "")
    job = {
        "job_id": digest,
        "source": source_label,
        "external_job_id": external_job_id,
        "source_url": source_url,
        "company": company,
        "role": role,
        "location": location,
        "posting_date": str(item.get("posting_date") or ""),
        "closing_date": str(item.get("closing_date") or ""),
        "jd_hash": "",
        "full_jd_path": f"projects/job-automation/data/jobs/{digest}.json",
        "first_seen": str(item.get("found_at") or now),
        "last_seen": now,
        "ingested_by": "system",
        "fit_score": score,
        "priority": decision or "unrated",
        "owner": "chatgpt",
        "processing_status": status,
        "resume_status": "not_started",
        "cover_letter_status": "not_started",
        "pdf_status": "not_started",
        "gmail_draft_status": "not_started",
        "application_status": "not_submitted",
        "outcome": "",
        "last_updated": now,
        "next_action": "Recover/verify full vacancy details before any application work" if not item.get("full_job_description") else "Review canonical imported vacancy",
        "notes": str(item.get("brief") or item.get("notes") or "Legacy/source-only job record; full JD may be unavailable."),
    }
    record = {
        "job": job,
        "full_job_description": str(item.get("full_job_description") or ""),
        "normalized_requirements": [],
        "provenance": {
            "source": source_label,
            "source_url": source_url,
            "intake_stub": not bool(item.get("full_job_description")),
            "legacy_key": str(item.get("key") or item.get("role_key") or ""),
        },
        "scoring": {"total": score, "raw_total": score, "recommendation": decision or "unrated", "rationale": [], "gaps": list(item.get("gaps") or [])},
        "evidence_matches": [],
        "processing_state": {"owner": "chatgpt", "status": status, "external_action_allowed": False, "live_status": "unverified"},
        "generated_artifacts": [],
        "gmail_draft_reference": None,
        "history": [],
    }
    tracker._save_job_and_row(record)
    tracker.record_event(
        actor="system", entity_type="job", entity_id=digest, action="created",
        before={}, after={"job": job}, comment=comment,
        source_refs=[source_url] if source_url else [], confidence="medium",
        requires_owner_review=not bool(item.get("full_job_description")),
    )
    return digest


def migrate_seed(tracker: Any, repo: Path, seed_path: Path, *, apply: bool) -> dict[str, Any]:
    seed = read_json(seed_path, []) or []
    records = tracker_records(tracker)
    indexes = build_indexes(records)
    matched: list[str] = []
    created: list[str] = []
    for item in seed:
        existing = resolve_existing(indexes, item)
        if existing:
            matched.append(existing)
            continue
        if apply:
            job_id = create_stub(
                tracker, item, source_label="legacy_dashboard",
                comment="Canonical tracker migration: imported legacy dashboard-only job into the shared tracker without inventing missing JD details.",
            )
            created.append(job_id)
            records = tracker_records(tracker)
            indexes = build_indexes(records)
    return {"seed_records": len(seed), "matched": sorted(set(matched)), "created": created}


def submission_note(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("note")
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def latest_by_role(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        data = data_of(record)
        key = str(data.get("role_key") or "")
        if not key:
            continue
        stamp = str(record.get("updatedAt") or record.get("createdAt") or data.get("updated_at") or "")
        prior = latest.get(key)
        prior_stamp = str(prior.get("updatedAt") or prior.get("createdAt") or "") if prior else ""
        if prior is None or stamp >= prior_stamp:
            latest[key] = record
    return latest


def job_id_from_role_key(role_key: str) -> str:
    return role_key[len("tracker-"):] if role_key.startswith("tracker-") else ""


def legacy_role_aliases(tracker: Any, repo: Path) -> dict[str, str]:
    """Map historical dashboard keys to existing active tracker ids only."""
    records = tracker_records(tracker)
    aliases: dict[str, str] = {}

    manifest = read_json(repo / "projects/job-automation/artifacts/five-applications-2026-08-04.json", {}) or {}
    for item in manifest.get("applications") or []:
        key = str(item.get("key") or "").strip()
        job_id = str(item.get("job_id") or "").strip()
        record = records.get(job_id) or {}
        if key and job_id and norm((record.get("job") or {}).get("processing_status")) != "superseded":
            aliases[key] = job_id

    indexes = build_indexes(records)
    seed = read_json(repo / "dashboard/career-review/legacy-tracker-seed.json", []) or []
    for item in seed:
        key = str(item.get("key") or "").strip()
        job_id = resolve_existing(indexes, item)
        if key and job_id:
            aliases[key] = job_id
    return aliases


def supersede_legacy_site_stubs(tracker: Any, aliases: dict[str, str], *, apply: bool) -> list[dict[str, str]]:
    """Retire temporary Site Data stubs once their legacy key resolves canonically."""
    changes: list[dict[str, str]] = []
    records = tracker_records(tracker)
    for job_id, record in records.items():
        job = record.get("job") or {}
        provenance = record.get("provenance") or {}
        if norm(job.get("processing_status")) == "superseded" or norm(job.get("source")) != "dashboard_site_data":
            continue
        legacy_key = str(provenance.get("legacy_key") or "").strip()
        canonical = str(aliases.get(legacy_key) or "").strip()
        if not canonical or canonical == job_id or canonical not in records:
            continue
        canonical_job = (records[canonical].get("job") or {})
        if text_key(job.get("company")) != text_key(canonical_job.get("company")) or text_key(job.get("role")) != text_key(canonical_job.get("role")):
            continue
        changes.append({"job_id": job_id, "canonical_job_id": canonical, "legacy_key": legacy_key})
        if not apply:
            continue
        notes = str(job.get("notes") or "").strip()
        state = dict(record.get("processing_state") or {})
        state.update({
            "status": "superseded",
            "canonical_job_id": canonical,
            "external_action_allowed": False,
            "superseded_at": utc_now(),
            "reason": "temporary Site Data intake stub resolved to an existing canonical CareerTracker job",
        })
        tracker.update_job(
            job_id,
            {
                "processing_status": "superseded",
                "next_action": f"Use canonical job {canonical}",
                "notes": f"{notes} Temporary Site Data stub superseded by canonical job {canonical}; history preserved.".strip(),
                "processing_state": state,
            },
            comment=f"Canonical tracker reconciliation: temporary Site Data stub for legacy key {legacy_key} superseded by existing canonical job {canonical}; no job history deleted.",
            actor="system", action="reviewed", confidence="high", requires_owner_review=False,
        )
    return changes


def resolve_site_role(
    tracker: Any,
    data: dict[str, Any],
    role_key: str,
    *,
    apply: bool,
    aliases: dict[str, str] | None = None,
) -> str:
    direct = job_id_from_role_key(role_key)
    if direct:
        try:
            record = tracker.get_job(direct)
            if norm((record.get("job") or {}).get("processing_status")) == "superseded":
                canonical = str((record.get("processing_state") or {}).get("canonical_job_id") or "").strip()
                if canonical:
                    tracker.get_job(canonical)
                    return canonical
            return direct
        except KeyError:
            pass
    alias_job_id = str((aliases or {}).get(role_key) or "").strip()
    if alias_job_id:
        try:
            tracker.get_job(alias_job_id)
            return alias_job_id
        except KeyError:
            pass
    records = tracker_records(tracker)
    indexes = build_indexes(records)
    enriched = dict(data)
    enriched.setdefault("key", role_key)
    existing = resolve_existing(indexes, enriched)
    if existing:
        return existing
    note = submission_note(data)
    for key in ("company", "role", "application_url", "url", "location", "external_job_id"):
        if not enriched.get(key) and note.get(key):
            enriched[key] = note[key]
    existing = resolve_existing(indexes, enriched)
    if existing:
        return existing
    if not apply or not (enriched.get("company") or enriched.get("role")):
        return ""
    return create_stub(
        tracker, enriched, source_label="dashboard_site_data",
        comment="Canonical tracker migration: imported a Site Data job that was missing from the shared tracker; preserved available metadata and left missing JD details unresolved.",
    )


def promote_submission(tracker: Any, job_id: str, *, event: str, data: dict[str, Any], source: str) -> bool:
    record = tracker.get_job(job_id)
    job = record.get("job") or {}
    target_application = "sent" if event == "email_sent_owner_confirmed" else "submitted"
    if norm(job.get("application_status")) == target_application and norm(job.get("processing_status")) == "applied":
        return False
    note = submission_note(data)
    package = dict(record.get("submission_package") or {})
    for key in ("submitted_at", "confirmation_reference", "template_id", "document_sha256", "cover_letter_sha256", "package_version", "application_url", "route"):
        value = data.get(key) or note.get(key)
        if value and not package.get(key):
            package[key] = value
    package["status_source"] = source
    changes: dict[str, Any] = {
        "processing_status": "applied",
        "application_status": target_application,
        "next_action": "Track employer response and follow up when appropriate",
    }
    if package:
        changes["submission_package"] = package
    tracker.update_job(
        job_id, changes,
        comment=f"Canonical submission reconciliation: {event} confirmed by {source}; application state promoted without inferring from a portal/email open.",
        actor="system", action="reviewed", source_refs=[str(data.get("url") or data.get("application_url") or "")],
        confidence="high", requires_owner_review=False,
    )
    return True


def scan_submission_manifests(repo: Path) -> list[dict[str, Any]]:
    root = repo / "projects/job-automation/artifacts"
    found: list[dict[str, Any]] = []
    if not root.is_dir():
        return found
    for manifest in root.glob("*/submissions/*/submission_manifest.json"):
        payload = read_json(manifest, {}) or {}
        if payload.get("status") != "archived":
            continue
        job_id = str(payload.get("job_id") or manifest.parts[-4])
        route = norm(payload.get("route"))
        found.append({
            "job_id": job_id,
            "event": "email_sent_owner_confirmed" if route == "email" else "application_submitted",
            "data": payload,
            "path": str(manifest),
        })
    return found


def exact_duplicate_groups(records: dict[str, dict[str, Any]]) -> list[list[str]]:
    identities: dict[str, list[str]] = defaultdict(list)
    for job_id, record in records.items():
        job = record.get("job") or {}
        if norm(job.get("processing_status")) == "superseded":
            continue
        key = url_key(job.get("source_url"))
        if not key:
            route_url = (record.get("processing_state") or {}).get("route", {}).get("application_url")
            key = url_key(route_url)
        if key:
            identities[f"url:{key}"].append(job_id)
        elif job.get("external_job_id"):
            identities[f"ext:{text_key(job.get('source'))}:{text_key(job.get('external_job_id'))}"].append(job_id)
    return [sorted(group) for group in identities.values() if len(group) > 1]


def choose_canonical_duplicate(records: dict[str, dict[str, Any]], group: list[str]) -> str:
    def rank(job_id: str) -> tuple[int, str, str]:
        record = records[job_id]
        applied = 0 if application_bucket(record) else 1
        first_seen = str((record.get("job") or {}).get("first_seen") or "9999")
        return (applied, first_seen, job_id)
    return sorted(group, key=rank)[0]


def supersede_exact_duplicates(tracker: Any, *, apply: bool) -> dict[str, Any]:
    records = tracker_records(tracker)
    groups = exact_duplicate_groups(records)
    changes: list[dict[str, str]] = []
    for group in groups:
        canonical = choose_canonical_duplicate(records, group)
        for duplicate in group:
            if duplicate == canonical:
                continue
            changes.append({"job_id": duplicate, "canonical_job_id": canonical})
            if not apply:
                continue
            record = tracker.get_job(duplicate)
            job = record.get("job") or {}
            notes = str(job.get("notes") or "").strip()
            suffix = f"Superseded exact duplicate of canonical job {canonical}."
            tracker.update_job(
                duplicate,
                {
                    "processing_status": "superseded",
                    "next_action": f"Use canonical job {canonical}",
                    "notes": f"{notes} {suffix}".strip(),
                },
                comment=f"Canonical tracker dedupe: exact URL/external identity duplicate superseded by {canonical}; history and artifacts preserved.",
                actor="system", action="reviewed", confidence="high", requires_owner_review=False,
            )
    return {"groups": groups, "superseded": changes}


def reconcile_site_data(tracker: Any, repo: Path, here: HereNow, *, apply: bool) -> dict[str, Any]:
    workflow_records = here.records("workflow", 1000)
    history_records = here.records("history", 1000)
    aliases = legacy_role_aliases(tracker, repo)
    submission_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    promoted: list[str] = []
    unresolved: list[dict[str, str]] = []

    for record in history_records:
        data = data_of(record)
        event = norm(data.get("event"))
        if event not in SUBMISSION_EVENTS:
            continue
        role_key = str(data.get("role_key") or "")
        job_id = resolve_site_role(tracker, data, role_key, apply=apply, aliases=aliases)
        if not job_id:
            unresolved.append({"role_key": role_key, "reason": "submission_event_job_unresolved"})
            continue
        submission_events[role_key].append(record)
        submission_events[f"tracker-{job_id}"].append(record)
        if apply and promote_submission(tracker, job_id, event=event, data=data, source="here.now explicit owner confirmation"):
            promoted.append(job_id)

    latest_workflow = latest_by_role(workflow_records)
    workflow_updates: list[dict[str, str]] = []
    workflow_blocked_applied: list[str] = []
    for role_key, record in latest_workflow.items():
        data = data_of(record)
        job_id = resolve_site_role(tracker, data, role_key, apply=apply, aliases=aliases)
        if not job_id:
            unresolved.append({"role_key": role_key, "reason": "workflow_job_unresolved"})
            continue
        requested = norm(data.get("stage"))
        if requested == "approved":
            requested = "ready_review"
        current_record = tracker.get_job(job_id)
        current_stage = canonical_stage(current_record, repo)
        if requested == "applied":
            has_explicit = bool(submission_events.get(role_key)) or current_stage == "applied"
            if not has_explicit:
                workflow_blocked_applied.append(role_key)
            # Never infer an application from a workflow label alone.
        elif requested in STAGE_TO_PROCESSING and current_stage != "applied":
            target_processing = STAGE_TO_PROCESSING[requested]
            if apply and norm((current_record.get("job") or {}).get("processing_status")) != target_processing:
                tracker.update_job(
                    job_id,
                    {"processing_status": target_processing},
                    comment=f"Canonical workflow reconciliation: owner dashboard stage '{requested}' written into the shared tracker.",
                    actor="system", action="reviewed", confidence="high", requires_owner_review=False,
                )
                current_record = tracker.get_job(job_id)
                current_stage = canonical_stage(current_record, repo)
        canonical = canonical_stage(tracker.get_job(job_id), repo)
        if canonical == "superseded":
            canonical = "inactive"
        canonical_role_key = f"tracker-{job_id}"
        if requested != canonical or role_key != canonical_role_key:
            workflow_updates.append({"role_key": role_key, "from": requested, "to": canonical})
            if apply and record.get("id"):
                here.patch("workflow", str(record["id"]), {"stage": canonical, "role_key": canonical_role_key})

    return {
        "workflow_records": len(workflow_records),
        "history_records": len(history_records),
        "submission_promoted": sorted(set(promoted)),
        "workflow_patched_to_canonical": workflow_updates,
        "workflow_applied_without_evidence_blocked": workflow_blocked_applied,
        "unresolved": unresolved,
    }


def reconcile_submission_archives(tracker: Any, repo: Path, *, apply: bool) -> dict[str, Any]:
    manifests = scan_submission_manifests(repo)
    promoted: list[str] = []
    missing_jobs: list[str] = []
    for item in manifests:
        job_id = item["job_id"]
        try:
            tracker.get_job(job_id)
        except KeyError:
            missing_jobs.append(job_id)
            continue
        if apply and promote_submission(tracker, job_id, event=item["event"], data=item["data"], source=f"immutable submission manifest {item['path']}"):
            promoted.append(job_id)
    return {"manifests": len(manifests), "promoted": sorted(set(promoted)), "missing_jobs": sorted(set(missing_jobs))}


def canonical_summary(tracker: Any, repo: Path) -> dict[str, Any]:
    records = tracker_records(tracker)
    counts = Counter()
    application_counts = Counter()
    processing_counts = Counter()
    application_raw = Counter()
    source_counts = Counter()
    ingested_by = Counter()
    jobs: list[dict[str, Any]] = []
    for job_id, record in records.items():
        job = record.get("job") or {}
        processing = norm(job.get("processing_status"))
        if processing == "superseded":
            counts["superseded_excluded"] += 1
            continue
        stage = canonical_stage(record, repo)
        counts["tracked_total"] += 1
        if stage == "found": counts["found"] += 1
        elif stage == "manual_review_needed": counts["needs_review"] += 1
        elif stage == "ready_review": counts["ready_for_review"] += 1
        elif stage == "applied": counts["applied_total"] += 1
        elif stage == "inactive": counts["closed_inactive"] += 1
        if stage != "inactive": counts["active_total"] += 1
        bucket = application_bucket(record)
        if bucket:
            application_counts[bucket] += 1
        processing_counts[processing or "unknown"] += 1
        application_raw[norm(job.get("application_status")) or "unknown"] += 1
        source_counts[str(job.get("source") or "unknown")] += 1
        ingested_by[str(job.get("ingested_by") or "unknown")] += 1
        jobs.append({
            "job_id": job_id,
            "company": job.get("company", ""),
            "role": job.get("role", ""),
            "canonical_stage": stage,
            "processing_status": job.get("processing_status", ""),
            "application_status": job.get("application_status", ""),
            "application_bucket": bucket,
            "source": job.get("source", ""),
            "ingested_by": job.get("ingested_by", ""),
        })
    # Applied total is the number of unique applied jobs; submitted+sent are a
    # route/evidence split of that same set and therefore must add up exactly.
    counts["submitted_portal"] = application_counts["submitted_portal"]
    counts["sent_email"] = application_counts["sent_email"]
    counts["application_split_matches_applied"] = int(
        counts["submitted_portal"] + counts["sent_email"] == counts["applied_total"]
    )
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "authority": "CareerTracker",
        "counts": dict(counts),
        "raw_processing_status_counts": dict(sorted(processing_counts.items())),
        "raw_application_status_counts": dict(sorted(application_raw.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "ingested_by_counts": dict(sorted(ingested_by.items())),
        "jobs": sorted(jobs, key=lambda item: item["job_id"]),
    }


def verify_summary(summary: dict[str, Any]) -> list[str]:
    counts = summary.get("counts") or {}
    issues: list[str] = []
    lifecycle_sum = sum(int(counts.get(key, 0) or 0) for key in ("found", "needs_review", "ready_for_review", "applied_total", "closed_inactive"))
    if lifecycle_sum != int(counts.get("tracked_total", 0) or 0):
        issues.append(f"lifecycle counts {lifecycle_sum} != tracked_total {counts.get('tracked_total', 0)}")
    split = int(counts.get("submitted_portal", 0) or 0) + int(counts.get("sent_email", 0) or 0)
    if split != int(counts.get("applied_total", 0) or 0):
        issues.append(f"submitted_portal + sent_email = {split}, applied_total = {counts.get('applied_total', 0)}")
    ids = [item.get("job_id") for item in summary.get("jobs", [])]
    if len(ids) != len(set(ids)):
        issues.append("canonical summary contains duplicate job IDs")
    return issues


def resolve_slug(repo: Path, explicit: str) -> str:
    if explicit:
        return explicit
    state = read_json(repo / "dashboard/career-review/.deploy.json", {}) or {}
    return str(state.get("slug") or DEFAULT_SLUG)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile every Career Engine job/status surface into CareerTracker")
    parser.add_argument("--repo", default=str(REPO_DEFAULT))
    parser.add_argument("--site-slug", default="")
    parser.add_argument("--apply", action="store_true", help="Apply safe migrations/reconciliation; default is audit only")
    parser.add_argument("--skip-site-data", action="store_true", help="Do not read/patch here.now Site Data")
    parser.add_argument("--legacy-seed", default="dashboard/career-review/legacy-tracker-seed.json")
    parser.add_argument("--summary-out", default="projects/job-automation/runtime/canonical-tracker-summary.json")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    tracker = load_tracker(repo)
    tracker.ensure_layout()
    runtime = repo / "projects/job-automation/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    lock_path = runtime / "canonical-tracker-unify.lock"

    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        report: dict[str, Any] = {
            "schema_version": 1,
            "started_at": utc_now(),
            "apply": bool(args.apply),
            "authority": "CareerTracker",
        }
        report["layout"] = repair_tracker_layout(tracker, apply=args.apply)
        report["legacy_migration"] = migrate_seed(
            tracker, repo, repo / args.legacy_seed, apply=args.apply,
        )
        aliases = legacy_role_aliases(tracker, repo)
        report["legacy_site_stub_cleanup"] = supersede_legacy_site_stubs(tracker, aliases, apply=args.apply)
        report["submission_archives"] = reconcile_submission_archives(tracker, repo, apply=args.apply)
        report["dedupe"] = supersede_exact_duplicates(tracker, apply=args.apply)
        if args.skip_site_data:
            report["site_data"] = {"skipped": True}
        else:
            slug = resolve_slug(repo, args.site_slug)
            try:
                here = HereNow(slug, load_api_key())
                report["site_data"] = reconcile_site_data(tracker, repo, here, apply=args.apply)
            except Exception as exc:
                report["site_data"] = {"error": f"{type(exc).__name__}: {exc}"}
        # Run exact duplicate pass again because Site Data/legacy migration may
        # have introduced an identity already present under another historical id.
        report["dedupe_after_migration"] = supersede_exact_duplicates(tracker, apply=args.apply)
        if args.apply:
            # Final JSON->CSV index rebuild after all job mutations.
            records = tracker_records(tracker)
            tracker._write_rows([tracker._row_from_record(records[job_id]) for job_id in sorted(records)])
        summary = canonical_summary(tracker, repo)
        issues = verify_summary(summary)
        report["summary"] = summary
        report["verification_issues"] = issues
        report["valid"] = not issues and not bool((report.get("site_data") or {}).get("error"))
        report["finished_at"] = utc_now()
        summary_target = repo / args.summary_out
        if args.apply:
            summary_target.parent.mkdir(parents=True, exist_ok=True)
            temp = summary_target.with_suffix(summary_target.suffix + ".tmp")
            temp.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp.replace(summary_target)
            audit_target = runtime / "canonical-tracker-reconcile.latest.json"
            audit_target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
