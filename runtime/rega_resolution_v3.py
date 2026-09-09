#!/usr/bin/env python3
"""Free-first full-universe REGA employer resolver.

The live Master Tracker remains the operational authority. This runtime first
uses free identity/domain discovery and verified first-party employment routes
across the whole selected REGA cohort, then optionally uses bounded existing
Outscraper credit. A protected provider reserve never stops later free work.

This file has no Gmail send/draft path, no application-submission path, and no
purchase/top-up path. Only first-party company-domain mailboxes that pass the
existing validator as RECEIVING can enter the existing Send Queue.
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
FINAL_STATES = {"resolved", "staged"}


def _checkpoint(store: dict[str, dict[str, Any]], path: Path, master_id: str, **payload: Any) -> None:
    record = dict(store.get(master_id) or {})
    record.update(payload)
    record.update(master_id=master_id, at=legacy.utc_now())
    store[master_id] = record
    legacy.atomic_jsonl(path, store)


def _context(priority: str, score: int, muqawil: MuqawilProfile | None) -> str:
    bits = [f"REGA resolution v3 {legacy.today()}: priority={priority}", f"score={score}"]
    if muqawil:
        bits.append(f"Muqawil={muqawil.url}")
        if muqawil.company_size:
            bits.append(f"Muqawil size={muqawil.company_size}")
        if muqawil.membership_number:
            bits.append(f"Muqawil membership={muqawil.membership_number}")
    return "; ".join(bits)


def _persist(
    token: str,
    headers: list[str],
    row: dict[str, str],
    updates: dict[str, str],
    note: str,
) -> None:
    payload = dict(updates)
    payload["Source_Date_or_Freshness"] = legacy.today()
    payload["Notes"] = legacy.append_note(row.get("Notes", ""), note)
    legacy.update_master_fields(token, headers, row, payload)


def persist_identity_unconfirmed(
    token: str,
    headers: list[str],
    row: dict[str, str],
    priority: str,
    score: int,
    reason: str,
    muqawil: MuqawilProfile | None,
) -> None:
    next_action = (
        "Priority-A manual identity review; reopen automated resolver when new registry/first-party evidence is available"
        if priority == "A"
        else "Reopen only when new registry/first-party identity evidence is available"
    )
    _persist(
        token,
        headers,
        row,
        {
            "Source_Status": "Identity unconfirmed after automated REGA resolution",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": next_action,
        },
        f"{_context(priority, score, muqawil)}; terminal=IDENTITY_UNCONFIRMED; reason={reason}",
    )


def persist_no_route(
    token: str,
    headers: list[str],
    row: dict[str, str],
    domain: str,
    priority: str,
    score: int,
    reason: str,
    muqawil: MuqawilProfile | None,
) -> None:
    _persist(
        token,
        headers,
        row,
        {
            "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
            "Source_Status": "Resolved - verified company; no usable employment route",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": "Closed automated route; revisit only when new first-party employment evidence appears",
        },
        f"{_context(priority, score, muqawil)}; terminal=RESOLVED_NO_EMPLOYMENT_ROUTE; reason={reason}",
    )


def persist_unvalidated_mailbox(
    token: str,
    headers: list[str],
    row: dict[str, str],
    domain: str,
    email: str,
    source_url: str,
    priority: str,
    score: int,
    reason: str,
    muqawil: MuqawilProfile | None,
) -> None:
    _persist(
        token,
        headers,
        row,
        {
            "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
            "Source_Status": "Resolved - official mailbox found; validation unavailable",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": "Hold exact mailbox until validator capacity is available; do not send blind",
        },
        f"{_context(priority, score, muqawil)}; terminal=MAILBOX_UNVALIDATED; email={email}; source={source_url}; reason={reason}; not queued",
    )


def persist_nonreceiving_mailbox(
    token: str,
    headers: list[str],
    row: dict[str, str],
    domain: str,
    email: str,
    source_url: str,
    validation: dict[str, Any],
    priority: str,
    score: int,
    muqawil: MuqawilProfile | None,
) -> None:
    status = str(validation.get("status") or "UNKNOWN")
    _persist(
        token,
        headers,
        row,
        {
            "Address_or_Website": str(row.get("Address_or_Website") or "").strip() or f"https://{domain}/",
            "Source_Status": f"Resolved - official mailbox not receiving ({status})",
            "Send_Eligibility": "NO_VERIFIED_EMAIL_ROUTE",
            "Next_Action": "Closed automated route; revisit only with a different verified official employment route",
        },
        f"{_context(priority, score, muqawil)}; terminal=MAILBOX_NOT_RECEIVING; email={email}; source={source_url}; validator={status}; not queued",
    )


def persist_company_covered(
    token: str,
    headers: list[str],
    row: dict[str, str],
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
    _persist(
        token,
        headers,
        row,
        {
            "Source_Status": status,
            "Send_Eligibility": "NO_ADDITIONAL_COMPANY_ROUTE_REQUIRED",
            "Next_Action": "No duplicate company outreach; await/review responses",
        },
        f"REGA resolution v3 {legacy.today()}: priority={priority}; score={score}; terminal=COMPANY_COVERED; {detail}",
    )


def _source_url(candidate: dict[str, Any], domain: str) -> str:
    urls = candidate.get("source_urls") or []
    return str(urls[0]) if urls else f"https://{domain}/"


def _free_email_candidate(route: Any) -> dict[str, Any] | None:
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


def _discover_route(row: dict[str, str], domain: str) -> tuple[Any | None, dict[str, Any] | None]:
    official_url = str(row.get("Address_or_Website") or "").strip()
    if legacy.normalized_domain(official_url) != domain:
        official_url = f"https://{domain}/"
    try:
        route = legacy.discover_employment_route(official_url)
    except Exception:
        return None, None
    return route, _free_email_candidate(route)


def _paid_available(client: Any | None, reserve: float, required: float) -> bool:
    if client is None:
        return False
    try:
        return legacy.balance(client) - reserve >= required
    except Exception:
        return False


def _selected(master: list[dict[str, str]], reopen_unconfirmed: bool) -> list[dict[str, str]]:
    rows = [r for r in master if str(r.get("Record_Type") or "").strip() == "REGA_COMPANY_NO_EMAIL"]
    out: list[dict[str, str]] = []
    for row in rows:
        if is_terminal_resolution(row):
            status = str(row.get("Source_Status") or "").lower()
            if reopen_unconfirmed and "identity unconfirmed after automated rega resolution" in status:
                out.append(row)
            continue
        out.append(row)
    out.sort(key=lambda row: (local_priority_key(row), legacy.row_rank(row)))
    return out


def _stage_receiving(
    token: str,
    headers: list[str],
    row: dict[str, str],
    domain: str,
    candidate: dict[str, Any],
    validation: dict[str, Any],
    config: dict[str, str],
    current_queue: list[dict[str, str]],
    known_emails: set[str],
    permanent_bounces: set[str],
    queued_companies: set[str],
    newly_queued_emails: set[str],
    checkpoints: dict[str, dict[str, Any]],
    checkpoint_path: Path,
    priority: str,
    score: int,
    muqawil: MuqawilProfile | None,
) -> tuple[list[dict[str, str]], bool]:
    master_id = str(row.get("Master_ID") or "").strip()
    email = str(validation.get("email") or candidate.get("email") or "").strip().lower()
    if not email or str(validation.get("status") or "").upper() != "RECEIVING" or not validation.get("safe_to_send"):
        raise RuntimeError(f"invalid RECEIVING validation for {master_id}")
    if email in permanent_bounces:
        raise RuntimeError(f"permanent-bounce mailbox blocked: {master_id} {email}")
    if email in known_emails:
        persist_company_covered(token, headers, row, "active_or_sent_queue", priority, score)
        _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="validated_email_became_known_before_write", email=email)
        return current_queue, False

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
    newly_queued_emails.add(email)
    company_key = legacy.normalized_company(row.get("Company_or_Office", ""))
    if company_key:
        queued_companies.add(company_key)
    legacy.persist_receiving_email(
        token,
        headers,
        row,
        domain,
        email,
        str(candidate.get("kind") or legacy.mailbox_rank(email)[1]),
        _source_url(candidate, domain),
    )
    legacy.update_master_fields(
        token,
        headers,
        row,
        {
            "Notes": legacy.append_note(
                row.get("Notes", ""),
                f"{_context(priority, score, muqawil)}; terminal=VERIFIED_RECEIVING_EMAIL; queue={queue_id}",
            )
        },
    )
    _checkpoint(checkpoints, checkpoint_path, master_id, state="staged", result="receiving_route_ready", queue_id=queue_id, email=email)
    return current_queue, True


def main() -> int:
    parser = argparse.ArgumentParser(description="Free-first full-universe REGA resolver")
    parser.add_argument("--apply", action="store_true", help="Required for Master Tracker / Send Queue writes")
    parser.add_argument("--allow-existing-credit", action="store_true", help="Permit bounded existing Outscraper credit; never buy/top-up")
    parser.add_argument("--reserve-usd", type=float, default=legacy.DEFAULT_RESERVE_USD)
    parser.add_argument("--max-companies", type=int, default=0, help="0 means all current unresolved REGA rows")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--skip-muqawil", action="store_true")
    parser.add_argument("--reopen-unconfirmed", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("refusing Sheet/queue mutation without --apply")

    key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
    if args.allow_existing_credit and not key:
        raise SystemExit("--allow-existing-credit requires OUTSCRAPER_API_KEY")

    root = Path(args.root)
    checkpoint_path = root / "checkpoint.jsonl"
    summary_path = root / "summary.json"
    checkpoints = legacy.load_jsonl(checkpoint_path)

    token = legacy.rclone_access_token(os.environ.get("RCLONE_GDRIVE_REMOTE", "gdrive"))
    headers, master = legacy.read_master(token)
    config = legacy.read_config(token)
    current_queue = legacy.read_queue(token, legacy.SPREADSHEET_ID)
    dedupe = legacy.read_dedupe_state(token, master, current_queue)
    known_emails = set(dedupe["known_emails"])
    permanent_bounces = set(dedupe["permanent_bounces"])
    contacted_companies = set(dedupe["contacted_companies"])
    queued_companies = set(dedupe["queued_companies"])
    newly_queued_emails: set[str] = set()

    universe = [r for r in master if str(r.get("Record_Type") or "").strip() == "REGA_COMPANY_NO_EMAIL"]
    selected = _selected(master, args.reopen_unconfirmed)
    if args.max_companies > 0:
        selected = selected[: args.max_companies]

    client = legacy.OutscraperClient(key) if args.allow_existing_credit else None
    reserve = max(0.0, float(args.reserve_usd))
    start_balance = legacy.balance(client) if client is not None else None
    counts = {
        "universe": len(universe), "selected": len(selected),
        "priority_A": 0, "priority_B": 0, "priority_C": 0,
        "muqawil_profiles": 0, "company_already_contacted": 0, "company_already_covered": 0,
        "free_domains": 0, "free_portals": 0, "free_email_candidates": 0,
        "paid_maps": 0, "paid_domain_contacts": 0, "paid_validations": 0,
        "receiving_queued": 0, "resolved_no_route": 0, "identity_unconfirmed": 0,
        "mailbox_unvalidated": 0, "mailbox_not_receiving": 0,
        "paid_skipped_reserve": 0, "provider_failures": 0,
    }
    pending: list[dict[str, Any]] = []

    # Recover only post-validation writes. Ambiguous prior paid operations are
    # never silently retried because that could consume credit twice.
    for row in selected:
        master_id = str(row.get("Master_ID") or "").strip()
        prior = checkpoints.get(master_id) or {}
        if str(prior.get("state") or "") == "ready_to_write":
            validation = dict(prior.get("validation") or {})
            candidate = dict(prior.get("candidate") or {})
            domain = str(prior.get("domain") or "")
            score = int(prior.get("score") or career_value_score(row))
            priority = str(prior.get("priority") or priority_band(score))
            current_queue, staged = _stage_receiving(
                token, headers, row, domain, candidate, validation, config, current_queue,
                known_emails, permanent_bounces, queued_companies, newly_queued_emails,
                checkpoints, checkpoint_path, priority, score, None,
            )
            counts["receiving_queued"] += int(staged)

    # Phase 1: FREE PASS across the selected universe. Provider balance cannot
    # stop this phase.
    for row in selected:
        master_id = str(row.get("Master_ID") or "").strip()
        if not master_id:
            continue
        prior = checkpoints.get(master_id) or {}
        if str(prior.get("state") or "") in FINAL_STATES and not args.reopen_unconfirmed:
            continue
        if str(prior.get("state") or "") == "inflight":
            # A prior paid operation may have happened before a crash. Do not
            # repeat it. Terminalize fail-closed and preserve evidence.
            score = int(prior.get("score") or career_value_score(row))
            priority = str(prior.get("priority") or priority_band(score))
            counts[f"priority_{priority}"] += 1
            counts["identity_unconfirmed"] += 1
            persist_identity_unconfirmed(token, headers, row, priority, score, f"ambiguous prior provider phase={prior.get('phase', 'unknown')}; not retried automatically", None)
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="ambiguous_provider_operation_not_retried")
            continue

        company_key = legacy.normalized_company(row.get("Company_or_Office", ""))
        base_score = career_value_score(row)
        base_priority = priority_band(base_score)
        if company_key and company_key in contacted_companies:
            counts[f"priority_{base_priority}"] += 1
            counts["company_already_contacted"] += 1
            persist_company_covered(token, headers, row, "successfully_contacted", base_priority, base_score)
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="company_already_successfully_contacted", priority=base_priority, score=base_score)
            continue
        if company_key and company_key in queued_companies:
            counts[f"priority_{base_priority}"] += 1
            counts["company_already_covered"] += 1
            persist_company_covered(token, headers, row, "active_or_sent_queue", base_priority, base_score)
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
        counts["muqawil_profiles"] += int(muqawil is not None)

        domain, discovery = legacy.free_discover_domain(row)
        candidate: dict[str, Any] | None = None
        if domain:
            counts["free_domains"] += 1
            route, candidate = _discover_route(row, domain)
            if route is not None and getattr(route, "is_portal", False):
                legacy.persist_portal_route(token, headers, row, domain, route)
                legacy.update_master_fields(token, headers, row, {"Notes": legacy.append_note(row.get("Notes", ""), f"{_context(priority, score, muqawil)}; terminal=VERIFIED_PORTAL")})
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="free_first_party_portal_route", domain=domain, priority=priority, score=score, muqawil=asdict(muqawil) if muqawil else None)
                counts["free_portals"] += 1
                continue
            if candidate is not None:
                reason = legacy._blocked_candidate(candidate["email"], known_emails, permanent_bounces)
                if reason:
                    candidate = None
                else:
                    counts["free_email_candidates"] += 1

        pending.append({
            "row": row, "domain": domain, "discovery": discovery, "candidate": candidate,
            "priority": priority, "score": score, "muqawil": muqawil,
        })
        _checkpoint(
            checkpoints, checkpoint_path, master_id, state="free_ready",
            result="verified_domain_free_pass_complete" if domain else "free_domain_not_verified",
            domain=domain, discovery=discovery, candidate=candidate, priority=priority, score=score,
            muqawil=asdict(muqawil) if muqawil else None,
        )

    # Phase 2: optional bounded paid fallback. Lack of credit/reserve is a
    # per-company terminal outcome, never a reason to break the loop.
    for item in pending:
        row = item["row"]
        master_id = str(row.get("Master_ID") or "").strip()
        domain = str(item.get("domain") or "")
        candidate = item.get("candidate")
        priority = str(item["priority"])
        score = int(item["score"])
        muqawil = item.get("muqawil")

        if not domain:
            if not args.allow_existing_credit or not _paid_available(client, reserve, legacy.MAPS_USD_UPPER_BOUND):
                counts["paid_skipped_reserve"] += 1
                counts["identity_unconfirmed"] += 1
                persist_identity_unconfirmed(token, headers, row, priority, score, "free discovery did not verify an official domain; bounded Maps fallback unavailable/reserve-protected", muqawil)
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="identity_unconfirmed_no_paid_capacity", priority=priority, score=score)
                continue
            _checkpoint(checkpoints, checkpoint_path, master_id, state="inflight", phase="outscraper_maps", priority=priority, score=score)
            try:
                domain, detail = legacy.maps_discover_domain(client, row)
                counts["paid_maps"] += 1
            except Exception as exc:
                domain, detail = "", {"basis": "provider_failure", "error": type(exc).__name__}
                counts["provider_failures"] += 1
            if not domain:
                counts["identity_unconfirmed"] += 1
                persist_identity_unconfirmed(token, headers, row, priority, score, f"no verified domain after bounded Maps fallback ({detail.get('basis', 'no_match')})", muqawil)
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="identity_unconfirmed_after_maps", discovery=detail, priority=priority, score=score)
                continue
            route, candidate = _discover_route(row, domain)
            if route is not None and getattr(route, "is_portal", False):
                legacy.persist_portal_route(token, headers, row, domain, route)
                legacy.update_master_fields(token, headers, row, {"Notes": legacy.append_note(row.get("Notes", ""), f"{_context(priority, score, muqawil)}; terminal=VERIFIED_PORTAL; domain_source=bounded_maps")})
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="maps_domain_first_party_portal_route", domain=domain, priority=priority, score=score)
                counts["free_portals"] += 1
                continue
            if candidate is not None and legacy._blocked_candidate(candidate["email"], known_emails, permanent_bounces):
                candidate = None

        if candidate is None:
            if not args.allow_existing_credit or not _paid_available(client, reserve, legacy.RECORD_USD_UPPER_BOUND):
                counts["paid_skipped_reserve"] += 1
                counts["resolved_no_route"] += 1
                persist_no_route(token, headers, row, domain, priority, score, "verified domain but no free employment route; bounded contact lookup unavailable/reserve-protected", muqawil)
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="verified_domain_no_free_route_no_paid_capacity", domain=domain, priority=priority, score=score)
                continue
            _checkpoint(checkpoints, checkpoint_path, master_id, state="inflight", phase="outscraper_domain_contacts", domain=domain, priority=priority, score=score)
            try:
                provider_candidates = legacy.contact_candidates(client, domain)
                counts["paid_domain_contacts"] += 1
            except Exception as exc:
                provider_candidates = []
                counts["provider_failures"] += 1
            candidate, rejected = legacy.select_best_eligible_candidate(provider_candidates, known_emails, permanent_bounces)
            if candidate is None:
                counts["resolved_no_route"] += 1
                persist_no_route(token, headers, row, domain, priority, score, "verified domain; no free portal and no eligible new company-domain mailbox after one bounded lookup", muqawil)
                _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="verified_domain_no_eligible_contact", domain=domain, rejected=rejected, priority=priority, score=score)
                continue

        email = str(candidate.get("email") or "").strip().lower()
        source_url = _source_url(candidate, domain)
        if not args.allow_existing_credit or not _paid_available(client, reserve, legacy.RECORD_USD_UPPER_BOUND):
            counts["paid_skipped_reserve"] += 1
            counts["mailbox_unvalidated"] += 1
            persist_unvalidated_mailbox(token, headers, row, domain, email, source_url, priority, score, "validator unavailable/reserve-protected", muqawil)
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="official_mailbox_unvalidated", domain=domain, email=email, priority=priority, score=score)
            continue

        _checkpoint(checkpoints, checkpoint_path, master_id, state="inflight", phase="outscraper_email_validation", domain=domain, candidate=candidate, priority=priority, score=score)
        try:
            validation = legacy.validate_one(client, email)
            counts["paid_validations"] += 1
        except Exception as exc:
            counts["provider_failures"] += 1
            counts["mailbox_unvalidated"] += 1
            persist_unvalidated_mailbox(token, headers, row, domain, email, source_url, priority, score, f"validator provider failure={type(exc).__name__}", muqawil)
            _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="validator_provider_failure", domain=domain, email=email, priority=priority, score=score)
            continue

        if str(validation.get("status") or "").upper() == "RECEIVING" and validation.get("safe_to_send"):
            current_queue, staged = _stage_receiving(
                token, headers, row, domain, candidate, validation, config, current_queue,
                known_emails, permanent_bounces, queued_companies, newly_queued_emails,
                checkpoints, checkpoint_path, priority, score, muqawil,
            )
            counts["receiving_queued"] += int(staged)
            continue

        counts["mailbox_not_receiving"] += 1
        persist_nonreceiving_mailbox(token, headers, row, domain, email, source_url, validation, priority, score, muqawil)
        _checkpoint(checkpoints, checkpoint_path, master_id, state="resolved", result="official_mailbox_not_receiving", domain=domain, email=email, validation=validation, priority=priority, score=score)

    unfinished = []
    for row in selected:
        master_id = str(row.get("Master_ID") or "").strip()
        if master_id and str((checkpoints.get(master_id) or {}).get("state") or "") not in FINAL_STATES:
            unfinished.append(master_id)
    if unfinished:
        raise SystemExit(f"REGA resolution v3 acceptance failed: unfinished={unfinished[:20]}")

    readback = legacy.read_queue(token, legacy.SPREADSHEET_ID)
    readback_emails = [str(r.get("Email") or "").strip().lower() for r in readback if str(r.get("Email") or "").strip()]
    if len(readback_emails) != len(set(readback_emails)):
        raise SystemExit("post-run Send Queue email uniqueness invariant failed")
    if permanent_bounces.intersection(newly_queued_emails):
        raise SystemExit("post-run newly queued permanent-bounce invariant failed")

    end_balance = legacy.balance(client) if client is not None else None
    summary = {
        "ok": True,
        "scope": "REGA_ONLY",
        "mode": "FREE_FIRST_FULL_UNIVERSE",
        **counts,
        "processed_final": len(selected),
        "unfinished": 0,
        "newly_queued_emails": len(newly_queued_emails),
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
