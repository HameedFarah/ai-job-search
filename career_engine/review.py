from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from .config import load_config

VERDICTS = {"accepted", "corrected", "rejected", "mixed"}
JOB_VERDICTS = {"accepted", "corrected", "rejected"}
AREAS = {
    "source_coverage", "job_data", "scoring", "evidence", "resume_content",
    "rendering", "selected_cv", "email_draft", "workflow", "other",
}


def _tracker(paths: Any) -> Any:
    # Implementation from the executing repository's clean tracked source; state
    # instantiated at the canonical live tracker base. Mirrors pipeline._load_tracker.
    source_dir = getattr(paths, "tracker_source_path", None) or Path(paths.tracker_base)
    tracker_path = Path(source_dir) / "tracker.py"
    spec = importlib.util.spec_from_file_location("career_engine_tracker_review", tracker_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load tracker: {tracker_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CareerTracker(paths.tracker_base)


def validate_review_diff(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "review_id", "reviewed_at", "hermes_run_id", "reviewer",
        "verdict", "job_diffs", "improvement_rules", "send_or_submit",
    }
    missing = sorted(required - set(payload))
    if missing:
        errors.append(f"Missing fields: {', '.join(missing)}")
        return errors
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if payload.get("reviewer") != "chatgpt":
        errors.append("reviewer must be chatgpt")
    if payload.get("verdict") not in VERDICTS:
        errors.append(f"verdict must be one of {sorted(VERDICTS)}")
    if payload.get("send_or_submit") is not False:
        errors.append("send_or_submit must be false")
    if not str(payload.get("review_id", "")).strip():
        errors.append("review_id is required")
    if not str(payload.get("reviewed_at", "")).strip():
        errors.append("reviewed_at is required")
    if not str(payload.get("hermes_run_id", "")).strip():
        errors.append("hermes_run_id is required")
    rules = payload.get("improvement_rules")
    if not isinstance(rules, list) or any(not str(item).strip() for item in rules):
        errors.append("improvement_rules must be an array of non-empty strings")
    job_diffs = payload.get("job_diffs")
    if not isinstance(job_diffs, list):
        errors.append("job_diffs must be an array")
        return errors
    for job_index, job in enumerate(job_diffs):
        if not isinstance(job, dict):
            errors.append(f"job_diffs[{job_index}] must be an object")
            continue
        job_id = str(job.get("job_id", "")).strip()
        if len(job_id) < 8:
            errors.append(f"job_diffs[{job_index}].job_id is invalid")
        if job.get("verdict") not in JOB_VERDICTS:
            errors.append(f"job_diffs[{job_index}].verdict must be one of {sorted(JOB_VERDICTS)}")
        differences = job.get("differences")
        if not isinstance(differences, list):
            errors.append(f"job_diffs[{job_index}].differences must be an array")
            continue
        for diff_index, difference in enumerate(differences):
            location = f"job_diffs[{job_index}].differences[{diff_index}]"
            if not isinstance(difference, dict):
                errors.append(f"{location} must be an object")
                continue
            if difference.get("area") not in AREAS:
                errors.append(f"{location}.area must be one of {sorted(AREAS)}")
            if not str(difference.get("reason", "")).strip():
                errors.append(f"{location}.reason is required")
            if not isinstance(difference.get("evidence_refs"), list):
                errors.append(f"{location}.evidence_refs must be an array")
            if "before" not in difference or "after" not in difference:
                errors.append(f"{location} must contain before and after")
    return errors


def record_review_diff(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    errors = validate_review_diff(payload)
    if errors:
        return {"valid": False, "errors": errors, "saved_to": "", "events": []}
    _, paths = load_config(root)
    review_dir = paths.tracker_base / "runtime/review-diffs"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_id = str(payload["review_id"]).strip()
    destination = review_dir / f"{review_id}.json"
    temp = destination.with_suffix(".json.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, destination)
    latest = review_dir / "latest.json"
    latest_temp = latest.with_suffix(".json.tmp")
    latest_temp.write_text(text, encoding="utf-8")
    os.replace(latest_temp, latest)

    tracker = _tracker(paths)
    events: list[dict[str, Any]] = []
    for job in payload.get("job_diffs", []):
        job_id = str(job["job_id"])
        differences = list(job.get("differences", []))
        reusable = [item.get("reusable_rule", "") for item in differences if str(item.get("reusable_rule", "")).strip()]
        event = tracker.record_event(
            actor="chatgpt",
            entity_type="job",
            entity_id=job_id,
            action="reviewed",
            before={"hermes_run_id": payload["hermes_run_id"], "differences": [item.get("before") for item in differences]},
            after={
                "review_id": review_id,
                "verdict": job["verdict"],
                "differences": [item.get("after") for item in differences],
                "reusable_rules": reusable,
            },
            comment=f"ChatGPT reviewed Hermes Career Engine output: {job['verdict']}",
            source_refs=[str(destination), *[ref for item in differences for ref in item.get("evidence_refs", [])]],
            confidence="high",
            requires_owner_review=job["verdict"] != "accepted",
        )
        events.append(event)
    return {
        "valid": True,
        "errors": [],
        "saved_to": str(destination),
        "latest": str(latest),
        "job_events": len(events),
        "events": events,
        "send_or_submit": False,
    }
