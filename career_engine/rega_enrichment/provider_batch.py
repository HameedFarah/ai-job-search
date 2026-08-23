"""Batch provider contact discovery over already verified REGA domains.

This stage is deliberately separate from official identity enrichment. Provider
contacts are retained as candidate evidence only and can never mutate official
REGA fields or become outreach-ready without separate official-page proof.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .provider_waterfall import WaterfallResult, run_configured_domain_waterfall


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_domain(value: str) -> str:
    domain = value.strip().lower()
    if domain.startswith("http://") or domain.startswith("https://"):
        from urllib.parse import urlsplit
        domain = (urlsplit(domain).hostname or "").lower()
    return domain.removeprefix("www.").strip().strip("/")


def run_provider_batch(
    sidecar_path: Path,
    output_path: Path,
    *,
    allow_existing_credit: bool = False,
    include_candidates: bool = False,
    max_domains: int | None = None,
    waterfall: Callable[..., WaterfallResult] = run_configured_domain_waterfall,
) -> dict[str, Any]:
    """Run one bounded provider waterfall per unique accepted official domain.

    Default admission is confirmed official domains only. Candidate domains can
    be included only with an explicit flag; contacts remain non-promoting in all
    cases. One provider failure does not stop other domains/providers.
    """
    if not sidecar_path.is_file():
        raise FileNotFoundError(sidecar_path)
    if max_domains is not None and max_domains < 1:
        raise ValueError("max_domains must be >= 1 when provided")

    with sidecar_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    accepted_assignments = {"confirmed"}
    if include_candidates:
        accepted_assignments.add("candidate")

    by_domain: dict[str, list[dict[str, str]]] = {}
    skipped = 0
    for row in rows:
        assignment = str(row.get("assignment", "")).strip().lower()
        domain = _normalized_domain(str(row.get("official_domain", "")))
        if assignment not in accepted_assignments or not domain or "." not in domain:
            skipped += 1
            continue
        by_domain.setdefault(domain, []).append(row)

    domains = sorted(by_domain)
    if max_domains is not None:
        domains = domains[:max_domains]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    provider_status_counts: dict[str, int] = {}
    contact_count = 0
    domain_failures = 0
    with tmp_path.open("w", encoding="utf-8") as handle:
        for domain in domains:
            source_rows = by_domain[domain]
            try:
                result = waterfall(domain, allow_existing_credit=allow_existing_credit)
                statuses = result.provider_statuses
                contacts = [asdict(contact) for contact in result.contacts]
            except Exception as exc:
                domain_failures += 1
                statuses = [{"provider": "batch", "status": "network_failed", "error_type": type(exc).__name__}]
                contacts = []
            for status in statuses:
                key = f"{status.get('provider', 'unknown')}:{status.get('status', 'unknown')}"
                provider_status_counts[key] = provider_status_counts.get(key, 0) + 1
            # Provider-only contacts must never be outreach-ready.
            for contact in contacts:
                if contact.get("official_recruitment"):
                    raise RuntimeError(f"Provider batch attempted official recruitment promotion for {domain}")
                contact_count += 1
            record = {
                "domain": domain,
                "assignment_floor": "candidate" if include_candidates else "confirmed",
                "company_ids": [str(row.get("company_id", "")) for row in source_rows],
                "license_nos": [str(row.get("License No", "")) for row in source_rows],
                "english_names": [str(row.get("English Name", "")) for row in source_rows],
                "provider_statuses": statuses,
                "candidate_contacts": contacts,
                "outreach_ready_count": 0,
                "official_promotion_performed": False,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
    tmp_path.replace(output_path)

    return {
        "valid": True,
        "sidecar": str(sidecar_path),
        "sidecar_sha256": _sha256(sidecar_path),
        "output": str(output_path),
        "output_sha256": _sha256(output_path),
        "input_rows": len(rows),
        "eligible_unique_domains": len(by_domain),
        "processed_unique_domains": len(domains),
        "skipped_rows": skipped,
        "candidate_contacts": contact_count,
        "outreach_ready_contacts": 0,
        "domain_failures": domain_failures,
        "provider_status_counts": dict(sorted(provider_status_counts.items())),
        "allow_existing_credit": allow_existing_credit,
        "include_candidates": include_candidates,
        "purchase_or_topup_performed": False,
        "zerobounce_called": False,
        "official_fields_mutated": False,
    }
