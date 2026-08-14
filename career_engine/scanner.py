from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import load_bundle
from .config import load_config
from .service import prepare_job

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
        "weak_or_blocked": [],
        "statistics": {
            "jobs_discovered": len(jobs),
            "jobs_ingested": 0,
            "new_jobs": 0,
            "existing_jobs": 0,
            "generation_candidates": 0,
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
        state = prepare_job(item, root=root, actor=actor)
        is_new = state["job_id"] not in known_job_ids
        known_job_ids.add(state["job_id"])
        summary = {
            "job_id": state["job_id"],
            "live_status": state["live_status"],
            "fit_score": state["fit_score"]["total"],
            "recommendation": state["fit_score"]["recommendation"],
            "route": state["route"],
            "blockers": state["blockers"],
            "warnings": state.get("warnings", []),
            "generation_packet": state["outputs"].get("generation_packet", ""),
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
        if summary["fit_score"] >= minimum and not summary["blockers"] and len(report["generation_candidates"]) < maximum:
            report["generation_candidates"].append(summary)
            report["statistics"]["generation_candidates"] += 1
            path_stats["generation_candidates"] += 1
        else:
            report["weak_or_blocked"].append(summary)
            report["statistics"]["weak_or_blocked"] += 1
            path_stats["blocked_or_below_threshold"] += 1

    _refresh_path_totals(report["statistics"])
    return report


def write_report(report: dict[str, Any], output: Path | None = None) -> str:
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    return text
