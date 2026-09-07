#!/usr/bin/env python3
"""Tests for career_engine.incremental_outreach_replenisher.

Covers all required scenarios:
1. Gmail failure fail-closed
2. Exact duplicate blocked
3. Successful same-company prior-contact block
4. Exact permanent failed mailbox blocked
5. Different verified replacement accepted if company never successfully contacted
6. Repeated run idempotent
7. Two batches do not reinsert first batch
8. TTW/ASA/Jordan
9. Inappropriate mailbox
10. Sheets write readback mismatch fails closed
11. Per-batch buffer cleared after append
12. Replacement_of note propagated correctly

Also covers existing central sender/reconciler regression via imports.
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

import career_engine.incremental_outreach_replenisher as replenisher
import career_engine.outreach_reconciler as reconciler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _write_candidates(path: Path, records: list[dict]) -> None:
    """Write JSONL candidate records."""
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True) + "\n")


def _make_candidate(
    email: str,
    company: str = "",
    domain: str = "",
    verification: str = "RECEIVING",
    identity_gate: str = "confirmed_official_domain",
    route_kind: str = "general",
    source: str = "test_enrichment",
    replacement_of: str = "",
    evidence: str = "",
) -> dict:
    """Build a candidate record matching the expected schema."""
    return {
        "email": email,
        "company_name": company,
        "company": company,
        "domain": domain or (email.rsplit("@", 1)[1] if "@" in email else ""),
        "verification": verification,
        "identity_gate": identity_gate,
        "route_kind": route_kind,
        "source": source,
        "replacement_of": replacement_of,
        "evidence": evidence,
        "status_details": "",
        "queue_id": "",
    }


def _mk_temp_env():
    """Create a temp directory tree with monitor subdirectory."""
    temp_root = Path(tempfile.mkdtemp())
    monitor_dir = temp_root / "monitor"
    monitor_dir.mkdir()
    return temp_root, monitor_dir


# Common patches for apply=True: mock token + sheet reads so we can reach the
# dedupe logic.  Patches MUST go on the *replenisher* module because the
# module does ``from career_engine.outreach_reconciler import X`` which creates
# a local binding — patching the source module does not affect it.
_BASE_APPLY_PATCHES = [
    mock.patch.object(replenisher, "rclone_access_token", return_value="FAKE"),
    mock.patch.object(replenisher, "_read_queue_sheet", return_value=[]),
    mock.patch.object(replenisher, "_read_master_send_queue", return_value=[]),
]


# ---------------------------------------------------------------------------
# 1. Gmail failure fail-closed
# ---------------------------------------------------------------------------


def test_gmail_fail_closed_on_auth_failure():
    """Gmail census must fail closed on auth failure — never continue with empty dedupe."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("test@newco.sa", "NewCo", "newco.sa")]

    patches = [
        mock.patch.object(reconciler, "rclone_access_token", return_value="FAKE"),
        mock.patch.object(reconciler, "_read_queue_sheet", return_value=[]),
        mock.patch.object(reconciler, "_read_master_send_queue", return_value=[]),
        # Patch the LOCAL binding in the replenisher module
        mock.patch.object(replenisher, "verify_both_accounts_available", return_value=(False, "primary unavailable")),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["ok"] is False, "Should fail closed on Gmail auth failure"
    assert "FAIL_CLOSED" in result.get("error", ""), "Error must indicate fail-closed"
    assert result["skipped_gmail_fail"] == len(candidates), "All candidates must be counted as Gmail-fail"


def test_gmail_fail_closed_on_census_runtime_error():
    """If census encounters a RuntimeError during enumeration, it must fail closed."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("test@newco.sa", "NewCo", "newco.sa")]

    patches = [
        mock.patch.object(reconciler, "rclone_access_token", return_value="FAKE"),
        mock.patch.object(reconciler, "_read_queue_sheet", return_value=[]),
        mock.patch.object(reconciler, "_read_master_send_queue", return_value=[]),
        mock.patch.object(replenisher, "verify_both_accounts_available", return_value=(True, "ok")),
        # Patch gmail_dedupe_fail_closed to raise
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", side_effect=RuntimeError("network timeout")),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["ok"] is False, "Should fail closed on census RuntimeError"
    assert "FAIL_CLOSED" in result.get("error", ""), "Error must indicate fail-closed"


# ---------------------------------------------------------------------------
# 2. Exact duplicate blocked
# ---------------------------------------------------------------------------


def test_exact_duplicate_blocked():
    """An exact duplicate email already in the queue must be blocked."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("test@newco.sa", "NewCo", "newco.sa")]

    existing_queue = [
        {"Email": "test@newco.sa", "Company_or_Office": "NewCo",
         "Source": "test", "Priority": "NORMAL", "Status": "PENDING",
         "Added_At": utc_now(), "Sent_At": "", "Gmail_Message_ID": "",
         "Last_Error": "", "Evidence_or_Notes": "", "__row_number": "2"},
    ]

    inserted = []

    def mock_insert(token, rows, spreadsheet_id):
        for row in rows:
            inserted.append(row)
        email_val = inserted[-1]["Email"] if inserted else "placeholder"
        company_val = inserted[-1].get("Company_or_Office", "") if inserted else ""
        return [{"queue_id": f"qid-{len(inserted)}", "email": email_val,
                 "company": company_val, "row_number": "3", "added_at": utc_now()}]

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(reconciler, "_read_queue_sheet", return_value=existing_queue),
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["newly_queued"] == 0, "Exact duplicate must not be queued"
    assert result["skipped_deduped"] >= 1, "Duplicate must be counted as deduped"


# ---------------------------------------------------------------------------
# 3. Successful same-company prior-contact block
# ---------------------------------------------------------------------------


def test_successful_same_company_prior_contact_block():
    """If a successful contact exists at a company, all aliases at that company are blocked."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("info@newco.sa", "NewCo", "newco.sa")]

    sent_by = {"careers@newco.sa": "msg-123"}
    master_rows = [{"Email": "careers@newco.sa", "Company_or_Office": "NewCo",
                    "Send_State": "", "Terminal_Outcome": "", "Notes": ""}]

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(reconciler, "_read_master_send_queue", return_value=master_rows),
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value=sent_by),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["newly_queued"] == 0, "Same-company prior contact must block all aliases"
    assert result["skipped_deduped"] >= 1, "Must be counted as deduped"


# ---------------------------------------------------------------------------
# 4. Exact permanent failed mailbox blocked
# ---------------------------------------------------------------------------


def test_exact_permanent_failed_mailbox_blocked():
    """An exact permanent-failure mailbox must be blocked — never retried."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("bad@newco.sa", "NewCo", "newco.sa")]

    master_rows = [{"Email": "bad@newco.sa", "Company_or_Office": "NewCo",
                    "Send_State": "", "Terminal_Outcome": "FAILED_PERMANENT BOUNCE",
                    "Notes": ""}]

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(reconciler, "_read_master_send_queue", return_value=master_rows),
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["newly_queued"] == 0, "Permanent failure must not be queued"
    assert result["skipped_permanent_failed"] >= 1


# ---------------------------------------------------------------------------
# 5. Different verified replacement accepted if company never successfully contacted
# ---------------------------------------------------------------------------


def test_different_verified_replacement_accepted():
    """A different verified email at the same company is accepted when the company
    has no successful contact and the failed exact mailbox was permanent-bounced."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [
        _make_candidate("hr@newco.sa", "NewCo", "newco.sa",
                        replacement_of="bad@newco.sa"),
    ]

    master_rows = [{"Email": "bad@newco.sa", "Company_or_Office": "NewCo",
                    "Send_State": "", "Terminal_Outcome": "FAILED_PERMANENT BOUNCE",
                    "Notes": ""}]

    inserted = []

    def mock_insert(token, rows, spreadsheet_id):
        for row in rows:
            inserted.append(row)
        email_val = inserted[-1]["Email"] if inserted else "placeholder"
        company_val = inserted[-1].get("Company_or_Office", "") if inserted else ""
        return [{"queue_id": f"qid-{len(inserted)}", "email": email_val,
                 "company": company_val, "row_number": "2", "added_at": utc_now()}]

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(reconciler, "_read_master_send_queue", return_value=master_rows),
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["newly_queued"] >= 1, "Replacement must be accepted"
    if inserted:
        evidence = inserted[0].get("Evidence_or_Notes", "")
        assert "replacement_of" in evidence.lower(), f"Replacement note missing: {evidence}"


# ---------------------------------------------------------------------------
# 6. Repeated run idempotent
# ---------------------------------------------------------------------------


def test_repeated_run_idempotent():
    """Re-running after a successful run must not re-insert already-promoted routes."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("test@newco.sa", "NewCo", "newco.sa")]
    _write_candidates(temp_root / "candidates.jsonl", candidates)

    def mock_insert_first(token, rows, spreadsheet_id):
        result = []
        for row in rows:
            result.append({"queue_id": "qid-1", "email": row["Email"],
                     "company": row.get("Company_or_Office", ""),
                     "row_number": "2", "added_at": utc_now()})
        return result

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert_first),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        summary1 = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert summary1["newly_queued"] == 1, "First pass must queue the candidate"

    inserted_second = []

    def mock_insert_second(token, rows, spreadsheet_id):
        inserted_second.extend(rows)
        return [{"queue_id": f"qid-r{i}", "email": row["Email"],
                 "company": row.get("Company_or_Office", ""),
                 "row_number": str(2 + i), "added_at": utc_now()} for i, row in enumerate(rows)]

    patches2 = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert_second),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches2:
            stack.enter_context(p)
        summary2 = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert summary2["ok"] is True
    assert summary2["newly_queued"] == 0, "Second run must not re-insert"
    assert len(inserted_second) == 0, "No inserts should happen on idempotent restart"


