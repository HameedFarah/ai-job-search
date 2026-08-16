#!/usr/bin/env python3
"""Safety wrapper for canonical CareerTracker reconciliation.

Keeps ambiguous duplicate identities as separate jobs and fails closed rather
than silently truncating Site Data reconciliation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

if __package__:
    from tools import career_tracker_unify as base
else:
    # Support production execution as `python3 tools/career_tracker_unify_safe.py`.
    # In direct-script mode Python places tools/ on sys.path rather than the
    # repository root, so the sibling module must be imported without `tools.`.
    import career_tracker_unify as base


def safe_exact_duplicate_groups(records: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Auto-dedupe only when the vacancy identity is unambiguous.

    A shared/generic careers URL is not sufficient by itself. URL matches must
    also have the same normalized company and role. External IDs are scoped to
    source and likewise require the same company+role. Ambiguous candidates stay
    in the tracker for owner review rather than being destructively collapsed.
    """
    identities: dict[str, list[str]] = defaultdict(list)
    for job_id, record in records.items():
        job = record.get("job") or {}
        if base.norm(job.get("processing_status")) == "superseded":
            continue
        company = base.text_key(job.get("company"))
        role = base.text_key(job.get("role"))
        url = base.url_key(job.get("source_url"))
        if not url:
            url = base.url_key((record.get("processing_state") or {}).get("route", {}).get("application_url"))
        if url and company and role:
            identities[f"url:{url}|company:{company}|role:{role}"].append(job_id)
            continue
        external_id = base.text_key(job.get("external_job_id"))
        source = base.text_key(job.get("source"))
        if external_id and source and company and role:
            identities[f"ext:{source}:{external_id}|company:{company}|role:{role}"].append(job_id)
    return [sorted(group) for group in identities.values() if len(group) > 1]


_original_records = base.HereNow.records


def complete_site_records(self, collection: str, limit: int = 1000):
    # The current here.now reader does not expose a proven pagination contract.
    # Never claim an all-data reconciliation if the bounded response could have
    # been truncated. Fail closed so the operator must add pagination first.
    effective_limit = min(max(int(limit), 1), 1000)
    records = _original_records(self, collection, effective_limit)
    if len(records) >= effective_limit:
        raise RuntimeError(
            f"here.now collection '{collection}' returned {len(records)} records at the "
            f"{effective_limit}-record safety limit; full reconciliation cannot be proven"
        )
    return records


base.exact_duplicate_groups = safe_exact_duplicate_groups
base.HereNow.records = complete_site_records


if __name__ == "__main__":
    raise SystemExit(base.main())
