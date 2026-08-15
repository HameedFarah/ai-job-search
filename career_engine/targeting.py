"""Deterministic Career Engine target-lane gating and queue cleanup.

This is deliberately narrower than fit scoring. Fit scoring can still describe
why an adjacent role is weak; this gate decides whether a role should consume
manual-review/generation attention at all.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import _role_title_signals


# Small explicit gap not represented by the existing scoring taxonomy. Most
# non-target roles are handled by calibration.out_of_lane or by the production
# individual-contributor rule below.
_SERVICE_ADMIN_ROLE_CUES = (
    "receptionist",
    "reception",
    "front desk",
)

# Existing records in these early/internal states can be safely reconciled when
# their title is unambiguously outside the target lane. Generated packages,
# owner-review states, applied/submitted records and terminal history are left
# untouched so a cleanup pass never rewrites an owner decision or application
# evidence.
_RECONCILABLE_NON_TARGET_STATUSES = {
    "manual_review_needed",
    "ingested",
    "normalizing",
    "blocked",
    "selective",
    "generation_ready",
}
_PROTECTED_APPLICATION_STATUSES = {"applied", "submitted", "sent"}
_PROTECTED_OWNER_DECISIONS = {"accepted", "forced", "approved"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def auto_skip_reason(normalized_job: dict[str, Any], score: dict[str, Any]) -> str:
    """Return a terminal skip reason for clearly non-target roles.

    Rules intentionally reuse the central score calibration rather than grow a
    second title blacklist:
    - out-of-lane disciplines are skipped even when senior/managerial;
    - production individual-contributor roles are skipped unless the title has
      management authority;
    - reception/front-desk roles fill the one obvious taxonomy gap seen in the
      reviewed/closed queue.

    Senior design/project/construction management roles are therefore preserved
    while roles such as Civil Engineer, Site Inspector, Urban Designer, finance
    roles and reception specialists do not enter manual review or generation.
    """

    calibration = score.get("calibration") or {}
    role = str(normalized_job.get("role") or "").strip().lower()

    if any(cue in role for cue in _SERVICE_ADMIN_ROLE_CUES):
        return "non_target_service_or_admin_role"
    if calibration.get("out_of_lane"):
        return "non_target_out_of_lane_role"
    if calibration.get("production") and not calibration.get("has_management"):
        return "non_target_production_individual_contributor"
    return ""


def auto_skip_title_reason(role: str, taxonomy: dict[str, Any]) -> str:
    """Classify a title before JD normalization.

    This prevents an obviously non-target vacancy with an empty/truncated JD
    from being promoted into Manual Review Needed merely because the JD is too
    short to score. The same central title calibration is reused, so there is no
    second specialization taxonomy to keep in sync.
    """

    title = str(role or "").strip()
    if not title:
        return ""
    calibration = _role_title_signals(title, taxonomy)
    return auto_skip_reason({"role": title}, {"calibration": calibration})


def _remove_pending_generation_packet(base_dir: Path, job_id: str) -> bool:
    removed = False
    artifact_dir = base_dir / "artifacts" / job_id
    for name in ("generation_packet.json", "generation_packet.stage.json"):
        path = artifact_dir / name
        if path.is_file():
            path.unlink()
            removed = True
    return removed


def _reconcile_pipeline_state(base_dir: Path, job_id: str, reason: str, reconciled_at: str) -> bool:
    path = base_dir / "artifacts" / job_id / "pipeline_state.json"
    if not path.is_file():
        return False
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    data = dict(wrapper.get("data") or {})
    data.update({
        "stage": "rejected",
        "skip_reason": reason,
        "target_lane_reconciled_at": reconciled_at,
        "external_action_allowed": False,
    })
    wrapper["data"] = data
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return True


def reconcile_existing_non_target_jobs(
    tracker: Any,
    taxonomy: dict[str, Any],
    *,
    actor: str = "system",
) -> dict[str, Any]:
    """Append-only cleanup of existing active non-target tracker rows.

    Only early/internal, non-submitted states are eligible. Existing generated
    packages, owner-review/approval states, applied/submitted evidence,
    superseded records and explicit owner accept/force decisions are preserved.
    The job record and history remain in place; only its current workflow state
    becomes ``rejected`` so the dashboard moves it to Closed / inactive.
    """

    changed: list[dict[str, Any]] = []
    preserved_owner_decisions: list[str] = []
    base_dir = Path(tracker.base_dir)

    for row in tracker.list_rows():
        status = str(row.get("processing_status") or "").strip().lower()
        if status not in _RECONCILABLE_NON_TARGET_STATUSES:
            continue
        application_status = str(row.get("application_status") or "").strip().lower()
        if application_status in _PROTECTED_APPLICATION_STATUSES:
            continue

        job_id = str(row.get("job_id") or "").strip()
        if not job_id:
            continue
        record = tracker.get_job(job_id)
        job = record.get("job") or {}
        processing_state = dict(record.get("processing_state") or {})
        owner_decision = processing_state.get("owner_decision") or {}
        if str(owner_decision.get("decision") or "").strip().lower() in _PROTECTED_OWNER_DECISIONS:
            preserved_owner_decisions.append(job_id)
            continue

        role = str(job.get("role") or row.get("role") or "").strip()
        reason = auto_skip_title_reason(role, taxonomy)
        if not reason:
            continue

        reconciled_at = _utc_now()
        previous_status = status
        packet_removed = _remove_pending_generation_packet(base_dir, job_id)
        pipeline_updated = _reconcile_pipeline_state(base_dir, job_id, reason, reconciled_at)
        processing_state.update({
            "owner": actor,
            "status": "rejected",
            "reason_code": reason,
            "skip_reason": reason,
            "target_lane_reconciled_at": reconciled_at,
            "target_lane_reconciled_from": previous_status,
            "external_action_allowed": False,
            "send_or_submit": False,
        })
        tracker.update_job(
            job_id,
            {
                "processing_status": "rejected",
                "next_action": "Skipped automatically as a non-target role",
                "processing_state": processing_state,
            },
            comment=(
                f"Target-lane reconciliation: {role} moved from {previous_status} to rejected "
                f"because {reason}; source/provenance/history preserved and no external action allowed."
            ),
            actor=actor,
            action="rejected",
            requires_owner_review=False,
        )
        changed.append({
            "job_id": job_id,
            "company": str(job.get("company") or row.get("company") or ""),
            "role": role,
            "from": previous_status,
            "to": "rejected",
            "reason": reason,
            "generation_packet_removed": packet_removed,
            "pipeline_state_updated": pipeline_updated,
        })

    return {
        "changed_count": len(changed),
        "changed_jobs": changed,
        "counts_by_reason": dict(Counter(item["reason"] for item in changed)),
        "preserved_owner_decision_job_ids": preserved_owner_decisions,
        "send_or_submit": False,
    }