# ---------------------------------------------------------------------------
# 7. Two batches do not reinsert first batch
# ---------------------------------------------------------------------------


def test_two_batches_no_reinsert():
    """After batch 1 is flushed and buffer cleared, batch 2 must not contain batch 1 rows."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [
        _make_candidate(f"test{i}@newco.sa", "NewCo", "newco.sa")
        for i in range(6)
    ]
    _write_candidates(temp_root / "candidates.jsonl", candidates)

    all_inserted = []
    call_count = [0]

    def mock_insert(token, rows, spreadsheet_id):
        all_inserted.extend(rows)
        call_count[0] += 1
        result = []
        for row in rows:
            result.append({"queue_id": f"qid-{len(all_inserted)}", "email": row["Email"],
                     "company": row.get("Company_or_Office", ""),
                     "row_number": str(2 + len(result)), "added_at": utc_now()})
        return result

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            batch_size=3,
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["batches_processed"] == 2, "Should process 2 batches"
    assert result["newly_queued"] == 6, "All 6 candidates must be queued"
    assert call_count[0] == 2, "Insert called once per batch"

    emails = [r["Email"].strip().lower() for r in all_inserted]
    assert len(emails) == len(set(emails)), "No duplicate emails across batches"


# ---------------------------------------------------------------------------
# 8. TTW/ASA/Jordan hold
# ---------------------------------------------------------------------------


def test_ttw_asa_jordan_held():
    """TTW, ASA are excluded; Jordan-held emails are blocked."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [
        _make_candidate("careers@ttwsa.com", "TTW", "ttwsa.com"),
        _make_candidate("info@arabsustainablearchitecture.com", "Arab Sustainable Architecture",
                        "arabsustainablearchitecture.com"),
        _make_candidate("jobs@jordanco.jo", "Jordan Co", "jordanco.jo",
                        evidence="HOLD_OWNER_JORDAN_ENG_OFFICES"),
        _make_candidate("hr@newco.sa", "NewCo", "newco.sa"),
    ]

    inserted = []

    def mock_insert(token, rows, spreadsheet_id):
        inserted.extend(rows)
        result = []
        for row in rows:
            result.append({"queue_id": f"qid-{len(inserted)}", "email": row["Email"],
                     "company": row.get("Company_or_Office", ""),
                     "row_number": str(2 + len(result)), "added_at": utc_now()})
        return result

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["newly_queued"] == 1, "Only newco.sa should pass"
    assert result["skipped_company_excluded"] >= 2, "TTW + ASA must be excluded"
    assert result["skipped_jordan_held"] >= 1, "Jordan held must be blocked"


