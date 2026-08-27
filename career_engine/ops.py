"""Career Engine operational commands.

One coherent operational module behind the native CLI operations that ChatGPT
and Hermes need to execute the complete Career Engine workflow directly:

- ``validate_config``: central config, required files, bundle currency/validity,
  tracker schema. Nonzero exit on errors.
- ``list_jobs``: read-only summary with score/status/company/role filters.
- ``show_job``: read-only canonical job detail.
- ``dashboard``: read-only dashboard status; ``sync`` writes the local
  dashboard data export. Never deploys to an external target.
- ``review_summary``: read-only review diff summary. Never changes owner
  decisions.
- ``reconcile``: idempotent tracker reconciliation against the canonical
  generation threshold and persisted owner decisions (append-only events).
- ``run``: deterministic batch orchestration. Rebuilds/validates the bundle,
  reconciles tracker statuses, prepares/generates only eligible records through
  the configured no-send pipeline, syncs the local dashboard data and emits a
  structured report. Never sends or submits.
- ``validate_all``: per-job validation, or aggregate config/bundle/tracker/all
  generated eligible jobs when no job id is supplied.
- ``record_review``: retains ``--file``; defaults to
  ``runtime/review-diffs/latest.json`` when it validates.

Authority precedence (see central-rules.README.md and AGENTS.md):
central config -> runtime bundle -> central rules -> Vault operations contract
-> persisted owner decisions (append-only) -> dashboard overrides -> review
diffs as lessons only. Persisted owner decisions below are append-only and
override any earlier reviewer interpretation.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle import build_bundle, bundle_status, load_bundle
from .config import load_config, validate_required_files
from .generation import validate_generated_application
from .pipeline import _load_tracker, prepare
from .review import record_review_diff, validate_review_diff
from .scanner import run_scan, write_report

# ---------------------------------------------------------------------------
# Persisted owner decisions (append-only authority, effective 2026-08-06).
# These come from the owner-approved handoff and central-rules.README.md and
# supersede any earlier reviewer interpretation.
# ---------------------------------------------------------------------------

FADEN_JOB_ID = "4006ecf27038992c28d4"
LUXOFT_JOB_ID = "910539803b3d337fb4a5"
ZAWAYA_APPLIED_JOB_ID = "4ec93d69301ada8bcc85"
ZAWAYA_DUPLICATE_JOB_ID = "8e8c0a3a72429fa51ee1"
PARSONS_SDM_APPROVED_JOB_ID = "fa42cc413b1abb52bf55"

FADEN_DECISION = {
    "job_id": FADEN_JOB_ID,
    "decision": "accepted",
    "reason": (
        "Owner accepted FADEN CONTRACTING LTD / Architecture Project Manager at raw score 78 "
        "under the centralized 70/100 generation threshold. Route is portal-only with the "
        "official Gotogulf application URL; ATS Linear selected as the single CV; no Gmail "
        "draft and no auto-submit under any circumstance."
    ),
}

LUXOFT_DECISION = {
    "job_id": LUXOFT_JOB_ID,
    "decision": "rejected",
    "reason": (
        "Owner rejected Luxoft Project Manager/Scrum Master despite raw score 72: the role is "
        "outside the target architecture/design-management lane and must never generate or be "
        "submitted. This persisted owner decision supersedes the score alone."
    ),
}

OWNER_DECISIONS = [FADEN_DECISION, LUXOFT_DECISION]

# Roles named in the handoff as currently generation-ready. They are checked,
# never forced: if canonical data proves a different current state, the exact
# result and reason are reported without mutation.
NAMED_GENERATION_READY_ROLES = [
    (FADEN_JOB_ID, "FADEN CONTRACTING LTD", "Architecture Project Manager"),
    ("fd6675da1bb6de6f40a1", "Parsons", "Senior Project Manager (Design)"),
    ("d40d3f3f10bb470875a0", "Parsons", "Project Manager (Infrastructure Design)"),
    ("48166c0b35b4d62b8489", "AECOM", "Senior Project Manager"),
    ("4509528885e013220587", "Turner & Townsend", "MEP Project Manager"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_tracker_ops(root: Path | None = None) -> Any:
    _, paths = load_config(root)
    return _load_tracker(paths)


def _effective_score(record: dict[str, Any], row_score: str = "") -> int:
    scoring = record.get("scoring") or {}
    value = scoring.get("total")
    # Legacy/superseded Site Data stubs may carry an empty-string score.
    # Treat blank/non-numeric values as unrated rather than aborting the run.
    try:
        return int(float(value if value not in (None, "") else row_score))
    except (TypeError, ValueError):
        return 0


def _artifact_dir(root: Path | None, job_id: str) -> Path:
    _, paths = load_config(root)
    return paths.tracker_base / "artifacts" / job_id


def _set_pipeline_stage(root: Path | None, job_id: str, stage: str) -> None:
    path = _artifact_dir(root, job_id) / "pipeline_state.json"
    if not path.is_file():
        return
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    data = dict(wrapper.get("data", {}))
    data["stage"] = stage
    data["reconciled_at"] = utc_now()
    wrapper["data"] = data
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _set_pipeline_blockers(
    root: Path | None,
    job_id: str,
    blockers: list[str],
    *,
    stage: str = "",
) -> bool:
    """Synchronize the current pipeline-state mirror without rewriting history."""
    path = _artifact_dir(root, job_id) / "pipeline_state.json"
    if not path.is_file():
        return False
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    data = dict(wrapper.get("data", {}))
    before = list(data.get("blockers") or [])
    changed = before != blockers
    if stage and data.get("stage") != stage:
        data["stage"] = stage
        changed = True
    if not changed:
        return False
    data["blockers"] = blockers
    data["reconciled_at"] = utc_now()
    wrapper["data"] = data
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return True


def _remove_generation_packet(root: Path | None, job_id: str) -> bool:
    removed = False
    for name in ("generation_packet.json", "generation_packet.stage.json"):
        path = _artifact_dir(root, job_id) / name
        if path.is_file():
            path.unlink()
            removed = True
    return removed


def _update_processing_state(
    tracker: Any,
    job_id: str,
    fields: dict[str, Any],
    *,
    comment: str,
    actor: str,
    action: str = "updated",
    requires_owner_review: bool = False,
) -> None:
    record = tracker.get_job(job_id)
    processing_state = dict(record.get("processing_state") or {})
    processing_state.update(fields)
    tracker.update_job(
        job_id,
        {"processing_state": processing_state},
        comment=comment,
        actor=actor,
        action=action,
        requires_owner_review=requires_owner_review,
    )


# ---------------------------------------------------------------------------
# validate-config
# ---------------------------------------------------------------------------

def _tracker_issues(root: Path | None) -> list[str]:
    """Bounded tracker schema checks: CSV header, event sample, per-job JSON."""
    issues: list[str] = []
    tracker = _load_tracker_ops(root)
    try:
        rows = tracker.list_rows()
    except ValueError as exc:
        issues.append(f"jobs.csv schema: {exc}")
        rows = []
    loaded = 0
    for row in rows:
        job_id = row.get("job_id", "")
        if not job_id:
            issues.append("jobs.csv contains a row without job_id")
            continue
        path = tracker.base_dir / "data/jobs" / f"{job_id}.json"
        if not path.is_file():
            issues.append(f"missing per-job JSON for {job_id}")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            loaded += 1
        except (json.JSONDecodeError, OSError) as exc:
            issues.append(f"invalid per-job JSON {job_id}: {exc}")
    events_path = tracker.base_dir / "logs/events.jsonl"
    if not events_path.is_file():
        issues.append("missing logs/events.jsonl")
    else:
        expected_event_fields = [
            "event_id", "timestamp", "actor", "entity_type", "entity_id", "action",
            "before", "after", "comment", "source_refs", "confidence",
            "requires_owner_review",
        ]
        for line_number, raw in enumerate(events_path.read_text(encoding="utf-8").splitlines()[:200], start=1):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
                if list(event.keys()) != expected_event_fields:
                    issues.append(f"events.jsonl line {line_number} has a non-canonical event schema")
                    break
            except json.JSONDecodeError as exc:
                issues.append(f"events.jsonl line {line_number} is not valid JSON: {exc}")
                break
    return issues


def validate_config(root: Path | None = None) -> dict[str, Any]:
    """Validate central config, required files, bundle currency/validity and the
    tracker schema. ``valid`` is False on any error."""
    issues: list[str] = []
    bundle: dict[str, Any] = {"valid": False, "current": False}
    try:
        config, paths = load_config(root)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        return {"valid": False, "issues": [f"central config: {exc}"], "bundle": bundle, "tracker": {}}
    missing = validate_required_files(config, paths, require_vault=True)
    if missing:
        issues.append(f"required files missing: {', '.join(missing)}")
    bundle = bundle_status(root)
    if not bundle.get("valid"):
        issues.append("runtime bundle is not valid")
    if not bundle.get("current"):
        issues.append("runtime bundle is stale (sources changed since build)")
    tracker = {"rows": 0, "issues": []}
    try:
        tracker_issues = _tracker_issues(root)
        tracker = {"rows": len(_load_tracker_ops(root).list_rows()), "issues": tracker_issues}
        issues.extend(tracker_issues)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        issues.append(f"tracker check failed: {exc}")
    return {
        "valid": not issues,
        "issues": issues,
        "bundle": {key: bundle.get(key) for key in ("valid", "current", "bundle_hash", "source_hash", "expected_source_hash")},
        "tracker": tracker,
    }


# ---------------------------------------------------------------------------
# list-jobs / show-job (read-only)
# ---------------------------------------------------------------------------

def _row_summary(tracker: Any, row: dict[str, str]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    try:
        record = tracker.get_job(row["job_id"])
    except KeyError:
        pass
    processing_state = record.get("processing_state") or {}
    route = processing_state.get("route") or {}
    scoring = record.get("scoring") or {}
    return {
        "job_id": row["job_id"],
        "company": row["company"],
        "role": row["role"],
        "source": row["source"],
        "source_url": row["source_url"],
        "fit_score": row["fit_score"],
        "raw_total": scoring.get("raw_total", row["fit_score"]),
        "priority": row["priority"],
        "processing_status": row["processing_status"],
        "application_status": row["application_status"],
        "manual_review_reason": processing_state.get("reason_code", "") if row["processing_status"] == "manual_review_needed" else "",
        "manual_review_detail": processing_state.get("reason", "") if row["processing_status"] == "manual_review_needed" else "",
        "live_status": processing_state.get("live_status", ""),
        "route": route.get("route", ""),
        "application_url": route.get("application_url", ""),
        "selected_resume_variant": processing_state.get("selected_resume_variant", ""),
        "location": row["location"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "next_action": row["next_action"],
    }


def list_jobs(
    root: Path | None = None,
    *,
    status: str = "",
    min_score: int | None = None,
    max_score: int | None = None,
    company: str = "",
    role: str = "",
) -> dict[str, Any]:
    tracker = _load_tracker_ops(root)
    rows = tracker.list_rows()
    selected: list[dict[str, Any]] = []
    for row in rows:
        if status and row["processing_status"] != status:
            continue
        score = row["fit_score"]
        try:
            score_value = int(float(score))
        except (TypeError, ValueError):
            score_value = 0
        if min_score is not None and score_value < min_score:
            continue
        if max_score is not None and score_value > max_score:
            continue
        if company and company.lower() not in row["company"].lower():
            continue
        if role and role.lower() not in row["role"].lower():
            continue
        selected.append(_row_summary(tracker, row))
    counts = dict(Counter(item["processing_status"] for item in selected))
    return {
        "count": len(selected),
        "counts_by_status": counts,
        "jobs": selected,
    }


def show_job(job_id: str, root: Path | None = None) -> dict[str, Any]:
    tracker = _load_tracker_ops(root)
    record = tracker.get_job(job_id)
    artifact_dir = _artifact_dir(root, job_id)
    pipeline_path = artifact_dir / "pipeline_state.json"
    packet_path = artifact_dir / "generation_packet.json"
    application_path = artifact_dir / "generated_application.json"
    packet: dict[str, Any] = {}
    if packet_path.is_file():
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    artifacts = [
        item.get("type", str(item).split("/")[-1])
        for item in record.get("generated_artifacts", [])
        if isinstance(item, dict)
    ]
    return {
        "job_id": job_id,
        "job": record["job"],
        "scoring": record.get("scoring"),
        "processing_state": record.get("processing_state"),
        "pipeline_state": (
            json.loads(pipeline_path.read_text(encoding="utf-8"))["data"]
            if pipeline_path.is_file()
            else None
        ),
        "route": (record.get("processing_state") or {}).get("route"),
        "generation_packet": {
            "exists": packet_path.is_file(),
            "bundle_hash": packet.get("bundle_hash", ""),
            "application_route": packet.get("application_route"),
            "email_draft_policy": packet.get("email_draft_policy"),
            "outward_filename": packet.get("outward_filename", ""),
        },
        "generated_application_exists": application_path.is_file(),
        "artifacts": sorted(set(artifacts)),
        "history_entries": len(record.get("history", [])),
    }


# ---------------------------------------------------------------------------
# dashboard (read-only by default; --sync writes the local export only)
# ---------------------------------------------------------------------------

DASHBOARD_PUBLISHER = {
    "established_publisher": (
        "Static career-review site at dashboard/career-review "
        "(scripts/build_site.js builds from the canonical tracker; "
        "scripts/publish_here_now.js deploys the permanent here.now URL)."
    ),
    "live_deploy": "external consequential action requiring explicit owner approval; not performed from this workspace",
    "deployed": False,
}


def dashboard(root: Path | None = None, *, sync: bool = False) -> dict[str, Any]:
    config, paths = load_config(root)
    bundle = load_bundle(root)
    tracker = _load_tracker_ops(root)
    rows = tracker.list_rows()
    jobs: list[dict[str, Any]] = []
    for row in rows:
        summary = _row_summary(tracker, row)
        jobs.append(summary)
    counts = dict(Counter(item["processing_status"] for item in jobs))
    data = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "bundle_hash": bundle["bundle_hash"],
        "threshold": config["scoring"]["thresholds"]["high_priority"],
        "counts": counts,
        "jobs": jobs,
    }
    target = paths.tracker_base / "runtime/dashboard-data.json"
    if sync:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(target)
    return {
        "mode": "synced" if sync else "readonly",
        "path": str(target) if sync else "",
        "jobs": len(jobs),
        "counts": counts,
        "threshold": config["scoring"]["thresholds"]["high_priority"],
        "bundle_hash": bundle["bundle_hash"],
        "publisher": DASHBOARD_PUBLISHER,
    }


# ---------------------------------------------------------------------------
# review (read-only summary of the latest review diff)
# ---------------------------------------------------------------------------

def review_summary(root: Path | None = None) -> dict[str, Any]:
    _, paths = load_config(root)
    latest = paths.tracker_base / "runtime/review-diffs/latest.json"
    if not latest.is_file():
        return {"valid": False, "reason": "no review diff recorded yet", "path": str(latest)}
    payload = json.loads(latest.read_text(encoding="utf-8"))
    validation_errors = validate_review_diff(payload)
    return {
        "valid": not validation_errors,
        "validation_errors": validation_errors,
        "path": str(latest),
        "review_id": payload.get("review_id"),
        "reviewed_at": payload.get("reviewed_at"),
        "hermes_run_id": payload.get("hermes_run_id"),
        "verdict": payload.get("verdict"),
        "send_or_submit": payload.get("send_or_submit"),
        "improvement_rules": payload.get("improvement_rules", []),
        "job_diffs": [
            {
                "job_id": job.get("job_id"),
                "verdict": job.get("verdict"),
                "differences": [
                    {
                        "area": diff.get("area"),
                        "reason": diff.get("reason"),
                        "reusable_rule": diff.get("reusable_rule"),
                    }
                    for diff in job.get("differences", [])
                ],
            }
            for job in payload.get("job_diffs", [])
        ],
    }


# ---------------------------------------------------------------------------
# reconcile (idempotent, append-only events, no-send/no-submit)
# ---------------------------------------------------------------------------

def _below_threshold_status(score: int, config: dict[str, Any]) -> str:
    bands = config["scoring"]["thresholds"]
    if score >= bands["high_priority"]:
        return "generation_ready"
    if score >= bands["selective"]:
        return "selective"
    return "blocked"


def _has_owner_force(record: dict[str, Any], config: dict[str, Any]) -> bool:
    """A persisted explicit owner force/accept decision may keep a below-70 job
    eligible; it is recorded in scoring.human_override or processing_state."""
    threshold = int(config["scoring"]["thresholds"]["high_priority"])
    scoring = record.get("scoring") or {}
    override = scoring.get("human_override") or {}
    if isinstance(override, dict) and int(override.get("score", 0) or 0) >= threshold:
        return True
    processing_state = record.get("processing_state") or {}
    decision = processing_state.get("owner_decision") or {}
    if isinstance(decision, dict) and str(decision.get("decision", "")).strip() in {"accepted", "forced"}:
        return True
    return False


def _reconcile_submitted_lifecycle(
    tracker: Any,
    root: Path | None,
    *,
    report: list[dict[str, Any]],
    preserved: list[dict[str, Any]],
) -> None:
    """Keep submitted/sent applications terminal in the processing lifecycle.

    Application evidence is authoritative over a stale internal preparation
    status.  This repairs any record whose application_status already proves an
    outward application while preserving the immutable submission artifacts.
    """
    for row in tracker.list_rows():
        job_id = row["job_id"]
        record = tracker.get_job(job_id)
        job = record.get("job") or {}
        application_status = str(job.get("application_status") or "").strip().lower()
        if application_status not in {"submitted", "sent", "applied"}:
            continue
        processing_state = dict(record.get("processing_state") or {})
        processing_status = str(job.get("processing_status") or row.get("processing_status") or "").strip().lower()
        if processing_status == "applied" and str(processing_state.get("status") or "").strip().lower() == "applied":
            preserved.append({
                "job_id": job_id,
                "state": "applied",
                "preserved": True,
                "reason": "application_submitted",
            })
            continue

        previous_status = job.get("processing_status") or row.get("processing_status") or ""
        processing_state.update({
            "status": "applied",
            "external_action_allowed": False,
            "send_or_submit": False,
            "reconciled_reason": (
                f"application_status={application_status} proves the application is already outward; "
                "processing lifecycle repaired to applied"
            ),
        })
        tracker.update_job(
            job_id,
            {
                "processing_status": "applied",
                "next_action": "Application already submitted/sent; preserve the submitted package and monitor for outcome.",
                "processing_state": processing_state,
            },
            comment=(
                f"Reconciled submitted lifecycle: application_status={application_status}; "
                f"processing_status {previous_status or 'unknown'} -> applied."
            ),
            actor="system",
            action="updated",
            requires_owner_review=False,
        )
        _set_pipeline_stage(root, job_id, "applied")
        report.append({
            "job_id": job_id,
            "company": job.get("company", ""),
            "role": job.get("role", ""),
            "from": previous_status,
            "to": "applied",
            "reason": "application_submitted_lifecycle_repair",
            "application_status": application_status,
        })


def _apply_owner_decisions(
    tracker: Any,
    config: dict[str, Any],
    root: Path | None,
    *,
    report: list[dict[str, Any]],
    preserved: list[dict[str, Any]],
    notes: list[dict[str, Any]],
    threshold: int,
) -> None:
    for decision in OWNER_DECISIONS:
        job_id = decision["job_id"]
        try:
            record = tracker.get_job(job_id)
        except KeyError:
            notes.append({
                "job_id": job_id,
                "detail": "owner decision recorded but job_id not present in tracker; nothing to change",
                "decision": decision["decision"],
            })
            continue
        job = record["job"]
        processing_state = dict(record.get("processing_state") or {})
        if decision["decision"] == "rejected":
            already = (
                job["processing_status"] == "rejected"
                and processing_state.get("status") == "rejected"
                and any(
                    str(item).startswith("owner_rejected:")
                    for item in processing_state.get("blockers", [])
                )
            )
            if already:
                preserved.append({"job_id": job_id, "state": "rejected", "preserved": True})
                continue
            packet_removed = _remove_generation_packet(root, job_id)
            fields = {
                "status": "rejected",
                "blockers": ["owner_rejected:outside_target_lane"],
                "external_action_allowed": False,
                "owner_decision": {
                    "decision": "rejected",
                    "recorded_at": utc_now(),
                    "reason": decision["reason"],
                },
            }
            _update_processing_state(
                tracker,
                job_id,
                fields,
                comment=(
                    "Persisted owner decision: reject Luxoft Project Manager/Scrum Master despite "
                    "raw score 72 because the role is outside the target architecture/design-management lane."
                ),
                actor="owner",
                action="rejected",
            )
            tracker.update_job(
                job_id,
                {
                    "processing_status": "rejected",
                    "next_action": "Owner rejected this role as outside the target lane; no generation, no submission, no draft.",
                },
                comment="Owner decision recorded: Luxoft Scrum Master rejected despite score 72 (outside target lane).",
                actor="owner",
                action="rejected",
                requires_owner_review=False,
            )
            _set_pipeline_stage(root, job_id, "rejected")
            report.append({
                "job_id": job_id,
                "company": job["company"],
                "role": job["role"],
                "from": job["processing_status"],
                "to": "rejected",
                "reason": "owner_decision",
                "packet_removed": packet_removed,
            })
        elif decision["decision"] == "accepted":
            application_status = str(job.get("application_status") or "").strip().lower()
            if application_status in {"submitted", "sent", "applied"}:
                preserved.append({
                    "job_id": job_id,
                    "state": job.get("processing_status") or "applied",
                    "preserved": True,
                    "reason": "application_submitted",
                })
                continue
            target_variant = "ats-linear"
            expected_status = "generation_ready"
            decision_score = _effective_score(record, str(job.get("fit_score", "")))
            needs = []
            if job["processing_status"] != expected_status:
                needs.append(f"processing_status={job['processing_status']}")
            if processing_state.get("status") != expected_status:
                needs.append(f"status={processing_state.get('status')}")
            if processing_state.get("selected_resume_variant") != target_variant:
                needs.append(f"selected_resume_variant={processing_state.get('selected_resume_variant')}")
            if processing_state.get("no_gmail_draft") is not True:
                needs.append("no_gmail_draft missing")
            if processing_state.get("external_action_allowed") is not False:
                needs.append("external_action_allowed not false")
            if processing_state.get("owner_decision", {}).get("decision") != "accepted":
                needs.append("owner_decision.accepted missing")
            if not needs:
                preserved.append({"job_id": job_id, "state": "generation_ready", "preserved": True})
                continue
            fields = {
                "status": expected_status,
                "selected_resume_variant": target_variant,
                "no_gmail_draft": True,
                "external_action_allowed": False,
                "auto_submit": False,
                "owner_decision": {
                    "decision": "accepted",
                    "recorded_at": utc_now(),
                    "score": decision_score,
                    "reason": decision["reason"],
                },
            }
            changes: dict[str, Any] = {"processing_state": fields}
            if job["processing_status"] != expected_status:
                changes["processing_status"] = expected_status
                changes["next_action"] = "Generate one structured application draft; portal route with ATS Linear selected; no Gmail draft; no auto-submit."
            _update_processing_state(
                tracker,
                job_id,
                fields,
                comment=(
                    "Persisted owner decision: FADEN accepted at 78 under the 70 threshold; portal-only "
                    "route; ATS Linear selected; no Gmail draft; no auto-submit."
                ),
                actor="owner",
                action="approved",
            )
            if "processing_status" in changes:
                tracker.update_job(
                    job_id,
                    {key: changes[key] for key in ("processing_status", "next_action") if key in changes},
                    comment="Owner decision applied: FADEN is generation-ready under the 70 threshold.",
                    actor="owner",
                    action="approved",
                    requires_owner_review=False,
                )
            _set_pipeline_stage(root, job_id, expected_status)
            report.append({
                "job_id": job_id,
                "company": job["company"],
                "role": job["role"],
                "from": job["processing_status"],
                "to": expected_status,
                "reason": "owner_decision",
                "needs_applied": needs,
            })


def _reconcile_below_threshold(
    tracker: Any,
    config: dict[str, Any],
    root: Path | None,
    *,
    threshold: int,
    report: list[dict[str, Any]],
) -> None:
    """No current job below 70 may remain generation_ready unless a persisted
    explicit owner force/accept decision applies."""
    for row in tracker.list_rows():
        job_id = row["job_id"]
        if row["processing_status"] != "generation_ready":
            continue
        record = tracker.get_job(job_id)
        application_status = str((record.get("job") or {}).get("application_status") or "").strip().lower()
        if application_status in {"submitted", "sent", "applied"}:
            continue
        score = _effective_score(record, row["fit_score"])
        if score >= threshold:
            continue
        if _has_owner_force(record, config):
            continue
        target = _below_threshold_status(score, config)
        packet_removed = _remove_generation_packet(root, job_id)
        band = _recommendation_name(score, config)
        processing_state = dict(record.get("processing_state") or {})
        _update_processing_state(
            tracker,
            job_id,
            {
                "status": target,
                "blockers": [f"below_generation_threshold:{score}"],
                "external_action_allowed": False,
                "reconciled_reason": (
                    f"score {score} is below the canonical generation threshold {threshold}; "
                    f"assigned to {target} ({band} band) without a persisted owner force decision"
                ),
            },
            comment=(
                f"Canonical threshold reconciliation: score {score} is below the {threshold} "
                f"generation threshold; moved from generation_ready to {target} ({band} band)."
            ),
            actor="system",
            action="updated",
            requires_owner_review=True,
        )
        tracker.update_job(
            job_id,
            {
                "processing_status": target,
                "next_action": f"Below the {threshold} generation threshold; owner override required before any generation.",
            },
            comment=f"Threshold reconciliation: generation_ready -> {target} (score {score} < {threshold}).",
            actor="system",
            action="updated",
            requires_owner_review=True,
        )
        _set_pipeline_stage(root, job_id, target)
        report.append({
            "job_id": job_id,
            "company": row["company"],
            "role": row["role"],
            "from": "generation_ready",
            "to": target,
            "score": score,
            "reason": f"below_generation_threshold:{score}",
            "packet_removed": packet_removed,
        })


def _recommendation_name(score: int, config: dict[str, Any]) -> str:
    bands = config["scoring"]["thresholds"]
    if score >= bands["high_priority"]:
        return "high_priority"
    if score >= bands["credible"]:
        return "credible"
    if score >= bands["selective"]:
        return "selective"
    return "weak"


def _is_stale_threshold_blocker(value: Any) -> bool:
    return str(value).strip().startswith("below_generation_threshold:")


def _truthful_retained_blocker(record: dict[str, Any], status: str) -> str:
    """Return a truthful current-state reason when the retired threshold was
    the only recorded blocker. This deliberately does not promote legacy jobs:
    promotion requires the normal pipeline or an explicit owner decision."""
    processing_state = record.get("processing_state") or {}
    route = processing_state.get("route") or {}
    route_blocker = str(route.get("blocker", "")).strip()
    if route_blocker:
        return route_blocker
    live_status = str(processing_state.get("live_status", "")).strip().lower()
    if live_status in {"closed", "expired", "removed", "unavailable"}:
        return f"vacancy_{live_status}"
    if processing_state.get("canonical_job_id") or status == "superseded":
        return "duplicate_or_superseded_record"
    route_name = str(route.get("route", "")).strip().lower()
    portal_usable = route_name == "portal" and bool(str(route.get("application_url", "")).strip())
    email_usable = route_name == "email" and bool(str(route.get("recipient", "")).strip())
    if not (portal_usable or email_usable):
        return "application_route_unresolved"
    if status in {"blocked", "selective", "rejected"}:
        return f"legacy_{status}_pending_owner_reassessment"
    return ""


def _cleanup_stale_threshold_blockers(
    tracker: Any,
    root: Path | None,
    *,
    threshold: int,
) -> list[dict[str, Any]]:
    """Remove retired threshold-80 blocker text from score >=70 records.

    Current statuses, genuine blockers, protected states and owner decisions are
    preserved. If the retired threshold was the only reason on a legacy blocked,
    selective or rejected record, a truthful non-threshold review reason is
    recorded instead of blindly promoting the job.
    """
    changes: list[dict[str, Any]] = []
    for row in tracker.list_rows():
        job_id = row["job_id"]
        record = tracker.get_job(job_id)
        score = _effective_score(record, row.get("fit_score", ""))
        if score < threshold:
            continue
        status = row["processing_status"]
        processing_state = dict(record.get("processing_state") or {})
        before_blockers = [str(item) for item in processing_state.get("blockers", [])]
        after_blockers = [item for item in before_blockers if not _is_stale_threshold_blocker(item)]

        pipeline_path = _artifact_dir(root, job_id) / "pipeline_state.json"
        pipeline_before: list[str] = []
        if pipeline_path.is_file():
            wrapper = json.loads(pipeline_path.read_text(encoding="utf-8"))
            pipeline_before = [str(item) for item in (wrapper.get("data", {}).get("blockers") or [])]
        pipeline_after = [item for item in pipeline_before if not _is_stale_threshold_blocker(item)]

        stale_processing = len(before_blockers) != len(after_blockers)
        stale_pipeline = len(pipeline_before) != len(pipeline_after)
        if not stale_processing and not stale_pipeline:
            continue

        replacement = ""
        if stale_processing and not after_blockers:
            replacement = _truthful_retained_blocker(record, status)
            if replacement:
                after_blockers = [replacement]
        if stale_pipeline and not pipeline_after:
            pipeline_after = list(after_blockers)

        comment = (
            f"Removed obsolete below_generation_threshold blocker from score {score} record after "
            f"the canonical generation threshold changed to {threshold}; preserved status {status} "
            "and all genuine blockers/owner decisions."
        )
        if stale_processing:
            processing_state["blockers"] = after_blockers
            processing_state["threshold_cleanup_at"] = utc_now()
            processing_state["threshold_cleanup_reason"] = (
                f"retired threshold blocker removed at canonical threshold {threshold}"
            )
            tracker.update_job(
                job_id,
                {"processing_state": processing_state},
                comment=comment,
                actor="system",
                action="updated",
                requires_owner_review=bool(replacement and "pending_owner_reassessment" in replacement),
            )
        else:
            tracker.record_event(
                actor="system",
                entity_type="job",
                entity_id=job_id,
                action="updated",
                before={"pipeline_blockers": pipeline_before},
                after={"pipeline_blockers": pipeline_after},
                comment=comment,
                requires_owner_review=False,
            )
        pipeline_changed = _set_pipeline_blockers(
            root,
            job_id,
            pipeline_after if stale_pipeline else after_blockers,
            stage=status,
        )
        changes.append({
            "job_id": job_id,
            "company": row["company"],
            "role": row["role"],
            "score": score,
            "status_retained": status,
            "removed_processing_blockers": [item for item in before_blockers if _is_stale_threshold_blocker(item)],
            "removed_pipeline_blockers": [item for item in pipeline_before if _is_stale_threshold_blocker(item)],
            "remaining_blockers": after_blockers,
            "replacement_reason": replacement,
            "pipeline_updated": pipeline_changed,
        })
    return changes


def _count_stale_threshold_blockers(
    tracker: Any,
    root: Path | None,
    *,
    threshold: int,
) -> dict[str, Any]:
    jobs: list[str] = []
    occurrences = 0
    for row in tracker.list_rows():
        record = tracker.get_job(row["job_id"])
        if _effective_score(record, row.get("fit_score", "")) < threshold:
            continue
        current = list((record.get("processing_state") or {}).get("blockers") or [])
        path = _artifact_dir(root, row["job_id"]) / "pipeline_state.json"
        pipeline: list[Any] = []
        if path.is_file():
            wrapper = json.loads(path.read_text(encoding="utf-8"))
            pipeline = list(wrapper.get("data", {}).get("blockers") or [])
        count = sum(1 for item in [*current, *pipeline] if _is_stale_threshold_blocker(item))
        if count:
            jobs.append(row["job_id"])
            occurrences += count
    return {"jobs": jobs, "job_count": len(jobs), "occurrences": occurrences}


def _threshold_cleanup_history(tracker: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in tracker.list_rows():
        record = tracker.get_job(row["job_id"])
        state = record.get("processing_state") or {}
        cleaned_at = str(state.get("threshold_cleanup_at", "")).strip()
        if not cleaned_at:
            continue
        records.append({
            "job_id": row["job_id"],
            "company": row["company"],
            "role": row["role"],
            "score": _effective_score(record, row.get("fit_score", "")),
            "status": row["processing_status"],
            "cleaned_at": cleaned_at,
            "reason": state.get("threshold_cleanup_reason", ""),
            "current_blockers": list(state.get("blockers") or []),
        })
    return records


def _reconcile_zawaya_duplicate(
    tracker: Any,
    root: Path | None,
    *,
    report: list[dict[str, Any]],
    preserved: list[dict[str, Any]],
) -> None:
    job_id = ZAWAYA_DUPLICATE_JOB_ID
    try:
        record = tracker.get_job(job_id)
    except KeyError:
        return
    job = record["job"]
    if job["processing_status"] == "superseded":
        preserved.append({"job_id": job_id, "state": "superseded", "preserved": True})
        return
    processing_state = dict(record.get("processing_state") or {})
    processing_state.update({
        "status": "superseded",
        "canonical_job_id": ZAWAYA_APPLIED_JOB_ID,
        "external_action_allowed": False,
        "superseded_at": utc_now(),
        "reason": "duplicate of the applied Zawaya record (exact_external_id)",
    })
    tracker.update_job(
        job_id,
        {
            "processing_status": "superseded",
            "owner": "system",
            "application_status": "not_submitted",
            "next_action": f"Superseded by canonical job {ZAWAYA_APPLIED_JOB_ID} (applied).",
            "processing_state": processing_state,
        },
        comment=(
            "Duplicate reconciliation: Zawaya Design Manager record is a duplicate of the applied "
            "canonical record 4ec93d69301ada8bcc85; marked superseded so the preserved 'applied' "
            "state stays unambiguous."
        ),
        actor="system",
        action="updated",
        requires_owner_review=False,
    )
    _set_pipeline_stage(root, job_id, "superseded")
    report.append({
        "job_id": job_id,
        "company": job["company"],
        "role": job["role"],
        "from": job["processing_status"],
        "to": "superseded",
        "reason": "duplicate_of_applied_record",
        "canonical_job_id": ZAWAYA_APPLIED_JOB_ID,
    })


def _check_named_roles(tracker: Any, config: dict[str, Any], *, threshold: int) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for job_id, company, role in NAMED_GENERATION_READY_ROLES:
        try:
            record = tracker.get_job(job_id)
        except KeyError:
            checks.append({
                "job_id": job_id,
                "company": company,
                "role": role,
                "found": False,
                "eligible": False,
                "reason": "job_id not present in tracker",
            })
            continue
        job = record["job"]
        score = _effective_score(record, str(job.get("fit_score", "")))
        status = job["processing_status"]
        application_status = str(job.get("application_status") or "").strip().lower()
        submitted = application_status in {"submitted", "sent", "applied"}
        eligible = status == "generation_ready" and score >= threshold and not submitted
        checks.append({
            "job_id": job_id,
            "company": job["company"],
            "role": job["role"],
            "found": True,
            "score": score,
            "status": status,
            "threshold": threshold,
            "eligible": eligible,
            "reason": (
                "confirmed generation-ready at/above the 70 threshold" if eligible
                else "not forced: canonical current state differs from the handoff naming"
            ),
        })
    return checks


def reconcile(root: Path | None = None) -> dict[str, Any]:
    """Idempotent reconciliation. Returns before/after counts, changed job ids
    and per-job reasons. Never sends, submits or creates drafts."""
    config, _ = load_config(root)
    tracker = _load_tracker_ops(root)
    rows = tracker.list_rows()
    threshold = int(config["scoring"]["thresholds"]["high_priority"])
    before = dict(Counter(row["processing_status"] for row in rows))

    changed: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    _reconcile_submitted_lifecycle(tracker, root, report=changed, preserved=preserved)
    _apply_owner_decisions(tracker, config, root, report=changed, preserved=preserved, notes=notes, threshold=threshold)
    _reconcile_below_threshold(tracker, config, root, threshold=threshold, report=changed)
    _reconcile_zawaya_duplicate(tracker, root, report=changed, preserved=preserved)
    stale_threshold_cleanup = _cleanup_stale_threshold_blockers(
        tracker,
        root,
        threshold=threshold,
    )
    stale_threshold_remaining = _count_stale_threshold_blockers(
        tracker,
        root,
        threshold=threshold,
    )
    named_checks = _check_named_roles(tracker, config, threshold=threshold)

    after_rows = tracker.list_rows()
    after = dict(Counter(row["processing_status"] for row in after_rows))
    protected = {
        "applied": ZAWAYA_APPLIED_JOB_ID,
        "package_approved_pending_submission": PARSONS_SDM_APPROVED_JOB_ID,
    }
    protected_statuses = {
        "awaiting_owner_approval",
        "owner_review_ready",
        "applied",
        "package_approved_pending_submission",
        "superseded",
    }
    protected_ids = {
        row["job_id"] for row in after_rows if row["processing_status"] in protected_statuses
    }
    result = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "threshold": threshold,
        "idempotent": True,
        "before_counts": before,
        "after_counts": after,
        "changed_jobs": changed,
        "changed_job_ids": [item["job_id"] for item in changed],
        "changed_count": len(changed),
        "stale_threshold_cleanup": stale_threshold_cleanup,
        "stale_threshold_cleanup_count": len(stale_threshold_cleanup),
        "stale_threshold_blockers_remaining": stale_threshold_remaining,
        "threshold_cleanup_history": _threshold_cleanup_history(tracker),
        "notes": notes,
        "preserved": preserved,
        "protected_statuses": sorted(protected_statuses),
        "protected_job_ids": sorted(protected_ids),
        "named_generation_ready_checks": named_checks,
        "send_or_submit": False,
        "drafts_created": 0,
    }
    _, paths = load_config(root)
    report_dir = paths.tracker_base / "runtime"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"reconciliation-report-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    temp = report_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(report_path)
    result["report_path"] = str(report_path)
    return result


# ---------------------------------------------------------------------------
# run (deterministic batch orchestration)
# ---------------------------------------------------------------------------

def _payload_from_record(record: dict[str, Any], root: Path | None) -> dict[str, Any]:
    """Faithfully reconstruct a prepare payload from the stored normalized state
    so re-preparing a job is deterministic and idempotent."""
    job = record.get("job", {})
    artifact_dir = _artifact_dir(root, str(job.get("job_id", "")))
    normalized: dict[str, Any] = {}
    stage_path = artifact_dir / "normalized_job.json"
    if stage_path.is_file():
        try:
            normalized = json.loads(stage_path.read_text(encoding="utf-8"))["data"]
        except (json.JSONDecodeError, KeyError, OSError):
            normalized = {}
    if not isinstance(normalized, dict):
        normalized = {}
    processing_state = record.get("processing_state") or {}
    route = processing_state.get("route") or {}
    if not normalized.get("full_job_description"):
        normalized = {
            "full_job_description": record.get("full_job_description") or "",
            "source_url": job.get("source_url", ""),
            "application_url": route.get("application_url", ""),
            "recipient": route.get("recipient", ""),
            "recipient_source": route.get("recipient_source", ""),
            "live_status": processing_state.get("live_status", ""),
            "live_verified_at": processing_state.get("live_verified_at", ""),
            "live_verification_source": processing_state.get("live_verification_source", ""),
        }
    return {
        "company": job.get("company") or normalized.get("company", ""),
        "role": job.get("role") or normalized.get("role", ""),
        "full_job_description": normalized.get("full_job_description") or record.get("full_job_description") or "",
        "source": job.get("source") or normalized.get("source", "manual"),
        "external_job_id": job.get("external_job_id") or normalized.get("reference", ""),
        "source_url": normalized.get("source_url") or job.get("source_url", ""),
        "location": job.get("location") or normalized.get("location", ""),
        "application_url": normalized.get("application_url") or route.get("application_url", ""),
        "recipient": normalized.get("recipient") or route.get("recipient", ""),
        "recipient_source": normalized.get("recipient_source") or route.get("recipient_source", ""),
        "required_email_subject": normalized.get("required_email_subject", ""),
        "application_instructions": normalized.get("application_instructions", ""),
        "live_status": normalized.get("live_status") or processing_state.get("live_status", ""),
        "live_verified_at": normalized.get("live_verified_at") or processing_state.get("live_verified_at", ""),
        "live_verification_source": normalized.get("live_verification_source") or processing_state.get("live_verification_source", ""),
    }


def run(
    root: Path | None = None,
    *,
    min_score: int | None = None,
    process_all: bool = False,
    reprocess_existing: bool = False,
) -> dict[str, Any]:
    """Deterministic batch orchestration. Rebuilds/validates the bundle as
    needed, reconciles tracker statuses, prepares eligible records through the
    configured no-send pipeline, syncs the local dashboard data and emits a
    structured report. ``min_score`` is an explicit owner-selected threshold
    from 0-100. ``process_all`` removes the routine daily packet cap.
    ``reprocess_existing`` also re-prepares active verified-live non-submitted
    records that already have a generated package, so an owner-triggered batch
    can genuinely refresh current CVs and cover letters. Never sends, never
    submits, never creates Gmail drafts."""
    config, _ = load_config(root)
    bundle_state = bundle_status(root)
    if not bundle_state.get("valid") or not bundle_state.get("current"):
        build_bundle(root)
        bundle_state = bundle_status(root)
    bundle = load_bundle(root)

    reconciliation = reconcile(root)

    canonical_threshold = int(config["scoring"]["thresholds"]["high_priority"])
    requested_threshold = canonical_threshold if min_score is None else int(min_score)
    if requested_threshold < 0 or requested_threshold > 100:
        raise ValueError("min_score must be between 0 and 100")
    threshold = requested_threshold
    cap = None if process_all else int(config["daily_scanner"]["maximum_generation_packets_per_scan"])
    tracker = _load_tracker_ops(root)

    eligible_by_job: dict[str, int] = {}
    for row in tracker.list_rows():
        record = tracker.get_job(row["job_id"])
        processing_state = record.get("processing_state") or {}
        job = record.get("job") or {}
        if processing_state.get("status") == "rejected":
            continue
        application_status = str(job.get("application_status") or record.get("application_status") or "not_submitted").lower()
        submitted = application_status in {"submitted", "sent", "applied"}
        if submitted:
            continue
        score = _effective_score(record, row["fit_score"])
        if score < threshold:
            continue

        is_generation_ready = row["processing_status"] == "generation_ready"
        if not is_generation_ready and reprocess_existing:
            live_status = str(processing_state.get("live_status") or record.get("live_status") or "").lower()
            processing_status = str(row.get("processing_status") or "").lower()
            terminal = processing_status in {"applied", "superseded", "rejected", "closed", "inactive"}
            is_generation_ready = live_status == "live" and not terminal
        if not is_generation_ready:
            continue
        eligible_by_job[row["job_id"]] = score

    eligible = sorted(((score, job_id) for job_id, score in eligible_by_job.items()), key=lambda item: (-item[0], item[1]))

    processed: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    selected = eligible if cap is None else eligible[:cap]
    overflow = [] if cap is None else eligible[cap:]
    for score, job_id in selected:
        try:
            record = tracker.get_job(job_id)
            payload = _payload_from_record(record, root)
            state = prepare(payload, root=root, actor="system")
            # Re-apply persisted owner-decision fields that prepare replaces.
            if job_id in {decision["job_id"] for decision in OWNER_DECISIONS}:
                reconcile(root)
            processed.append({
                "job_id": job_id,
                "score": score,
                "stage": state.get("stage"),
                "blockers": state.get("blockers", []),
                "warnings": state.get("warnings", []),
                "generation_packet": bool(state.get("outputs", {}).get("generation_packet")),
                "bundle_hash": state.get("bundle_hash", bundle["bundle_hash"]),
            })
        except Exception as exc:  # noqa: BLE001 - per-job errors reported, run continues
            errors.append({"job_id": job_id, "error": f"{type(exc).__name__}: {exc}"})
    for score, job_id in overflow:
        deferred.append({"job_id": job_id, "score": score, "reason": f"over maximum_generation_packets_per_scan cap {cap}"})

    dashboard_state = dashboard(root, sync=True)

    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "engine_version": bundle.get("engine_version"),
        "bundle": {
            "valid": bundle_state.get("valid"),
            "current": bundle_state.get("current"),
            "bundle_hash": bundle["bundle_hash"],
        },
        "threshold": threshold,
        "canonical_threshold": canonical_threshold,
        "owner_threshold_override": threshold != canonical_threshold,
        "generation_packet_cap": cap,
        "process_all": process_all,
        "reprocess_existing": reprocess_existing,
        "reconciliation": reconciliation,
        "eligible": [{"job_id": item[1], "score": item[0]} for item in eligible],
        "processed": processed,
        "deferred": deferred,
        "errors": errors,
        "dashboard": {key: dashboard_state[key] for key in ("mode", "path", "jobs", "counts", "publisher")},
        "send_or_submit": False,
        "drafts_created": 0,
        "submissions": 0,
    }
    _, paths = load_config(root)
    report_dir = paths.tracker_base / "runtime"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"run-report-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    temp = report_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(report_path)
    report["report_path"] = str(report_path)
    return report


# ---------------------------------------------------------------------------
# validate (per-job, or aggregate config/bundle/tracker/all generated jobs)
# ---------------------------------------------------------------------------

def _validate_generated_job(job_id: str, root: Path | None, bundle: dict[str, Any]) -> dict[str, Any]:
    artifact_dir = _artifact_dir(root, job_id)
    application_path = artifact_dir / "generated_application.json"
    packet_path = artifact_dir / "generation_packet.json"
    if not application_path.is_file():
        return {"job_id": job_id, "present": False, "valid": True, "findings": []}
    if not packet_path.is_file():
        return {"job_id": job_id, "present": True, "valid": False, "findings": [
            {"code": "packet_missing", "severity": "error", "message": "generated_application.json exists but generation_packet.json is missing"}
        ]}
    application = json.loads(application_path.read_text(encoding="utf-8"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    findings = validate_generated_application(application, packet, bundle)
    return {
        "job_id": job_id,
        "present": True,
        "valid": not any(item.get("severity") == "error" for item in findings),
        "findings": findings,
    }


# Jobs in these terminal statuses carry historical application artifacts that
# legitimately predate the current bundle; re-validation against the current
# bundle is informational and must not fail the aggregate validation.
TERMINAL_STATUSES = {"applied", "superseded", "rejected"}


def _is_historical_non_generatable(row: dict[str, Any], record: dict[str, Any], config: dict[str, Any], item: dict[str, Any]) -> bool:
    """Identify old artifacts attached to a currently non-generatable role.

    This deliberately requires all canonical signals: blocked processing state,
    a score below the current generation threshold, the persisted threshold
    blocker, and no current generation packet. Other statuses remain strict.
    """
    if item.get("generation_packet_exists", False):
        return False
    if str(row.get("processing_status", "")) != "blocked":
        return False
    score = _effective_score(record, row.get("fit_score", ""))
    threshold = int(config["scoring"]["thresholds"]["high_priority"])
    if score >= threshold:
        return False
    state = record.get("processing_state") or {}
    blockers = state.get("blockers") or []
    return any(_is_stale_threshold_blocker(value) for value in blockers)


def validate_all(root: Path | None = None, job_id: str = "") -> dict[str, Any]:
    """Per-job validation, or aggregate config/bundle/tracker and every existing
    generated application when no job id is supplied. Applications attached to
    terminal-status jobs (applied/superseded/rejected) are reported as
    historical and never fail the aggregate result."""
    config_check = validate_config(root)
    bundle = load_bundle(root)
    if job_id:
        result = _validate_generated_job(job_id, root, bundle)
        terminal = _job_status(root, job_id) in TERMINAL_STATUSES
        if terminal and result.get("present"):
            result["historical"] = True
            result["valid"] = True
            result["note"] = "artifact is historical; current-bundle validation is informational"
        result["config"] = config_check
        return result
    tracker = _load_tracker_ops(root)
    _, paths = load_config(root)
    config, _ = load_config(root)
    results: list[dict[str, Any]] = []
    for row in tracker.list_rows():
        item = _validate_generated_job(row["job_id"], root, bundle)
        record = tracker.get_job(row["job_id"])
        artifact_dir = _artifact_dir(root, row["job_id"])
        item["generation_packet_exists"] = (artifact_dir / "generation_packet.json").is_file()
        if row["processing_status"] in TERMINAL_STATUSES and item.get("present"):
            item["historical"] = True
            item["valid"] = True
            item["note"] = "artifact is historical; current-bundle validation is informational"
        elif item.get("present") and _is_historical_non_generatable(row, record, config, item):
            item["historical"] = True
            item["valid"] = True
            item["note"] = "artifact is historical; role is below the current generation threshold"
        results.append(item)
    generated = [item for item in results if item.get("present")]
    invalid = [item for item in results if not item.get("valid")]
    historical = [item for item in results if item.get("historical")]
    return {
        "valid": config_check["valid"] and not invalid,
        "config": config_check,
        "bundle": {"valid": bool(bundle.get("bundle_hash")), "hash": bundle.get("bundle_hash")},
        "jobs_checked": len(results),
        "generated_applications": len(generated),
        "historical_artifacts": [item["job_id"] for item in historical],
        "invalid_jobs": [item["job_id"] for item in invalid],
        "job_validation": results,
    }


def _job_status(root: Path | None, job_id: str) -> str:
    tracker = _load_tracker_ops(root)
    for row in tracker.list_rows():
        if row["job_id"] == job_id:
            return row["processing_status"]
    return ""


# ---------------------------------------------------------------------------
# record-review (retain --file; safe default to latest.json when it validates)
# ---------------------------------------------------------------------------

def record_review(root: Path | None = None, file: str = "") -> dict[str, Any]:
    _, paths = load_config(root)
    if file:
        path = Path(file).expanduser()
        if not path.is_absolute():
            path = paths.repo_root / path
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        path = paths.tracker_base / "runtime/review-diffs/latest.json"
        if not path.is_file():
            return {"valid": False, "errors": ["no --file supplied and no runtime/review-diffs/latest.json exists"]}
        payload = json.loads(path.read_text(encoding="utf-8"))
    validation_errors = validate_review_diff(payload)
    if validation_errors:
        return {"valid": False, "errors": validation_errors, "source": str(path)}
    # Idempotency: re-recording an already-recorded review must not duplicate
    # append-only history events.
    review_dir = paths.tracker_base / "runtime/review-diffs"
    review_id = str(payload["review_id"]).strip()
    destination = review_dir / f"{review_id}.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if destination.is_file() and destination.read_text(encoding="utf-8") == text:
        return {
            "valid": True,
            "errors": [],
            "already_recorded": True,
            "saved_to": str(destination),
            "source": str(path),
            "job_events": 0,
            "send_or_submit": False,
        }
    result = record_review_diff(payload, root=root)
    result["source"] = str(path)
    result["already_recorded"] = False
    return result


# ---------------------------------------------------------------------------
# scan (safe wrapper of the existing scanner ingest with explicit input+scanner-id)
# ---------------------------------------------------------------------------

def scan(file: str, scanner_id: str, output: str = "", root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    source = Path(file).expanduser()
    if not source.is_absolute():
        source = paths.repo_root / source
    report = run_scan(source, root=paths.repo_root, scanner_id=scanner_id)
    if output:
        target = Path(output).expanduser()
        if not target.is_absolute():
            target = paths.repo_root / target
        write_report(report, target)
    return report
