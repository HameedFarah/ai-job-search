from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import load_bundle
from .service import prepare_job

SCANNER_ACTORS = {
    "hermes_scanner": "hermes",
    "chatgpt_scanner": "chatgpt",
}


def load_jobs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        jobs = payload
    elif isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        jobs = payload["jobs"]
    else:
        raise ValueError("Scanner input must be a JSON array or an object containing jobs[]")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(jobs):
        if not isinstance(item, dict):
            raise ValueError(f"jobs[{index}] must be an object")
        result.append(dict(item))
    return result


def run_scan(path: Path, *, root: Path, scanner_id: str) -> dict[str, Any]:
    if scanner_id not in SCANNER_ACTORS:
        raise ValueError(f"Unsupported scanner_id: {scanner_id}")
    actor = SCANNER_ACTORS[scanner_id]
    bundle = load_bundle(root)
    config = bundle["config"]["daily_scanner"]
    minimum = int(config["minimum_score_for_generation"])
    maximum = int(config["maximum_generation_packets_per_scan"])
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
        "send_or_submit": False,
    }
    for item in load_jobs(path):
        item.setdefault("source", scanner_id)
        item["scanner_id"] = scanner_id
        state = prepare_job(item, root=root, actor=actor)
        summary = {
            "job_id": state["job_id"],
            "live_status": state["live_status"],
            "fit_score": state["fit_score"]["total"],
            "recommendation": state["fit_score"]["recommendation"],
            "route": state["route"],
            "blockers": state["blockers"],
            "generation_packet": state["outputs"].get("generation_packet", ""),
        }
        report["results"].append(summary)
        if summary["fit_score"] >= minimum and not summary["blockers"] and len(report["generation_candidates"]) < maximum:
            report["generation_candidates"].append(summary)
        else:
            report["weak_or_blocked"].append(summary)
    return report


def write_report(report: dict[str, Any], output: Path | None = None) -> str:
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    return text