# ---------------------------------------------------------------------------
# 9. Inappropriate mailbox blocked
# ---------------------------------------------------------------------------


def test_inappropriate_mailbox_blocked():
    """Blocked mailbox locals (privacy, legal, finance, support, etc.) must be blocked."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [
        _make_candidate("privacy@newco.sa", "NewCo", "newco.sa"),
        _make_candidate("legal@newco.sa", "NewCo", "newco.sa"),
        _make_candidate("support@newco.sa", "NewCo", "newco.sa"),
        _make_candidate("finance@newco.sa", "NewCo", "newco.sa"),
        _make_candidate("hr@newco.sa", "NewCo", "newco.sa"),
    ]

    inserted = []

    def mock_insert(token, rows, spreadsheet_id):
        inserted.extend(rows)
        result = []
        for row in rows:
            result.append({"queue_id": f"qid-{len(inserted)}", "email": row["Email"],
                     "company": row.get("Company_or_Office", ""),
                     "row_number": str(2 + len(result)), "added_at": utc_now()})
        return result

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["newly_queued"] == 1, "Only hr@ should pass"
    assert result["skipped_mailbox"] >= 4, "All blocked mailbox locals must be skipped"


# ---------------------------------------------------------------------------
# 10. Sheets write readback mismatch fails closed
# ---------------------------------------------------------------------------


def test_sheets_write_readback_mismatch_fails_closed():
    """If readback after append shows a different email, the operation must fail closed."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("test@newco.sa", "NewCo", "newco.sa")]

    def mock_insert(token, rows, spreadsheet_id):
        raise RuntimeError(
            "FAIL_CLOSED_READBACK_MISMATCH: row 2 email = bad@hacked.com, expected test@newco.sa"
        )

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        try:
            result = replenisher.run_replenishment(
                candidates_path=temp_root / "candidates.jsonl",
                apply=True,
                monitor_dir=str(monitor_dir),
            )
            assert False, "Should have raised RuntimeError"
        except RuntimeError as exc:
            assert "FAIL_CLOSED" in str(exc), f"Error must indicate fail-closed: {exc}"


