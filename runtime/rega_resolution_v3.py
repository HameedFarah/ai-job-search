#!/usr/bin/env python3
"""Resolve the current REGA employer universe to explicit terminal states.

Design goals:
- Master Tracker remains the single operational authority.
- Free identity/domain + first-party employment-route discovery runs across the
  whole selected universe before any paid fallback.
- Muqawil is used only as public identity/company-size evidence; its mailbox is
  never promoted by this runtime.
- Existing Outscraper credit is optional and bounded by the configured reserve.
- A depleted reserve never stops later free research. Instead, each processed
  company receives an explicit non-sendable terminal classification.
- Only a first-party company-domain mailbox with validator RECEIVING may be
  staged into the existing Send Queue.
- No Gmail send, Gmail draft, application submission, purchase, or top-up path
  exists in this file.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any

from career_engine.rega_enrichment.resolution import (
    MuqawilProfile,
    career_value_score,
    discover_muqawil_profile,
    is_terminal_resolution,
    local_priority_key,
    priority_band,
)
from runtime import rega_priority_scan as legacy


DEFAULT_ROOT = Path("runtime/acceptance/rega-resolution-v3")
FINAL_CHECKPOINT_STATES = {"resolved", "staged"}


def _load_checkpoints(path: Path) -> dict[str, dict[str, Any]]:
    return legacy.load_jsonl(path)


def _checkpoint(
    checkpoints: dict[str, dict[str, Any]],
    path: Path,
    master_id: str,
    **payload: Any,
) -> None:
    record = dict(checkpoints.get(master_id) or {})
    record.update(payload)
    record["master_id"] = master_id
    record["at"] = legacy.utc_now()
    checkpoints[master_id] = record
    legacy.atomic_jsonl(path, checkpoints)


def _context_note(priority: str, score: int, muqawil: MuqawilProfile | None) -> str:
    bits = [f"REGA resolution v3 {legacy.today()}: priority={priority}", f"score={score}"]
    if muqawil is not None:
        bits.append(f"Muqawil={muqawil.url}")
        if muqawil.company_size:
            bits.append(f"Muqawil size={muqawil.company_size}")
        if muqawil.membership_number:
            bits.append(f"Muqawil membership={muqawil.membership_number}")
    return "; ".join(bits)


def _persist_fields(
    token: str,
    headers: list[str],
    row: dict[str, str],
    updates: dict[str, str],
    *,
    note: str,
) -> None:
    updates = dict(updates)
    updates["Source_Date_or_Freshness"] = legacy.today()
    updates["Notes"] = legacy.append_note(row.get("Notes", ""), note)
    legacy.update_master_fields(token, headers, row, updates)


def persist_identity_unconfirmed(
    token: str,
    headers: list[str],
    row: dict[str, str],
    *,
    priority: str,
    score: int,
    reason: str,
    muqawil: MuqawilProfile | None,
) -> None:
    next_action = (
        "Terminal automated classification; Priority-A manual identity review if capacity permits"
        if priority == "A"
        else "Terminal automated classification; reopen only with new first-party/registry evidence"
    )
    note = (
        f"{_context_note(priority, score, muqawil)}; terminal=IDENTITY_UNCONFIRMED; "
        f"reason={reason}"
    )
    _persist_fields(
        token,
        headers,
        row,
        {
            "Source_Status": "Identity unconfirmed after automated REGA resolution",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": next_action,
        },
        note=note,
    )


def persist_resolved_no_route(
    token: str,
    headers: list[str],
    row: dict[str, str],
    *,
    domain: str,
    priority: str,
    score: int,
    reason: str,
    muqawil: MuqawilProfile | None,
) -> None:
    note = (
        f"{_context_note(priority, score, muqawil)}; "
        f"terminal=RESOLVED_NO_EMPLOYMENT_ROUTE; reason={reason}"
    )
    _persist_fields(
        token,
        headers,
        row,
        {
            "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
            "Source_Status": "Resolved - verified company; no usable employment route",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": "Closed automated route; revisit only when new first-party employment evidence appears",
        },
        note=note,
    )


def persist_unvalidated_mailbox(
    token: str,
    headers: list[str],
    row: dict[str, str],
    *,
    domain: str,
    email: str,
    source_url: str,
    priority: str,
    score: int,
    reason: str,
    muqawil: MuqawilProfile | None,
) -> None:
    note = (
        f"{_context_note(priority, score, muqawil)}; "
        f"terminal=MAILBOX_UNVALIDATED; email={email}; source={source_url}; reason={reason}; not queued"
    )
    _persist_fields(
        token,
        headers,
        row,
        {
            "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
            "Source_Status": "Resolved - official mailbox found; validation unavailable",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": "Hold exact mailbox until validator capacity is available; do not send blind",
        },
        note=note,
    )


def persist_nonreceiving_mailbox(
    token: str,
    headers: list[str],
    row: dict[str, str],
    *,
    domain: str,
    email: str,
    source_url: str,
    validation: dict[str, Any],
    priority: str,
    score: int,
    muqawil: MuqawilProfile | None,
) -> None:
    status = str(validation.get("status") or "UNKNOWN")
    note = (
        f"{_context_note(priority, score, muqawil)}; "
        f"terminal=MAILBOX_NOT_RECEIVING; email={email}; source={source_url}; validator={status}; not queued"
    )
    _persist_fields(
        token,
        headers,
        row,
        {
            "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
            "Source_Status": f"Resolved - official mailbox not receiving ({status})",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": "Closed automated route; revisit only with a different verified official employment route",
        },
        note=note,
    )


def persist_company_covered(
    token: str,
    headers: list[str],
    row: dict[str, str],
    *,
    result: str,
    priority: str,
    score: int,
) -> None:
    if result == "successfully_contacted":
        status = "Resolved - company already successfully contacted"
        detail = "company-level Sent Email Tracker evidence already exists"
    else:
        status = "Resolved - company already covered by active/sent outreach"
        detail = "company already has an active or sent queue route"
    _persist_fields(
        token,
        headers,
        row,
        {
            "Source_Status": status,
            "Send_Eligibility": "NO_ADDITIONAL_COMPANY_ROUTE_REQUIRED",
            "Next_Action": "No duplicate company outreach; await/review responses",
        },
        note=f"REGA resolution v3 {legacy.today()}: priority={priority}; score={score}; terminal=COMPANY_COVERED; {detail}",
    )


def _source_url(candidate: dict[str, Any], domain: str) -> str:
    urls = candidate.get("source_urls") or []
    return str(urls[0]) if urls else f"https://{domain}/"


def _free_route_candidate(route: Any) -> dict[str, Any] | None:
    if route is None or not getattr(route, "is_email", False):
        return None
    email = str(route.value or "").strip().lower()
    rank, kind = legacy.mailbox_rank(email)
    if rank >= 9:
        return None
    return {
        "email": email,
        "rank": rank,
        "kind": route.email_kind or kind,
        "source_urls": [route.source_url],
        "route_source": "free_first_party_site",
    }


def _paid_available(client: Any | None, reserve: float, required: float) -> bool:
    if client is None:
        return False
    try:
        return legacy.balance(client) - reserve >= required
    except Exception:
        return False


def _stage_receiving(
    *,
    token: str,
    master_headers: list[str],
    row: dict[str, str],
    domain: str,
    candidate: dict[str, Any],
    validation: dict[str, Any],
    config: dict[str, str],
    current_queue: list[dict[str, str]],
    known_emails: set[str],
    permanent_bounces: set[str],
    queued_companies: set[str],
    checkpoints: dict[str, dict[str, Any]],
    checkpoint_path: Path,
    priority: str,
    score: int,
    muqawil: MuqawilProfile | None,
) -> list[dict[str, str]]:
    master_id = str(row.get("Master_ID") or "").strip()
    email = str(validation.get("email") or candidate.get("email") or "").strip().lower()
    if not email or str(validation.get("status") or "").upper() != "RECEIVING" or not validation.get("safe_to_send"):
        raise RuntimeError(f"invalid receiving validation for {master_id}")
    if email in permanent_bounces:
        raise RuntimeError(f"permanent bounce blocked during stage for {master_id}: {email}")
    if email in known_emails:
        persist_company_covered(
            token,
            master_headers,
            row,
            result="active_or_sent_queue",
            priority=priority,
            score=score,
        )
        _checkpoint(
            checkpoints,
            checkpoint_path,
            master_id,
            state="resolved",
            result="validated_email_became_known_before_write",
            email=email,
        )
        return current_queue

    queue_id = legacy.next_queue_id(current_queue)
    _checkpoint(
        checkpoints,
        checkpoint_path,
        master_id,
        state="ready_to_write",
        result="validated_receiving_pending_sheet_write",
        queue_id=queue_id,
        domain=domain,
        candidate=candidate,
        validation=validation,
        priority=priority,
        score=score,
        muqawil=asdict(muqawil) if muqawil else None,
    )
    legacy.append_queue_row(
        token,
        legacy.queue_row_values(
            queue_id=queue_id,
            row=row,
            domain=domain,
            candidate=candidate,
            validation=validation,
            config=config,
        ),
    )
    legacy.ensure_queue_metadata(token, legacy.SPREADSHEET_ID, {queue_id})
    current_queue = legacy.read_queue(token, legacy.SPREADSHEET_ID)
    known_emails.add(email)
    company_key = legacy.normalized_company(row.get("Company_or_Office", ""))
    if company_key:
        queued_companies.add(company_key)
    legacy.persist_receiving_email(
        token,
        master_headers,
        row,
        domain,
        email,
        str(candidate.get("kind") or legacy.mailbox_rank(email)[1]),
        _source_url(candidate, domain),
    )
    # Add triage/registry evidence without changing the accepted send state.
    legacy.update_master_fields(
        token,
        master_headers,
        row,
        {
            "Notes": legacy.append_note(
                row.get("Notes", ""),
                f"{_context_note(priority, score, muqawil)}; terminal=VERIFIED_RECEIVING_EMAIL; queue={queue_id}",
            )
        },
    )
    _checkpoint(
        checkpoints,
        checkpoint_path,
        master_id,
        state="staged",
        result="receiving_route_ready",
        queue_id=queue_id,
        email=email,
    )
    return current_queue


def _resolve_domain_routes(
    *,
    row: dict[str, str],
    domain: str,
) -> tuple[Any | None, dict[str, Any] | None, str]:
    official_url = str(row.get("Address_or_Website") or "").strip()
    if legacy.normalized_domain(official_url) != domain:
        official_url = f"https://{domain}/"
    try:
        route = legacy.discover_employment_route(official_url)
    except Exception as exc:
        return None, None, f"employment_route_error={type(exc).__name__}"
    if route is not None and getattr(route, "is_portal", False):
        return route, None, "portal"
    return route, _free_route_candidate(route), "email" if route is not None and getattr(route, "is_email", False) else "none"


def _selected_rows(master: list[dict[str, str]], *, reopen_unconfirmed: bool) -> list[dict[str, str]]:
    rows = [
        row for row in master
        if str(row.get("Record_Type") or "").strip() == "REGA_COMPANY_NO_EMAIL"
    ]
    selected: list[dict[str, str]] = []
    for row in rows:
        if is_terminal_resolution(row):
            status = str(row.get("Source_Status") or "").lower()
            if reopen_unconfirmed and "identity unconfirmed after automated rega resolution" in status:
                selected.append(row)
            continue
        selected.append(row)
    selected.sort(key=lambda row: (local_priority_key(row), legacy.row_rank(row)))
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Free-first full-universe REGA resolver")
    parser.add_argument("--apply", action="store_true", help="Required for Master Tracker / Send Queue writes")
    parser.add_argument("--allow-existing-credit", action="store_true", help="Permit bounded existing Outscraper credit; never purchase/top-up")
    parser.add_argument("--reserve-usd", type=float, default=legacy.DEFAULT_RESERVE_USD)
    parser.add_argument("--max-companies", type=int, default=0, help="0 means all current unresolved REGA rows")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--skip-muqawil", action="store_true", help="Skip free Muqawil identity/company-size evidence")
    parser.add_argument("--reopen-unconfirmed", action="store_true", help="Re-run prior IDENTITY_UNCONFIRMED terminal rows")
    args = parser.parse_args()

    if not args.apply:
        raise SystemExit("refusing Sheet/queue mutation without --apply")

    key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
    if args.allow_existing_credit and not key:
        raise SystemExit("--allow-existing-credit requires OUTSCRAPER_API_KEY")

    root = Path(args.root)
    checkpoint_path = root / "checkpoint.jsonl"
    summary_path = root / "summary.json"
    checkpoints = _load_checkpoints(checkpoint_path)

    token = legacy.rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
    master_headers, master = legacy.read_master(token)
    config = legacy.read_config(token)
    current_queue = legacy.read_queue(token, legacy.SPREADSHEET_ID)
    dedupe = legacy.read_dedupe_state(token, master, current_queue)
    known_emails: set[str] = set(dedupe["known_emails"])
    permanent_bounces: set[str] = set(dedupe["permanent_bounces"])
    contacted_companies: set[str] = set(dedupe["contacted_companies"])
    queued_companies: set[str] = set(dedupe["queued_companies"])

    universe = [r for r in master if str(r.get("Record_Type") or "").strip() == "REGA_COMPANY_NO_EMAIL"]
    selected = _selected_rows(master, reopen_unconfirmed=args.reopen_unconfirmed)
    limit = max(0, int(args.max_companies))
    if limit:
        selected = selected[:limit]

    client = legacy.OutscraperClient(key) if args.allow_existing_credit else None
    reserve = max(0.0, float(args.reserve_usd))
    start_balance = legacy.balance(client) if client is not None else None

    counts: dict[str, int] = {
        "universe": len(universe),
        "selected": len(selected),
        "priority_A": 0,
        "priority_B": 0,
        "priority_C": 0,
        "muqawil_profiles": 0,
        "company_already_contacted": 0,
        "company_already_covered": 0,
        "free_domains": 0,
        "free_portals": 0,
        "free_email_candidates": 0,
        "paid_maps": 0,
        "paid_domain_contacts": 0,
        "paid_validations": 0,
        "receiving_queued": 0,
        "resolved_no_route": 0,
        "identity_unconfirmed": 0,
        "mailbox_unvalidated": 0,
        "mailbox_not_receiving": 0,
        "paid_skipped_reserve": 0,
        "provider_failures": 0,
    }

    pending: list[dict[str, Any]] = []

    # Phase 1: complete the free pass for every selected company. This phase is
    # deliberately independent of Outscraper balance.
    for row in selected:
        master_id = str(row.get("Master_ID") or "").strip()
        if not master_id:
            continue
        prior = checkpoints.get(master_id) or {}
        if str(prior.get("state") or "") in FINAL_CHECKPOINT_STATES and not args.reopen_unconfirmed:
            continue

        company_key = legacy.normalized_company(row.get("Company_or_Office", ""))
        base_score = career_value_score(row)
        base_priority = priority_band(base_score)

        if company_key and company_key in contacted_companies:
            counts[f"priority_{base_priority}"] += 1
            counts["company_already_contacted"] += 1
            persist_company_covered(token, master_headers, row, result="successfully_contacted", priority=base_priority, score=base_score)
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="company_already_successfully_contacted", priority=base_priority, score=base_score)
            continue
        if company_key and company_key in queued_companies:
            counts[f"priority_{base_priority}"] += 1
            counts["company_already_covered"] += 1
            persist_company_covered(token, master_headers, row, result="active_or_sent_queue", priority=base_priority, score=base_score)
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="company_already_covered", priority=base_priority, score=base_score)
            continue
        if legacy.has_usable_nonemail_route(row):
            counts[f"priority_{base_priority}"] += 1
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="existing_evidenced_nonemail_route", priority=base_priority, score=base_score)
            continue

        muqawil: MuqawilProfile | None = None
        if not args.skip_muqawil:
            try:
                muqawil = discover_muqawil_profile(legacy.company_record(row))
            except Exception:
                muqawil = None
        score = career_value_score(row, muqawil=muqawil)
        priority = priority_band(score)
        counts[f"priority_{priority}"] += 1
        if muqawil is not None:
            counts["muqawil_profiles"] += 1

        domain, discovery = legacy.free_discover_domain(row)
        if domain:
            counts["free_domains"] += 1
            route, free_candidate, route_kind = _resolve_domain_routes(row=row, domain=domain)
            if route_kind == "portal" and route is not None:
                legacy.persist_portal_route(token, master_headers, row, domain, route)
                legacy.update_master_fields(
                    token,
                    master_headers,
                    row,
                    {"Notes": legacy.append_note(row.get("Notes", ""), f"{_context_note(priority, score, muqawil)}; terminal=VERIFIED_PORTAL")},
                )
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="free_first_party_portal_route", domain=domain, priority=priority, score=score, muqawil=asdict(muqawil) if muqawil else None)
                counts["free_portals"] += 1
                continue
            if free_candidate is not None:
                reason = legacy._blocked_candidate(free_candidate["email"], known_emails, permanent_bounces)
                if not reason:
                    counts["free_email_candidates"] += 1
                else:
                    free_candidate = None
            pending.append({
                "row": row,
                "domain": domain,
                "discovery": discovery,
                "candidate": free_candidate,
                "priority": priority,
                "score": score,
                "muqawil": muqawil,
                "route_detail": route_kind,
            })
            _checkpoint(checkpoints, checkpoint_path, master_id, state="free_ready", result="verified_domain_free_pass_complete", domain=domain, priority=priority, score=score, candidate=free_candidate, muqawil=asdict(muqawil) if muqawil else None)
            continue

        pending.append({
            "row": row,
            "domain": "",
            "discovery": discovery,
            "candidate": None,
            "priority": priority,
            "score": score,
            "muqawil": muqawil,
            "route_detail": "no_domain",
        })
        _checkpoint(checkpoints, checkpoint_path, master_id, state="free_ready", result="free_domain_not_verified", discovery=discovery, priority=priority, score=score, muqawil=asdict(muqawil) if muqawil else None)

    # Phase 2: paid fallback is bounded and optional. Reserve depletion is not a
    # loop breaker; later companies still receive terminal classifications.
    for item in pending:
        row = item["row"]
        master_id = str(row.get("Master_ID") or "").strip()
        domain = str(item.get("domain") or "")
        discovery = dict(item.get("discovery") or {})
        candidate = item.get("candidate")
        priority = str(item["priority"])
        score = int(item["score"])
        muqawil = item.get("muqawil")

        if not domain:
            if not args.allow_existing_credit or not _paid_available(client, reserve, legacy.MAPS_USD_UPPER_BOUND):
                counts["paid_skipped_reserve"] += 1
                counts["identity_unconfirmed"] += 1
                persist_identity_unconfirmed(
                    token,
                    master_headers,
                    row,
                    priority=priority,
                    score=score,
                    reason="free identity/domain discovery did not verify an official domain; paid Maps fallback unavailable or reserve-protected",
                    muqawil=muqawil,
                )
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="identity_unconfirmed_no_paid_capacity", priority=priority, score=score)
                continue

            _checkpoint(checkpoints, checkpoint_path, master_id, state="inflight", phase="outscraper_maps", priority=priority, score=score)
            try:
                domain, maps_detail = legacy.maps_discover_domain(client, row)
                counts["paid_maps"] += 1
            except Exception as exc:
                domain, maps_detail = "", {"basis": "outscraper_maps_exception", "provider_status": type(exc).__name__}
                counts["provider_failures"] += 1
            if not domain:
                counts["identity_unconfirmed"] += 1
                persist_identity_unconfirmed(
                    token,
                    master_headers,
                    row,
                    priority=priority,
                    score=score,
                    reason=f"no verified domain after free + bounded Maps lookup ({maps_detail.get('basis', 'no_match')})",
                    muqawil=muqawil,
                )
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="identity_unconfirmed_after_maps", discovery=maps_detail, priority=priority, score=score)
                continue

            route, free_candidate, route_kind = _resolve_domain_routes(row=row, domain=domain)
            if route_kind == "portal" and route is not None:
                legacy.persist_portal_route(token, master_headers, row, domain, route)
                legacy.update_master_fields(
                    token,
                    master_headers,
                    row,
                    {"Notes": legacy.append_note(row.get("Notes", ""), f"{_context_note(priority, score, muqawil)}; terminal=VERIFIED_PORTAL; domain_source=bounded_maps")},
                )
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="maps_domain_first_party_portal_route", domain=domain, priority=priority, score=score)
                counts["free_portals"] += 1
                continue
            candidate = free_candidate
            if candidate is not None:
                reason = legacy._blocked_candidate(candidate["email"], known_emails, permanent_bounces)
                if reason:
                    candidate = None

        # Prefer a published first-party mailbox over spending another provider
        # lookup. It still must pass the existing validator gate.
        if candidate is None:
            if not args.allow_existing_credit or not _paid_available(client, reserve, legacy.RECORD_USD_UPPER_BOUND):
                counts["paid_skipped_reserve"] += 1
                counts["resolved_no_route"] += 1
                persist_resolved_no_route(
                    token,
                    master_headers,
                    row,
                    domain=domain,
                    priority=priority,
                    score=score,
                    reason="verified domain but no free employment route; paid contact lookup unavailable or reserve-protected",
                    muqawil=muqawil,
                )
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="verified_domain_no_free_route_no_paid_capacity", domain=domain, priority=priority, score=score)
                continue

            _checkpoint(checkpoints, checkpoint_path, master_id, state="inflight", phase="outscraper_domain_contacts", domain=domain, priority=priority, score=score)
            try:
                provider_candidates = legacy.contact_candidates(client, domain)
                counts["paid_domain_contacts"] += 1
            except Exception as exc:
                provider_candidates = []
                counts["provider_failures"] += 1
                discovery["domain_contact_error"] = type(exc).__name__
            candidate, rejected = legacy.select_best_eligible_candidate(provider_candidates, known_emails, permanent_bounces)
            if candidate is None:
                counts["resolved_no_route"] += 1
                persist_resolved_no_route(
                    token,
                    master_headers,
                    row,
                    domain=domain,
                    priority=priority,
                    score=score,
                    reason="verified domain; no free portal and no eligible new company-domain mailbox after one bounded contact lookup",
                    muqawil=muqawil,
                )
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="verified_domain_no_eligible_contact", domain=domain, rejected=rejected, priority=priority, score=score)
                continue

        email = str(candidate.get("email") or "").strip().lower()
        source_url = _source_url(candidate, domain)
        if not args.allow_existing_credit or not _paid_available(client, reserve, legacy.RECORD_USD_UPPER_BOUND):
            counts["paid_skipped_reserve"] += 1
            counts["mailbox_unvalidated"] += 1
            persist_unvalidated_mailbox(
                token,
                master_headers,
                row,
                domain=domain,
                email=email,
                source_url=source_url,
                priority=priority,
                score=score,
                reason="validator unavailable or reserve-protected",
                muqawil=muqawil,
            )
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="official_mailbox_unvalidated", domain=domain, email=email, priority=priority, score=score)
            continue

        _checkpoint(checkpoints, checkpoint_path, master_id, state="inflight", phase="outscraper_email_validation", domain=domain, candidate=candidate, priority=priority, score=score)
        try:
            validation = legacy.validate_one(client, email)
            counts["paid_validations"] += 1
        except Exception as exc:
            validation = {"email": email, "status": "PROVIDER_FAILURE", "safe_to_send": False, "error": type(exc).__name__}
            counts["provider_failures"] += 1

        if str(validation.get("status") or "").upper() == "RECEIVING" and validation.get("safe_to_send"):
            current_queue = _stage_receiving(
                token=token,
                master_headers=master_headers,
                row=row,
                domain=domain,
                candidate=candidate,
                validation=validation,
                config=config,
                current_queue=current_queue,
                known_emails=known_emails,
                permanent_bounces=permanent_bounces,
                queued_companies=queued_companies,
                checkpoints=checkpoints,
                checkpoint_path=checkpoint_path,
                priority=priority,
                score=score,
                muqawil=muqawil,
            )
            counts["receiving_queued"] += 1
            continue

        counts["mailbox_not_receiving"] += 1
        persist_nonreceiving_mailbox(
            token,
            master_headers,
            row,
            domain=domain,
            email=email,
            source_url=source_url,
            validation=validation,
            priority=priority,
            score=score,
            muqawil=muqawil,
        )
        _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="official_mailbox_not_receiving", domain=domain, email=email, validation=validation, priority=priority, score=score)

    # Acceptance: every row selected by this run must now be explicitly final,
    # except pre-existing evidenced routes that were already final by authority.
    unfinished: list[str] = []
    for row in selected:
        master_id = str(row.get("Master_ID") or "").strip()
        if not master_id:
            continue
        state = str((checkpoints.get(master_id) or {}).get("state") or "")
        if state not in FINAL_CHECKPOINT_STATES:
            unfinished.append(master_id)
    if unfinished:
        raise SystemExit(f"REGA resolution v3 acceptance failed: unfinished={unfinished[:20]}")

    readback = legacy.read_queue(token, legacy.SPREADSHEET_ID)
    emails = [str(r.get("Email") or "").strip().lower() for r in readback if str(r.get("Email") or "").strip()]
    if len(emails) != len(set(emails)):
        raise SystemExit("post-run Send Queue email uniqueness invariant failed")
    if permanent_bounces.intersection(emails):
        raise SystemExit("post-run permanent-bounce invariant failed")

    end_balance = legacy.balance(client) if client is not None else None
    summary: dict[str, Any] = {
        "ok": True,
        "scope": "REGA_ONLY",
        "mode": "FREE_FIRST_FULL_UNIVERSE",
        **counts,
        "processed_final": len(selected),
        "unfinished": 0,
        "balance_before": start_balance,
        "balance_after": end_balance,
        "reserve_usd": reserve,
        "send_queue_rows_after": len(readback),
        "sends": 0,
        "gmail_drafts": 0,
        "purchases_or_topups": 0,
        "finished_at": legacy.utc_now(),
    }
    legacy.atomic_json(summary_path, summary)
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
