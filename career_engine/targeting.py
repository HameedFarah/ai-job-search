"""Deterministic Career Engine target-lane gating.

This is deliberately narrower than fit scoring. Fit scoring can still describe
why an adjacent role is weak; this gate decides whether a role should consume
manual-review/generation attention at all.
"""

from __future__ import annotations

from typing import Any


# Small explicit gap not represented by the existing scoring taxonomy. Most
# non-target roles are handled by calibration.out_of_lane or by the production
# individual-contributor rule below.
_SERVICE_ADMIN_ROLE_CUES = (
    "receptionist",
    "reception",
    "front desk",
)


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