# ---------------------------------------------------------------------------
# 11. Per-batch buffer cleared after append
# ---------------------------------------------------------------------------


def test_per_batch_buffer_cleared():
    """The rows_to_insert buffer must be cleared after each batch."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [
        _make_candidate(f"batch{i}@newco.sa", "NewCo", "newco.sa")
        for i in range(4)
    ]
    _write_candidates(temp_root / "candidates.jsonl", candidates)

    buffer_snapshots = []

    def mock_insert(token, rows, spreadsheet_id):
        buffer_snapshots.append([row["Email"] for row in rows])
        result = []
        for row in rows:
            result.append({"queue_id": f"qid-{len(buffer_snapshots)}", "email": row["Email"],
                     "company": row.get("Company_or_Office", ""),
                     "row_number": "2", "added_at": utc_now()})
        return result

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            batch_size=2,
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["batches_processed"] == 2, "Should process 2 batches"
    assert result["newly_queued"] == 4, "All 4 candidates must be queued"

    if len(buffer_snapshots) >= 2:
        batch1_emails = set(buffer_snapshots[0])
        batch2_emails = set(buffer_snapshots[1])
        assert batch1_emails.isdisjoint(batch2_emails), \
            f"Batch 1 {batch1_emails} and batch 2 {batch2_emails} must not overlap"


# ---------------------------------------------------------------------------
# 12. No Gmail send code exists
# ---------------------------------------------------------------------------


def test_no_gmail_send_code():
    """The replenisher must not contain any Gmail send code."""
    import inspect
    source = inspect.getsource(replenisher)
    send_patterns = [
        "messages/send",
        "_send_raw",
        "send_raw",
        "message.send",
    ]
    for pattern in send_patterns:
        assert pattern not in source, f"Prohibited send pattern found: {pattern}"


# ---------------------------------------------------------------------------
# 13. Replacement_of note propagated
# ---------------------------------------------------------------------------


def test_replacement_of_note_propagated():
    """When a candidate has replacement_of, the note must appear in Evidence_or_Notes."""
    temp_root, monitor_dir = _mk_temp_env()
    candidates = [_make_candidate("new@newco.sa", "NewCo", "newco.sa",
                                  replacement_of="old@newco.sa")]

    inserted = []

    def mock_insert(token, rows, spreadsheet_id):
        inserted.extend(rows)
        return [{"queue_id": "qid-1", "email": row["Email"],
                 "company": row.get("Company_or_Office", ""),
                 "row_number": "2", "added_at": utc_now()} for row in rows]

    patches = _BASE_APPLY_PATCHES + [
        mock.patch.object(replenisher, "gmail_dedupe_fail_closed", return_value={}),
        mock.patch.object(replenisher, "_append_rows_with_readback", mock_insert),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = replenisher.run_replenishment(
            candidates_path=temp_root / "candidates.jsonl",
            apply=True,
            monitor_dir=str(monitor_dir),
        )

    assert result["ok"] is True
    assert len(inserted) == 1
    evidence = inserted[0].get("Evidence_or_Notes", "")
    assert "replacement_of=old@newco.sa" in evidence, f"Missing: {evidence}"


# ---------------------------------------------------------------------------
# 14. Stable Queue_ID idempotency
# ---------------------------------------------------------------------------


def test_stable_queue_id_idempotency():
    """Same email+company must produce the same Queue_ID across runs."""
    qid1 = replenisher._stable_id_for("test@example.com", "Example Co")
    qid2 = replenisher._stable_id_for("test@example.com", "Example Co")
    assert qid1 == qid2, f"Queue_ID must be stable: {qid1} != {qid2}"
    assert qid1.startswith("OASQ-"), f"Must start with OASQ-: {qid1}"

    qid3 = replenisher._stable_id_for("other@example.com", "Example Co")
    assert qid1 != qid3, "Different email must produce different Queue_ID"


# ---------------------------------------------------------------------------
# 15. Owner coverage policy: catch-all + confirmed-domain info@ fallback
# ---------------------------------------------------------------------------


def test_catch_all_route_is_not_filtered():
    route = _make_candidate(
        "info@holdingco.sa",
        "Holding Co",
        "holdingco.sa",
        verification="RECEIVING | Catch All",
        identity_gate="confirmed_official_domain",
    )
    materialized = replenisher._materialize_owner_route(route)
    assert materialized["email"] == "info@holdingco.sa"
    assert materialized["verification"] == "RECEIVING | Catch All"


def test_confirmed_domain_without_email_materializes_info_fallback():
    route = _make_candidate(
        "",
        "Example Holding",
        "exampleholding.sa",
        identity_gate="confirmed_official_domain",
    )
    route["official_domain"] = "https://www.exampleholding.sa/"
    route["domain"] = ""
    materialized = replenisher._materialize_owner_route(route)
    assert materialized["email"] == "info@exampleholding.sa"
    assert materialized["domain"] == "exampleholding.sa"
    assert materialized["route_kind"] == "general"
    assert materialized["domain_fallback"] is True


def test_quarantined_domain_does_not_materialize_info_fallback():
    route = _make_candidate(
        "",
        "Wrong Company",
        "oracle.com",
        identity_gate="confirmed_official_domain",
    )
    materialized = replenisher._materialize_owner_route(route)
    assert not materialized.get("email")


# ---------------------------------------------------------------------------
# 16. Reuse of existing reconciler helpers
# ---------------------------------------------------------------------------


def test_uses_existing_reconciler_helpers():
    """The module must import and use existing QueueReconciler helpers."""
    import inspect
    source = inspect.getsource(replenisher)

    assert "from career_engine.outreach_reconciler import" in source
    assert "_stable_id_for" in source
    assert "_read_queue_sheet" in source
    assert "_valid_email" in source


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------

import contextlib

if __name__ == "__main__":
    tests = [
        ("gmail_fail_closed_on_auth_failure", test_gmail_fail_closed_on_auth_failure),
        ("gmail_fail_closed_on_census_error", test_gmail_fail_closed_on_census_runtime_error),
        ("exact_duplicate_blocked", test_exact_duplicate_blocked),
        ("same_company_prior_contact_block", test_successful_same_company_prior_contact_block),
        ("permanent_failed_mailbox_blocked", test_exact_permanent_failed_mailbox_blocked),
        ("replacement_accepted_no_prior_contact", test_different_verified_replacement_accepted),
        ("repeated_run_idempotent", test_repeated_run_idempotent),
        ("two_batches_no_reinsert", test_two_batches_no_reinsert),
        ("ttw_asa_jordan_held", test_ttw_asa_jordan_held),
        ("inappropriate_mailbox_blocked", test_inappropriate_mailbox_blocked),
        ("sheets_readback_mismatch_fails_closed", test_sheets_write_readback_mismatch_fails_closed),
        ("per_batch_buffer_cleared", test_per_batch_buffer_cleared),
        ("no_gmail_send_code", test_no_gmail_send_code),
        ("replacement_of_note_propagated", test_replacement_of_note_propagated),
        ("stable_queue_id", test_stable_queue_id_idempotency),
        ("uses_existing_reconciler_helpers", test_uses_existing_reconciler_helpers),
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
            tb_snippet = tb[-500:]
            results.append(f"  FAIL  {name}: {exc}\n        {tb_snippet}")

    print(f"\nResults: {passed}/{passed + failed} passed, {failed} failed")
    for r in results:
        print(r)

    sys.exit(0 if failed == 0 else 1)
