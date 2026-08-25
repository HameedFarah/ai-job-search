from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import load_bundle
from .config import load_config
from .pipeline import _load_tracker
from .post_scan import reconcile_after_scan
from .service import prepare_job
from .targeting import auto_skip_title_reason

SCANNER_ACTORS = {
    "hermes_scanner": "hermes",
    "chatgpt_scanner": "chatgpt",
}


def load_scan_input(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scan_paths: list[dict[str, Any]] = []
    if isinstance(payload, list):
        jobs = payload
    elif isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        jobs = payload["jobs"]
        raw_paths = payload.get("paths", payload.get("scan_paths", []))
        if raw_paths is not None:
            if not isinstance(raw_paths, list):
                raise ValueError("Scanner input paths/scan_paths must be an array when present")
            for index, item in enumerate(raw_paths):
                if not isinstance(item, dict):
                    raise ValueError(f"paths[{index}] must be an object")
                scan_paths.append(dict(item))
    else:
        raise ValueError("Scanner input must be a JSON array or an object containing jobs[]")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(jobs):
        if not isinstance(item, dict):
            raise ValueError(f"jobs[{index}] must be an object")
        result.append(dict(item))
    return result, scan_paths


def load_jobs(path: Path) -> list[dict[str, Any]]:
    jobs, _ = load_scan_input(path)
    return jobs


def _path_name(item: dict[str, Any], fallback: str) -> str:
    for key in (
        "consultant_source_name",
        "source_path",
        "search_path",
        "path",
        "source_name",
        "source",
    ):
        value = str(item.get(key, "")).strip()
        if value:
            return value
    return fallback


def _path_entry(stats: dict[str, Any], name: str) -> dict[str, Any]:
    paths = stats["by_path"]
    if name not in paths:
        paths[name] = {
            "path": name,
            "attempted": True,
            "status": "observed",
            "jobs_discovered": 0,
            "jobs_ingested": 0,
            "new_jobs": 0,
            "existing_jobs": 0,
            "generation_candidates": 0,
            "blocked_or_below_threshold": 0,
            "error": "",
        }
    return paths[name]


def _refresh_path_totals(stats: dict[str, Any]) -> None:
    paths = list(stats["by_path"].values())
    stats["paths_total"] = len(paths)
    stats["paths_scanned"] = sum(bool(item.get("attempted", True)) for item in paths)
    stats["paths_failed"] = sum(
        bool(item.get("attempted", True))
        and str(item.get("status", "")).lower() in {"error", "unavailable", "failed"}
        for item in paths
    )


def add_path_scan_statistics(report: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge source-attempt metadata, including zero-result and failed paths."""
    stats = report["statistics"]
    for source in sources:
        name = str(
            source.get("source_name")
            or source.get("path")
            or source.get("source_id")
            or source.get("url")
            or "unknown"
        ).strip()
        entry = _path_entry(stats, name)
        attempted = bool(source.get("attempted", True))
        entry["attempted"] = attempted
        entry["status"] = str(source.get("status", entry["status"]))
        discovered = source.get("jobs_fetched", source.get("jobs_found"))
        if isinstance(discovered, int):
            entry["jobs_discovered"] = max(entry["jobs_discovered"], discovered)
        error = str(source.get("error", "")).strip()
        if error:
            entry["error"] = error
    _refresh_path_totals(stats)
    return report


INSUFFICIENT_DESCRIPTION_MESSAGE = "Job description is too short to evaluate"
MANUAL_REVIEW_STATUS = "manual_review_needed"


def _reject_non_target_title(
    item: dict[str, Any],
    *,
    root: Path,
    actor: str,
    skip_reason: str,
) -> dict[str, Any]:
    """Persist an obvious non-target vacancy without creating review work.

    Title classification happens before JD normalization so a missing/truncated
    description cannot promote Civil Engineer, Site Inspector, Urban Designer,
    finance, reception/front-desk or similar roles into Manual Review Needed.
    The record and source provenance are retained for audit/deduplication.
    """
    _, paths = load_config(root)
    tracker = _load_tracker(paths)
    description = str(item.get("full_job_description") or item.get("job_description") or "").strip()
    if not description:
        description = "[Source provided no job description]"
    source_url = str(item.get("source_url") or "").strip()
    application_url = str(item.get("application_url") or "").strip()
    reason = "The role title is clearly outside the owner target lane and was skipped before manual review or generation."
    ingest = tracker.ingest(
        {
            "source": str(item.get("source") or "manual"),
            "external_job_id": str(item.get("external_job_id") or item.get("reference") or ""),
            "source_url": source_url,
            "company": str(item.get("company") or "Unknown company"),
            "role": str(item.get("role") or "Unknown role"),
            "location": str(item.get("location") or ""),
            "posting_date": str(item.get("posting_date") or ""),
            "posting_date_precision": str(item.get("posting_date_precision") or ""),
            "posting_date_source": str(item.get("posting_date_source") or ""),
            "full_job_description": description,
            "fit_score": "",
            "priority": "rejected",
            "processing_status": "rejected",
            "next_action": "Skipped automatically as a non-target role",
            "notes": f"Automatic target-lane skip: {skip_reason}.",
            "provenance": item.get("provenance") if isinstance(item.get("provenance"), dict) else {},
            "scoring": {
                "total": None,
                "recommendation": "rejected",
                "reason_code": skip_reason,
                "reason": reason,
            },
            "processing_state": {
                "owner": actor,
                "status": "rejected",
                "reason_code": skip_reason,
                "skip_reason": skip_reason,
                "reason": reason,
                "source_url": source_url,
                "application_url": application_url,
                "external_action_allowed": False,
                "send_or_submit": False,
            },
        },
        comment=f"Skipped non-target role before JD normalization: {skip_reason}",
        actor=actor,
        source_refs=[value for value in (source_url, application_url) if value],
        confidence="high",
    )
    job_id = ingest["job_id"]
    tracker.update_job(
        job_id,
        {
            "fit_score": "",
            "priority": "rejected",
            "processing_status": "rejected",
            "next_action": "Skipped automatically as a non-target role",
            "scoring": {
                "total": None,
                "recommendation": "rejected",
                "reason_code": skip_reason,
                "reason": reason,
            },
            "processing_state": {
                "owner": actor,
                "status": "rejected",
                "reason_code": skip_reason,
                "skip_reason": skip_reason,
                "reason": reason,
                "source_url": source_url,
                "application_url": application_url,
                "external_action_allowed": False,
                "send_or_submit": False,
            },
        },
        comment=f"Classified title as a terminal non-target role: {skip_reason}",
        actor=actor,
        action="rejected",
        source_refs=[value for value in (source_url, application_url) if value],
        requires_owner_review=False,
    )
    return {
        "job_id": job_id,
        "live_status": str(item.get("live_status") or "unverified"),
        "fit_score": {"total": None, "recommendation": "rejected"},
        "route": {"route": "skipped", "application_url": application_url},
        "blockers": [],
        "warnings": [],
        "outputs": {},
        "stage": "rejected",
        "skip_reason": skip_reason,
    }


def _manual_review_for_insufficient_description(
    item: dict[str, Any],
    *,
    root: Path,
    actor: str,
) -> dict[str, Any]:
    """Persist an unscored vacancy for human review without aborting the scan.

    This path is intentionally narrow: it is used only when the central normalizer
    rejects a missing/short description.  It preserves source metadata and never
    invents requirements or a fit score.
    """
    _, paths = load_config(root)
    tracker = _load_tracker(paths)
    description = str(item.get("full_job_description") or item.get("job_description") or "").strip()
    if not description:
        description = "[Source provided no job description]"
    source_url = str(item.get("source_url") or "").strip()
    application_url = str(item.get("application_url") or "").strip()
    reason_code = "insufficient_job_description"
    ingest = tracker.ingest(
        {
            "source": str(item.get("source") or "manual"),
            "external_job_id": str(item.get("external_job_id") or item.get("reference") or ""),
            "source_url": source_url,
            "company": str(item.get("company") or "Unknown company"),
            "role": str(item.get("role") or "Unknown role"),
            "location": str(item.get("location") or ""),
            "posting_date": str(item.get("posting_date") or ""),
            "posting_date_precision": str(item.get("posting_date_precision") or ""),
            "posting_date_source": str(item.get("posting_date_source") or ""),
            "full_job_description": description,
            "fit_score": "",
            "priority": "unscored",
            "processing_status": MANUAL_REVIEW_STATUS,
            "next_action": "Review the official vacancy manually and obtain a complete job description before scoring",
            "notes": "Manual review required: source description is too short for evidence-based scoring.",
            "provenance": item.get("provenance") if isinstance(item.get("provenance"), dict) else {},
            "scoring": {
                "total": None,
                "recommendation": "unscored",
                "reason_code": reason_code,
                "reason": "The source job description is insufficient for evidence-based scoring.",
            },
            "processing_state": {
                "owner": "owner",
                "status": MANUAL_REVIEW_STATUS,
                "reason_code": reason_code,
                "reason": "The source job description is insufficient for evidence-based scoring.",
                "source_url": source_url,
                "application_url": application_url,
                "external_action_allowed": False,
                "send_or_submit": False,
            },
        },
        comment="Retained vacancy for manual review because the source description is insufficient for evidence-based scoring",
        actor=actor,
        source_refs=[value for value in (source_url, application_url) if value],
        confidence="high",
    )
    job_id = ingest["job_id"]
    # Duplicate ingest preserves the previous workflow state by design, so make
    # the manual-review classification explicit on both new and existing rows.
    tracker.update_job(
        job_id,
        {
            "fit_score": "",
            "priority": "unscored",
            "processing_status": MANUAL_REVIEW_STATUS,
            "next_action": "Review the official vacancy manually and obtain a complete job description before scoring",
            "scoring": {
                "total": None,
                "recommendation": "unscored",
                "reason_code": reason_code,
                "reason": "The source job description is insufficient for evidence-based scoring.",
            },
            "processing_state": {
                "owner": "owner",
                "status": MANUAL_REVIEW_STATUS,
                "reason_code": reason_code,
                "reason": "The source job description is insufficient for evidence-based scoring.",
                "source_url": source_url,
                "application_url": application_url,
                "external_action_allowed": False,
                "send_or_submit": False,
            },
        },
        comment="Classified insufficient-description vacancy as Manual Review Needed without assigning a fit score",
        actor=actor,
        action="updated",
        source_refs=[value for value in (source_url, application_url) if value],
        requires_owner_review=True,
    )
    return {
        "job_id": job_id,
        "live_status": str(item.get("live_status") or "unverified"),
        "fit_score": {"total": None, "recommendation": "unscored"},
        "route": {"route": "manual_review", "application_url": application_url},
        "blockers": [reason_code],
        "warnings": [],
        "outputs": {},
        "stage": MANUAL_REVIEW_STATUS,
        "manual_review_reason": reason_code,
    }


def run_scan(path: Path, *, root: Path, scanner_id: str) -> dict[str, Any]:
    if scanner_id not in SCANNER_ACTORS:
        raise ValueError(f"Unsupported scanner_id: {scanner_id}")
    actor = SCANNER_ACTORS[scanner_id]
    bundle = load_bundle(root)
    config = bundle["config"]["daily_scanner"]
    minimum = int(config["minimum_score_for_generation"])
    maximum = int(config["maximum_generation_packets_per_scan"])
    jobs, declared_paths = load_scan_input(path)
    _, paths = load_config(root)
    jobs_dir = paths.tracker_base / "data" / "jobs"
    known_job_ids = {item.stem for item in jobs_dir.glob("*.json")} if jobs_dir.is_dir() else set()
    report: dict[str, Any] = {
        "schema_version": 1,
        "scanned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bundle_hash": bundle["bundle_hash"],
        "scanner_id": scanner_id,
        "actor": actor,
        "source_file": str(path),
        "results": [],
        "generation_candidates": [],
        "manual_review_needed": [],
        "weak_or_blocked": [],
        "statistics": {
            "jobs_discovered": len(jobs),
            "jobs_ingested": 0,
            "new_jobs": 0,
            "existing_jobs": 0,
            "generation_candidates": 0,
            "manual_review_needed": 0,
            "weak_or_blocked": 0,
            "paths_total": 0,
            "paths_scanned": 0,
            "paths_failed": 0,
            "by_path": {},
        },
        "send_or_submit": False,
    }
    if declared_paths:
        add_path_scan_statistics(report, declared_paths)

    for item in jobs:
        item.setdefault("source", scanner_id)
        item["scanner_id"] = scanner_id
        logical_path = _path_name(item, scanner_id)
        path_stats = _path_entry(report["statistics"], logical_path)
        path_stats["jobs_discovered"] = max(
            path_stats["jobs_discovered"], path_stats["jobs_ingested"] + 1
        )
        skip_reason = auto_skip_title_reason(str(item.get("role") or ""), bundle.get("taxonomy", {}))
        if skip_reason:
            state = _reject_non_target_title(item, root=root, actor=actor, skip_reason=skip_reason)
        else:
            try:
                state = prepare_job(item, root=root, actor=actor)
            except ValueError as exc:
                if str(exc) != INSUFFICIENT_DESCRIPTION_MESSAGE:
                    raise
                state = _manual_review_for_insufficient_description(item, root=root, actor=actor)
        is_new = state["job_id"] not in known_job_ids
        known_job_ids.add(state["job_id"])
        fit = state.get("fit_score") or {}
        summary = {
            "job_id": state["job_id"],
            "live_status": state.get("live_status", "unverified"),
            "fit_score": fit.get("total"),
            "recommendation": fit.get("recommendation", "unscored"),
            "route": state.get("route", {}),
            "blockers": state.get("blockers", []),
            "warnings": state.get("warnings", []),
            "generation_packet": state.get("outputs", {}).get("generation_packet", ""),
            "processing_status": state.get("stage", ""),
            "manual_review_reason": state.get("manual_review_reason", ""),
            "skip_reason": state.get("skip_reason", ""),
            "source_path": logical_path,
            "is_new": is_new,
        }
        report["results"].append(summary)
        report["statistics"]["jobs_ingested"] += 1
        path_stats["jobs_ingested"] += 1
        if is_new:
            report["statistics"]["new_jobs"] += 1
            path_stats["new_jobs"] += 1
        else:
            report["statistics"]["existing_jobs"] += 1
            path_stats["existing_jobs"] += 1
        if summary["processing_status"] == MANUAL_REVIEW_STATUS:
            report["manual_review_needed"].append(summary)
            report["statistics"]["manual_review_needed"] += 1
            path_stats["blocked_or_below_threshold"] += 1
        elif summary["fit_score"] is not None and summary["fit_score"] >= minimum and not summary["blockers"]:
            summary["over_packet_cap"] = len(report["generation_candidates"]) >= maximum
            report["generation_candidates"].append(summary)
            report["statistics"]["generation_candidates"] += 1
            path_stats["generation_candidates"] += 1
        else:
            report["weak_or_blocked"].append(summary)
            report["statistics"]["weak_or_blocked"] += 1
            path_stats["blocked_or_below_threshold"] += 1

    _refresh_path_totals(report["statistics"])
    reconcile_after_scan(root, report)
    return report


def write_report(report: dict[str, Any], output: Path | None = None) -> str:
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    return text