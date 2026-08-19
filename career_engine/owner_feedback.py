from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config
from .pipeline import _load_tracker


IRRELEVANT_EVENT = "role_marked_irrelevant"
IRRELEVANT_RETRACTION_EVENT = "role_irrelevant_retracted"
DEFAULT_SITE_SLUG = "gilded-timber-xfj7"
MIN_NEGATIVE_SAMPLES = 2
MAX_OWNER_FEEDBACK_PENALTY = 10


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _title_key(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _load_api_key() -> str:
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


def _site_slug(repo: Path) -> str:
    path = repo / "dashboard/career-review/.deploy.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    return str(payload.get("slug") or DEFAULT_SITE_SLUG)


def _site_records(repo: Path, collection: str, limit: int = 1000) -> list[dict[str, Any]]:
    slug = _site_slug(repo)
    url = f"https://here.now/api/v1/publishes/{slug}/data/{urllib.parse.quote(collection)}?limit={max(1, min(limit, 1000))}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {_load_api_key()}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-800:]
        raise RuntimeError(f"here.now HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"here.now request failed: {exc.reason}") from exc
    return list(payload.get("records") or [])


def _data(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("data")
    return value if isinstance(value, dict) else record


def _event_time(record: dict[str, Any]) -> str:
    data = _data(record)
    return str(record.get("createdAt") or record.get("updatedAt") or data.get("created_at") or "")


def _job_id_from_role_key(value: Any) -> str:
    role_key = str(value or "")
    candidate = role_key[len("tracker-"):] if role_key.startswith("tracker-") else ""
    return candidate if re.fullmatch(r"[0-9a-f]{20}", candidate) else ""


def _resolve_job_id(tracker: Any, data: dict[str, Any]) -> str:
    direct = str(data.get("job_id") or "").strip()
    if re.fullmatch(r"[0-9a-f]{20}", direct):
        try:
            tracker.get_job(direct)
            return direct
        except KeyError:
            pass
    direct = _job_id_from_role_key(data.get("role_key"))
    if direct:
        try:
            tracker.get_job(direct)
            return direct
        except KeyError:
            pass
    company = _title_key(data.get("company"))
    role = _title_key(data.get("role"))
    if not company or not role:
        return ""
    matches: list[str] = []
    for row in tracker.list_rows():
        if _norm(row.get("processing_status")) == "superseded":
            continue
        if _title_key(row.get("company")) == company and _title_key(row.get("role")) == role:
            matches.append(row["job_id"])
    return matches[0] if len(matches) == 1 else ""


def _has_generated_package(paths: Any, job_id: str) -> bool:
    artifact = paths.tracker_base / "artifacts" / job_id
    if not artifact.is_dir():
        return False
    for path in artifact.iterdir():
        name = path.name.lower()
        if path.is_file() and ("cv" in name or "resume" in name) and (name.endswith(".pdf") or name.endswith(".docx")):
            return True
    return False


def _apply_irrelevant_event(tracker: Any, paths: Any, job_id: str, record_id: str, data: dict[str, Any]) -> bool:
    record = tracker.get_job(job_id)
    job = record.get("job") or {}
    state = dict(record.get("processing_state") or {})
    if state.get("owner_relevance_event_id") == record_id and _norm(job.get("outcome")) == "irrelevant":
        return False
    previous = str(state.get("pre_irrelevant_processing_status") or job.get("processing_status") or "ingested")
    if _norm(job.get("outcome")) != "irrelevant" and _norm(job.get("processing_status")) not in {"inactive", "superseded", "applied"}:
        previous = str(job.get("processing_status") or "ingested")
    state.update({
        "status": "inactive",
        "owner_relevance": "irrelevant",
        "owner_relevance_event_id": record_id,
        "owner_relevance_recorded_at": _event_time(data) or utc_now(),
        "pre_irrelevant_processing_status": previous,
        "external_action_allowed": False,
        "send_or_submit": False,
    })
    tracker.update_job(
        job_id,
        {
            "processing_status": "inactive",
            "outcome": "irrelevant",
            "next_action": "Owner marked this role Irrelevant; exclude from generation and retain as negative fit-calibration evidence.",
            "processing_state": state,
        },
        comment="Owner relevance feedback reconciled from private dashboard: role marked Irrelevant and retained for scoring calibration.",
        actor="system",
        action="reviewed",
        confidence="high",
        requires_owner_review=False,
    )
    return True


def _apply_retraction_event(tracker: Any, paths: Any, job_id: str, record_id: str, data: dict[str, Any]) -> bool:
    record = tracker.get_job(job_id)
    job = record.get("job") or {}
    state = dict(record.get("processing_state") or {})
    if state.get("owner_relevance_event_id") == record_id and _norm(job.get("outcome")) != "irrelevant":
        return False
    restore = str(state.get("pre_irrelevant_processing_status") or "").strip()
    if not restore or _norm(restore) in {"inactive", "irrelevant", "superseded", "applied"}:
        restore = "awaiting_owner_approval" if _has_generated_package(paths, job_id) else "ingested"
    state.update({
        "status": restore,
        "owner_relevance": "",
        "owner_relevance_event_id": record_id,
        "owner_relevance_recorded_at": _event_time(data) or utc_now(),
        "external_action_allowed": False,
        "send_or_submit": False,
    })
    tracker.update_job(
        job_id,
        {
            "processing_status": restore,
            "outcome": "",
            "next_action": "Review the vacancy and continue the normal Career Engine workflow.",
            "processing_state": state,
        },
        comment="Owner relevance feedback reconciled from private dashboard: Irrelevant label retracted and job restored to the prior internal workflow.",
        actor="system",
        action="reviewed",
        confidence="high",
        requires_owner_review=False,
    )
    return True


def reconcile_irrelevant_feedback(root: Path) -> dict[str, Any]:
    _, paths = load_config(root)
    tracker = _load_tracker(paths)
    records = sorted(_site_records(paths.repo_root, "history", 1000), key=_event_time)
    relevant = []
    changed: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    for record in records:
        data = _data(record)
        event = _norm(data.get("event"))
        if event not in {IRRELEVANT_EVENT, IRRELEVANT_RETRACTION_EVENT}:
            continue
        relevant.append(record)
        job_id = _resolve_job_id(tracker, data)
        if not job_id:
            unresolved.append({"role_key": str(data.get("role_key") or ""), "event": event})
            continue
        record_id = str(record.get("id") or data.get("id") or f"{event}:{_event_time(record)}:{job_id}")
        if event == IRRELEVANT_EVENT:
            mutated = _apply_irrelevant_event(tracker, paths, job_id, record_id, record)
        else:
            mutated = _apply_retraction_event(tracker, paths, job_id, record_id, record)
        if mutated:
            changed.append({"job_id": job_id, "event": event, "record_id": record_id})
    return {
        "schema_version": 1,
        "events_seen": len(relevant),
        "changed": changed,
        "unresolved": unresolved,
        "send_or_submit": False,
    }


def build_owner_feedback_calibration(root: Path) -> dict[str, Any]:
    config, paths = load_config(root)
    tracker = _load_tracker(paths)
    negative = Counter()
    positive = Counter()
    examples: dict[str, list[str]] = {}
    for row in tracker.list_rows():
        title = _title_key(row.get("role"))
        if not title:
            continue
        outcome = _norm(row.get("outcome"))
        application = _norm(row.get("application_status"))
        processing = _norm(row.get("processing_status"))
        if outcome == "irrelevant":
            negative[title] += 1
            examples.setdefault(title, []).append(row["job_id"])
        if processing == "applied" or application in {"applied", "submitted", "sent"}:
            positive[title] += 1
    patterns: dict[str, dict[str, Any]] = {}
    for title, negatives in sorted(negative.items()):
        positives = positive.get(title, 0)
        active = negatives >= MIN_NEGATIVE_SAMPLES and positives == 0
        penalty = min(MAX_OWNER_FEEDBACK_PENALTY, 4 + 2 * negatives) if active else 0
        patterns[title] = {
            "negative_samples": negatives,
            "positive_samples": positives,
            "active": active,
            "penalty": penalty,
            "evidence_job_ids": examples.get(title, []),
            "activation_rule": f">={MIN_NEGATIVE_SAMPLES} owner-Irrelevant samples and zero applied samples",
        }
    result = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "mode": "bounded_exact_title_negative_feedback",
        "max_penalty": MAX_OWNER_FEEDBACK_PENALTY,
        "patterns": patterns,
        "active_patterns": sum(1 for item in patterns.values() if item.get("active")),
        "irrelevant_jobs": sum(negative.values()),
        "threshold": int(config["scoring"]["thresholds"]["high_priority"]),
    }
    target = paths.tracker_base / "runtime" / "owner-fit-calibration.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    result["path"] = str(target)
    return result


def _recommendation(score: int, config: dict[str, Any]) -> str:
    thresholds = config["scoring"]["thresholds"]
    if score >= int(thresholds["high_priority"]):
        return "high_priority"
    if score >= int(thresholds["credible"]):
        return "credible"
    if score >= int(thresholds["selective"]):
        return "selective"
    return "weak"


def apply_owner_feedback_calibration(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    config, paths = load_config(root)
    tracker = _load_tracker(paths)
    calibration = build_owner_feedback_calibration(root)
    patterns = calibration.get("patterns") or {}
    threshold = int(config["scoring"]["thresholds"]["high_priority"])
    adjusted: list[dict[str, Any]] = []

    for summary in report.get("results", []):
        job_id = str(summary.get("job_id") or "")
        if not job_id:
            continue
        record = tracker.get_job(job_id)
        job = record.get("job") or {}
        if _norm(job.get("outcome")) == "irrelevant":
            continue
        if _norm(job.get("processing_status")) in {"applied", "superseded", "rejected"} or _norm(job.get("application_status")) in {"applied", "submitted", "sent"}:
            continue
        scoring = dict(record.get("scoring") or {})
        if scoring.get("human_override"):
            continue
        title = _title_key(job.get("role"))
        rule = patterns.get(title) or {}
        if not rule.get("active"):
            continue
        raw_value = scoring.get("raw_total")
        if raw_value in (None, ""):
            raw_value = scoring.get("total", job.get("fit_score"))
        try:
            raw_score = int(float(raw_value))
        except (TypeError, ValueError):
            continue
        penalty = int(rule.get("penalty") or 0)
        calibrated_score = max(0, raw_score - penalty)
        recommendation = _recommendation(calibrated_score, config)
        scoring["raw_total"] = raw_score
        scoring["total"] = calibrated_score
        scoring["recommendation"] = recommendation
        scoring["owner_feedback_calibration"] = {
            "title_key": title,
            "penalty": penalty,
            "negative_samples": int(rule.get("negative_samples") or 0),
            "positive_samples": int(rule.get("positive_samples") or 0),
            "evidence_job_ids": list(rule.get("evidence_job_ids") or []),
            "applied_at": utc_now(),
        }
        changes: dict[str, Any] = {
            "fit_score": calibrated_score,
            "priority": recommendation,
            "scoring": scoring,
        }
        target_status = str(job.get("processing_status") or "")
        packet_removed = False
        if calibrated_score < threshold and _norm(target_status) == "generation_ready":
            target_status = "selective" if calibrated_score >= int(config["scoring"]["thresholds"]["selective"]) else "blocked"
            state = dict(record.get("processing_state") or {})
            blockers = [str(value) for value in state.get("blockers", []) if not str(value).startswith("owner_feedback_calibration:")]
            blockers.append(f"owner_feedback_calibration:{title}")
            state.update({"status": target_status, "blockers": blockers, "external_action_allowed": False})
            changes.update({
                "processing_status": target_status,
                "next_action": "Owner feedback calibration moved this role below the generation threshold; retain for review only.",
                "processing_state": state,
            })
            packet = paths.tracker_base / "artifacts" / job_id / "generation_packet.json"
            if packet.is_file():
                packet.unlink()
                packet_removed = True
        tracker.update_job(
            job_id,
            changes,
            comment=f"Daily owner-feedback calibration applied a bounded {penalty}-point penalty from repeated Irrelevant labels for exact role title '{job.get('role', '')}'. Raw score preserved.",
            actor="system",
            action="reviewed",
            confidence="high",
            requires_owner_review=False,
        )
        summary["fit_score"] = calibrated_score
        summary["recommendation"] = recommendation
        summary["processing_status"] = target_status
        summary["owner_feedback_penalty"] = penalty
        adjusted.append({
            "job_id": job_id,
            "role": job.get("role", ""),
            "raw_score": raw_score,
            "calibrated_score": calibrated_score,
            "penalty": penalty,
            "processing_status": target_status,
            "generation_packet_removed": packet_removed,
        })

    if adjusted:
        adjusted_ids = {item["job_id"] for item in adjusted}
        report["generation_candidates"] = [item for item in report.get("generation_candidates", []) if item.get("job_id") not in adjusted_ids or (item.get("fit_score") is not None and int(item.get("fit_score")) >= threshold)]
        weak_ids = {item.get("job_id") for item in report.get("weak_or_blocked", [])}
        for summary in report.get("results", []):
            if summary.get("job_id") in adjusted_ids and int(summary.get("fit_score") or 0) < threshold and summary.get("job_id") not in weak_ids:
                report.setdefault("weak_or_blocked", []).append(summary)
        stats = report.get("statistics") or {}
        stats["generation_candidates"] = len(report.get("generation_candidates", []))
        stats["weak_or_blocked"] = len(report.get("weak_or_blocked", []))
    return {**calibration, "adjusted_jobs": adjusted, "adjusted_count": len(adjusted)}
