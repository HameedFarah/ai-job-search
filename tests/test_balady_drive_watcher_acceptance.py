from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCHER = ROOT / "career_engine" / "balady_drive_watcher.py"


def _source() -> str:
    return WATCHER.read_text(encoding="utf-8")


def test_watcher_has_explicit_provider_validation_gate_before_promotion():
    source = _source()
    assert "OUTSCRAPER" in source.upper()
    assert ("validate_emails" in source or "validate_outreach_emails" in source), (
        "Balady mailboxes must pass the canonical Outscraper validator before queue promotion"
    )
    promote_pos = source.find("# Promote candidates")
    validation_pos = max(source.find("validate_emails"), source.find("validate_outreach_emails"))
    assert validation_pos >= 0
    if promote_pos >= 0:
        assert validation_pos < promote_pos


def test_watcher_never_uses_random_queue_ids():
    source = _source()
    assert "uuid.uuid4" not in source
    assert "_stable_id_for" in source


def test_watcher_never_ignores_queue_readback_failure():
    source = _source()
    assert "Even if readback fails, the append may have succeeded" not in source
    assert "FAIL_CLOSED_READBACK" in source


def test_auto_send_queue_mapping_uses_canonical_a_to_k_headers():
    source = _source()
    # New rows must map only to the canonical Auto Send Queue columns:
    # Queue_ID, Email, Company_or_Office, Source, Priority, Status, Added_At,
    # Sent_At, Gmail_Message_ID, Last_Error, Evidence_or_Notes.
    assert "QUEUE_HEADERS" in source or "Evidence_or_Notes" in source
    assert "Sent_At" in source
    assert "Gmail_Message_ID" in source
    assert "Last_Error" in source


def test_watcher_keeps_same_snapshot_processable_until_backlog_is_drained():
    source = _source()
    # A file fingerprint is source freshness, not a completion marker. The
    # watcher must have explicit pending/backlog progress so a 10/25-row batch
    # cannot make the rest of an unchanged snapshot disappear forever.
    markers = (
        "pending_fingerprints",
        "pending_row_fingerprints",
        "processed_fingerprints",
        "backlog",
        "cursor",
    )
    assert any(marker in source for marker in markers), (
        "Watcher needs restart-safe per-row/backlog progress independent of Drive modifiedTime"
    )


def test_watcher_deduplicates_email_and_company_not_only_office_id():
    source = _source()
    assert "seen_emails" in source or "deduplicate_candidates" in source or "selected_emails" in source
    assert "company" in source.lower()


def test_watcher_has_fail_closed_acceptance_guard():
    source = _source()
    assert "CAREER_BALADY_WATCHER_ENABLE" in source
    assert "BALADY_WATCHER_NOT_ACCEPTED" in source
