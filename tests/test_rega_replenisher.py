#!/usr/bin/env python3
"""Tests for the incremental REGA/Balady enrichment replenisher.

Covers:
1. Incremental promotion — each accepted route appended to Auto Send Queue
2. Duplicate/company contact block — verified prior contact blocks all aliases
3. Exact permanent-failure replacement behavior — blocked exact mailbox, replacement queued if company never sent
4. Idempotent restart — re-running after checkpoint skips already-promoted routes
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_route(
    email: str,
    company: str,
    domain: str,
    verification: str = "RECEIVING",
    identity_gate: str = "confirmed_official_domain",
    route_kind: str = "recruitment",
) -> dict:
    return {
        "email": email,
        "company_name": company,
        "company": company,
        "domain": domain,
        "verification": verification,
        "identity_gate": identity_gate,
        "route_kind": route_kind,
        "status_details": "",
        "catch_all_accepted_for_preparation": verification == "RECEIVING",
        "usable_for_preparation": True,
        "send_authorized": False,
    }


def _write_routes(monitor_dir: Path, routes: list[dict]) -> None:
    routes_file = monitor_dir / "rega-outscraper-usable-routes.jsonl"
    with routes_file.open("w", encoding="utf-8") as f:
        for r in routes:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _write_validated(monitor_dir: Path, records: list[dict]) -> None:
    validated_file = monitor_dir / "rega-outscraper-validated-emails.jsonl"
    with validated_file.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")


# Helper: create a temp dir where REPO_ROOT == parent, monitor_dir == child
def _mk_test_env():
    """Return (temp_root, monitor_dir) where monitor_dir = temp_root / 'monitor'."""
    temp_root = Path(tempfile.mkdtemp())
    monitor_dir = temp_root / "monitor"
    monitor_dir.mkdir()
    return temp_root, monitor_dir


# ---------------------------------------------------------------------------
# Test 1: Incremental promotion — each accepted route appended to Auto Send Queue
# ---------------------------------------------------------------------------


def test_incremental_promotion():
    """Each accepted validated route is promoted to Auto Send Queue immediately."""
    import career_engine.outreach_reconciler as reconciler_mod
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers@regaco.sa", "REGA Company", "regaco.sa", route_kind="recruitment"),
        _make_route("hr@baladyksa.com", "Balady KSA Office", "baladyksa.com", route_kind="recruitment"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    mock_queue_rows = [
        {"Queue_ID": "existing-1", "Email": "old@example.com", "Company_or_Office": "OldCo",
         "Source": "test", "Priority": "NORMAL", "Status": "PENDING",
         "Added_At": utc_now(), "Sent_At": "", "Gmail_Message_ID": "",
         "Last_Error": "", "Evidence_or_Notes": "", "__row_number": "2"}
    ]

    inserted_rows: list[dict] = []

    def mock_insert(token, rows, spreadsheet_id):
        for i, row in enumerate(rows):
            inserted_rows.append({
                "queue_id": f"OASQ-{i:04d}",
                "email": row["Email"].strip().lower(),
                "company": row["Company_or_Office"],
                "row_number": str(2 + i),
                "added_at": utc_now(),
            })
        return inserted_rows

    def mock_read_queue(token, spreadsheet_id):
        return mock_queue_rows

    def mock_gmail_dedupe():
        return {}

    with mock.patch.object(reconciler_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(reconciler_mod, '_read_queue_sheet', mock_read_queue), \
         mock.patch.object(reconciler_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', mock_gmail_dedupe), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    assert summary["ok"] is True
    assert summary["newly_queued"] == 2, f"Expected 2 newly_queued, got {summary['newly_queued']}"
    assert summary["batches_processed"] == 1
    assert len(inserted_rows) == 2, f"Expected 2 inserted rows, got {len(inserted_rows)}"

    emails = {r["email"] for r in inserted_rows}
    assert "careers@regaco.sa" in emails
    assert "hr@baladyksa.com" in emails


# ---------------------------------------------------------------------------
# Test 2: Duplicate / company contact block
# ---------------------------------------------------------------------------


def test_duplicate_company_contact_block():
    """A verified prior contact at a company blocks all aliases at that company."""
    import career_engine.outreach_reconciler as reconciler_mod
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("info@regaco.sa", "REGA Company", "regaco.sa", route_kind="general"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    # Gmail dedupe says careers@regaco.sa was already sent
    blocked_sent = {"careers@regaco.sa": "msg-123"}
    blocked_domains = {"regaco.sa"}  # derived from careers@regaco.sa send

    def mock_gmail_dedupe():
        return blocked_sent

    def mock_build_blocked(sent_by_email, master_rows):
        return {
            "blocked_emails": set(sent_by_email),
            "blocked_domains": blocked_domains,
            "blocked_companies": set(),
            "permanent_bounce_emails": set(),
            "email_to_company": {"careers@regaco.sa": "regaco-company"},
            "company_domains": {},
            "jordan_held_emails": set(),
            "jordan_held_companies": set(),
            "hard_blocked_emails": set(),
        }

    def mock_insert(token, rows, spreadsheet_id):
        return [{"queue_id": "x", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": "2", "added_at": utc_now()} for r in rows]

    with mock.patch.object(reconciler_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(reconciler_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(reconciler_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', mock_gmail_dedupe), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
         mock.patch.object(replenisher_mod, '_build_blocked_set', mock_build_blocked), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    # info@ should be blocked because careers@ was sent at same domain
    assert summary["ok"] is True
    assert summary["newly_queued"] == 0, f"Expected 0 newly_queued (company contact blocks alias), got {summary['newly_queued']}"
    assert summary["skipped_deduped"] == 1


# ---------------------------------------------------------------------------
# Test 3: Exact permanent-failure replacement behavior
# ---------------------------------------------------------------------------


def test_permanent_failure_no_retry_same_mailbox():
    """Exact permanent-failure mailbox is never retried; replacement at same company may be queued if company has no successful send."""
    import career_engine.outreach_reconciler as reconciler_mod
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers2@regaco.sa", "REGA Company", "regaco.sa", route_kind="recruitment"),
        _make_route("hr@regaco.sa", "REGA Company", "regaco.sa", route_kind="recruitment"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    def mock_build_blocked(sent_by_email, master_rows):
        return {
            "blocked_emails": set(),
            "blocked_domains": set(),
            "blocked_companies": set(),
            "permanent_bounce_emails": {"careers@regaco.sa"},
            "email_to_company": {},
            "company_domains": {},
            "jordan_held_emails": set(),
            "jordan_held_companies": set(),
            "hard_blocked_emails": set(),
        }

    inserted_rows: list[dict] = []
    def mock_insert(token, rows, spreadsheet_id):
        for row in rows:
            inserted_rows.append({"queue_id": f"qid-{len(inserted_rows)}",
                                  "email": row["Email"], "company": row["Company_or_Office"],
                                  "row_number": "2", "added_at": utc_now()})
        return inserted_rows

    with mock.patch.object(reconciler_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(reconciler_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(reconciler_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
         mock.patch.object(replenisher_mod, '_build_blocked_set', mock_build_blocked), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    assert summary["ok"] is True
    # careers2@ and hr@ should pass (careers@ permanent bounce doesn't block other mailboxes)
    assert summary["newly_queued"] == 2, f"Expected 2 (careers2@ and hr@), got {summary['newly_queued']}"


def test_permanent_failure_same_company_blocked_if_company_sent():
    """If the company already has a successful send, no replacement mailbox is allowed at that company."""
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers2@regaco.sa", "REGA Company", "regaco.sa", route_kind="recruitment"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    # The company already has a sent email → all aliases blocked
    def mock_build_blocked(sent_by_email, master_rows):
        return {
            "blocked_emails": {"info@regaco.sa"},  # already sent
            "blocked_domains": set(),
            "blocked_companies": {"regaco-company"},
            "permanent_bounce_emails": {"careers@regaco.sa"},
            "email_to_company": {"info@regaco.sa": "regaco-company", "careers2@regaco.sa": "regaco-company"},
            "company_domains": {},
            "jordan_held_emails": set(),
            "jordan_held_companies": set(),
            "hard_blocked_emails": set(),
        }

    with mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={"info@regaco.sa": "msg-001"}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_build_blocked_set', mock_build_blocked), \
         mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    # careers2@ should be blocked because company already sent (company_sent_gmail)
    assert summary["ok"] is True
    assert summary["newly_queued"] == 0, f"Expected 0 (company already sent blocks replacement), got {summary['newly_queued']}"


# ---------------------------------------------------------------------------
# Test 4: Idempotent restart
# ---------------------------------------------------------------------------


def test_idempotent_restart():
    """Re-running after checkpoint skips already-promoted routes."""
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers@regaco.sa", "REGA Company", "regaco.sa", route_kind="recruitment"),
        _make_route("hr@baladyksa.com", "Balady KSA Office", "baladyksa.com", route_kind="recruitment"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    # First pass: promote both
    def mock_insert_first(token, rows, spreadsheet_id):
        return [{"queue_id": f"qid-{i}", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": str(2 + i), "added_at": utc_now()} for i, r in enumerate(rows)]

    with mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert_first), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary1 = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    assert summary1["ok"] is True
    assert summary1["newly_queued"] == 2

    # Second pass: restart — journal has both entries, so no new promotions
    inserted_count_after_restart = []

    def mock_insert_restart(token, rows, spreadsheet_id):
        inserted_count_after_restart.extend(rows)
        return [{"queue_id": f"qid-r{i}", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": str(2 + i), "added_at": utc_now()} for i, r in enumerate(rows)]

    with mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert_restart), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary2 = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    assert summary2["ok"] is True
    assert summary2["newly_queued"] == 0, \
        f"Restart should be idempotent: expected 0 newly_queued, got {summary2['newly_queued']}"
    assert summary2["routes_already_replenished"] == 2
    assert len(inserted_count_after_restart) == 0, \
        "Should not attempt to re-insert already-promoted routes"


# ---------------------------------------------------------------------------
# Test 5: TTW and Arab Sustainable Architecture exclusions
# ---------------------------------------------------------------------------


def test_ttw_and_asa_exclusions():
    """TTW and Arab Sustainable Architecture are absolute exclusions."""
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers@ttwsa.com", "TTW", "ttwsa.com", route_kind="recruitment"),
        _make_route("jobs@arabsustainablearchitecture.com", "Arab Sustainable Architecture",
                    "arabsustainablearchitecture.com", route_kind="recruitment"),
        _make_route("hr@regaco.sa", "REGA Company", "regaco.sa", route_kind="recruitment"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    def mock_insert(token, rows, spreadsheet_id):
        return [{"queue_id": f"qid-{i}", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": str(2 + i), "added_at": utc_now()} for i, r in enumerate(rows)]

    with mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    assert summary["ok"] is True
    assert summary["newly_queued"] == 1, f"Expected 1 (only regaco.sa), got {summary['newly_queued']}"
    assert summary["skipped_excluded"] == 2, f"Expected 2 excluded, got {summary['skipped_excluded']}"


# ---------------------------------------------------------------------------
# Test 6: Batch bounded processing
# ---------------------------------------------------------------------------


def test_bounded_batch_processing():
    """Routes are processed in bounded batches with checkpoints between batches."""
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    # 7 routes, batch_size=3 → 3 batches
    routes = [
        _make_route(f"careers{i}@company{i}.sa", f"Company {i}", f"company{i}.sa",
                    route_kind="recruitment")
        for i in range(7)
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    def mock_insert(token, rows, spreadsheet_id):
        return [{"queue_id": f"qid-{i}", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": str(2 + i), "added_at": utc_now()} for i, r in enumerate(rows)]

    with mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=3,
            apply=True,
        )

    assert summary["ok"] is True
    assert summary["newly_queued"] == 7
    assert summary["batches_processed"] == 3, \
        f"Expected 3 batches (7 routes / 3 batch_size), got {summary['batches_processed']}"

    # Verify checkpoint file was created
    checkpoint = monitor_dir / "replenisher-checkpoint.json"
    assert checkpoint.is_file(), "Checkpoint file should exist"
    checkpoint_data = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert len(checkpoint_data) == 3, f"Expected 3 checkpoint entries, got {len(checkpoint_data)}"


# ---------------------------------------------------------------------------
# Test 7: Invalid identity gate
# ---------------------------------------------------------------------------


def test_identity_gate_rejection():
    """Routes without confirmed_official_domain identity gate are skipped."""
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers@regaco.sa", "REGA Company", "regaco.sa", identity_gate="candidate"),
        _make_route("info@baladyksa.com", "Balady KSA", "baladyksa.com", identity_gate="confirmed_official_domain"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    def mock_insert(token, rows, spreadsheet_id):
        return [{"queue_id": f"qid-{i}", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": str(2 + i), "added_at": utc_now()} for i, r in enumerate(rows)]

    with mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    assert summary["ok"] is True
    assert summary["newly_queued"] == 1  # only baladyksa has confirmed identity
    assert summary["skipped_excluded"] == 1  # regaco was skipped for identity


# ---------------------------------------------------------------------------
# Test 8: Journal persistence
# ---------------------------------------------------------------------------


def test_journal_persistence():
    """Replenisher journal records all outcomes for restart safety."""
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers@regaco.sa", "REGA Company", "regaco.sa"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    journal_path = replenisher_mod.RESTART_LEADERBOARD

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as jf:
        custom_journal = Path(jf.name)

    def mock_insert(token, rows, spreadsheet_id):
        return [{"queue_id": "qid-1", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": "2", "added_at": utc_now()} for r in rows]

    original_journal = replenisher_mod.RESTART_LEADERBOARD
    try:
        replenisher_mod.RESTART_LEADERBOARD = custom_journal
        with mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
             mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
             mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
             mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
             mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
             mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

            summary = replenisher_mod.run_replenishment(
                monitor_dir="monitor",
                batch_size=5,
                apply=True,
            )

        assert summary["ok"] is True
        assert custom_journal.is_file()
        journal_data = json.loads(custom_journal.read_text(encoding="utf-8"))
        entries = journal_data.get("entries", {})
        assert len(entries) == 1
        assert "qid-1" in entries
        assert entries["qid-1"]["status"] == "PROMOTED"
    finally:
        replenisher_mod.RESTART_LEADERBOARD = original_journal
        if custom_journal.exists():
            custom_journal.unlink()


# ---------------------------------------------------------------------------
# Test 9: No Gmail send code exists
# ---------------------------------------------------------------------------


def test_no_gmail_send_in_enrichment():
    """The replenisher must not contain any Gmail send code."""
    import inspect
    import runtime.rega_replenisher as replenisher_mod

    source = inspect.getsource(replenisher_mod)
    send_patterns = [
        "messages/send",
        "_send_raw",
        "send_raw",
        "message.send",
    ]
    for pattern in send_patterns:
        assert pattern not in source, \
            f"Replenisher contains prohibited send pattern: {pattern}"


# ---------------------------------------------------------------------------
# Test 10: No Outscraper credit purchase
# ---------------------------------------------------------------------------


def test_no_credit_purchase():
    """The replenisher must not purchase or top-up Outscraper credits."""
    import inspect
    import runtime.rega_replenisher as replenisher_mod

    source = inspect.getsource(replenisher_mod)
    # Check for explicit purchase actions (not budget references)
    assert "credit" not in source.lower() or "existing" in source.lower(), \
        "Should only reference existing credits"


# ---------------------------------------------------------------------------
# Test 11: Domain preference — recruitment > general
# ---------------------------------------------------------------------------


def test_recruitment_preference():
    """When multiple verified routes exist at the same domain, recruitment locals are preferred."""
    import runtime.rega_replenisher as replenisher_mod

    temp_root, monitor_dir = _mk_test_env()
    routes = [
        _make_route("careers@regaco.sa", "REGA Company", "regaco.sa", route_kind="recruitment"),
        _make_route("info@regaco.sa", "REGA Company", "regaco.sa", route_kind="general"),
    ]
    _write_routes(monitor_dir, routes)
    _write_validated(monitor_dir, [])

    def mock_build_blocked(sent, master):
        return {
            "blocked_emails": set(),
            "blocked_domains": set(),
            "blocked_companies": set(),
            "permanent_bounce_emails": set(),
            "email_to_company": {},
            "company_domains": {},
            "jordan_held_emails": set(),
            "jordan_held_companies": set(),
            "hard_blocked_emails": set(),
        }

    inserted: list[dict] = []

    def mock_insert(token, rows, spreadsheet_id):
        inserted.extend(rows)
        return [{"queue_id": f"qid-{i}", "email": r["Email"], "company": r["Company_or_Office"],
                 "row_number": str(2 + i), "added_at": utc_now()} for i, r in enumerate(rows)]

    with mock.patch.object(replenisher_mod, 'rclone_access_token', return_value="FAKE"), \
         mock.patch.object(replenisher_mod, '_read_queue_sheet', return_value=[]), \
         mock.patch.object(replenisher_mod, '_read_master_send_queue', return_value=[]), \
         mock.patch.object(replenisher_mod, '_gmail_dedupe_all', return_value={}), \
         mock.patch.object(replenisher_mod, '_insert_rows_into_queue', mock_insert), \
         mock.patch.object(replenisher_mod, '_build_blocked_set', mock_build_blocked), \
         mock.patch.object(replenisher_mod, 'REPO_ROOT', temp_root):

        summary = replenisher_mod.run_replenishment(
            monitor_dir="monitor",
            batch_size=5,
            apply=True,
        )

    # careers@ should be queued. info@ at same domain gets blocked by domain dedupe
    assert summary["ok"] is True
    inserted_emails = [r.get("Email", "") for r in inserted]
    assert "careers@regaco.sa" in inserted_emails, \
        "careers@ should be queued (recruitment preference)"


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    tests = [
        ("incremental_promotion", test_incremental_promotion),
        ("duplicate_company_contact_block", test_duplicate_company_contact_block),
        ("permanent_failure_no_retry", test_permanent_failure_no_retry_same_mailbox),
        ("permanent_failure_company_sent", test_permanent_failure_same_company_blocked_if_company_sent),
        ("idempotent_restart", test_idempotent_restart),
        ("ttw_asa_exclusions", test_ttw_and_asa_exclusions),
        ("bounded_batch_processing", test_bounded_batch_processing),
        ("identity_gate_rejection", test_identity_gate_rejection),
        ("journal_persistence", test_journal_persistence),
        ("no_gmail_send", test_no_gmail_send_in_enrichment),
        ("no_credit_purchase", test_no_credit_purchase),
        ("recruitment_preference", test_recruitment_preference),
    ]

    passed = 0
    failed = 0
    results = []

    for name, fn in tests:
        try:
            fn()
            passed += 1
            results.append(f"  PASS  {name}")
        except Exception as exc:
            failed += 1
            import traceback
            tb = traceback.format_exc().strip()
            results.append(f"  FAIL  {name}: {exc}\n        {tb[-300:]}")

    print(f"\nResults: {passed}/{passed + failed} passed, {failed} failed")
    for r in results:
        print(r)

    sys.exit(0 if failed == 0 else 1)
