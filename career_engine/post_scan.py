from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .gmail_reconcile import reconcile_submission_mail
from .owner_feedback import apply_owner_feedback_calibration, reconcile_irrelevant_feedback


def _safe_runtime_step(name: str, function: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Keep discovery usable when optional connected-data reconciliation fails.

    Gmail and here.now feedback are evidence enrichments after discovery. Their
    failure must be visible in the structured report, but must never turn a
    completed discovery scan into a send/submit action or silently abort it.
    """
    try:
        result = function(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - bounded and surfaced in scan report
        return {"step": name, "error": f"{type(exc).__name__}: {exc}", "send_or_submit": False}
    return result if isinstance(result, dict) else {"step": name, "result": result, "send_or_submit": False}


def reconcile_after_scan(root: Path, report: dict[str, Any]) -> dict[str, Any]:
    """Apply higher-authority evidence after a normal discovery scan.

    Ordering is deliberate:
    1. Verified Gmail submission evidence promotes real applications.
    2. Explicit owner Irrelevant feedback is reconciled into CareerTracker.
    3. The bounded calibration corpus is rebuilt and applied to newly scanned
       jobs, while applied jobs and human overrides remain protected.
    """
    report["gmail_submission_reconciliation"] = _safe_runtime_step(
        "gmail_submission_reconciliation", reconcile_submission_mail, root,
    )
    report["owner_irrelevant_reconciliation"] = _safe_runtime_step(
        "owner_irrelevant_reconciliation", reconcile_irrelevant_feedback, root,
    )
    report["owner_feedback_calibration"] = _safe_runtime_step(
        "owner_feedback_calibration", apply_owner_feedback_calibration, root, report,
    )
    return report
