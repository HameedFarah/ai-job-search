from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _source(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def test_status_dropdown_exposes_irrelevant_and_owner_feedback_events() -> None:
    source = _source("dashboard/career-review/site/assets/irrelevant-feedback.js")
    assert "id: 'irrelevant'" in source
    assert "label: 'Irrelevant'" in source
    assert "role_marked_irrelevant" in source
    assert "role_irrelevant_retracted" in source
    assert "explicit_owner_relevance_feedback" in source


def test_relevance_feedback_is_append_only_and_history_schema_safe() -> None:
    source = _source("dashboard/career-review/site/assets/irrelevant-feedback.js")
    assert "createRecord('history'" in source
    assert "updateRecord('history'" not in source
    assert "deleteRecord('history'" not in source

    payload_start = source.index("const saved = await createRecord('history', {")
    payload_end = source.index("}, `owner-relevance-", payload_start)
    payload = source[payload_start:payload_end]
    assert "role_key: role.key" in payload
    assert "event," in payload
    assert "from_stage:" in payload
    assert "to_stage:" in payload
    assert "note: JSON.stringify" in payload
    # The live collection has repeatedly rejected ad-hoc top-level metadata.
    # Rich owner-feedback context must remain inside note JSON.
    note_start = payload.index("note: JSON.stringify")
    top_level = payload[:note_start]
    for forbidden in ("job_id:", "company:", "role:", "recorded_at:", "ui_source:", "evidence_type:", "actor:"):
        assert forbidden not in top_level
    note = payload[note_start:]
    for metadata in ("job_id:", "company:", "role:", "recorded_at:", "ui_source:", "evidence_type:", "actor:"):
        assert metadata in note


def test_closed_and_irrelevant_detail_dismiss_with_undo_toast() -> None:
    source = _source("dashboard/career-review/site/assets/irrelevant-feedback.js")
    assert "['irrelevant', 'inactive'].includes(nextStage)" in source
    assert "state.overlayOpen && state.overlayKey === role.key" in source
    assert "closeOverlay()" in source
    assert "showActionToast(" in source
    assert "'Undo'" in source
    assert "undoDismissal" in source
    assert "Marked Irrelevant" in source
    assert "Job closed" in source


def test_relevance_wrapper_waits_for_later_deferred_bulk_table_script() -> None:
    source = _source("dashboard/career-review/site/assets/irrelevant-feedback.js")
    assert "document.readyState === 'loading' || document.readyState === 'interactive'" in source
    assert "document.addEventListener('DOMContentLoaded', installMoveRoleWrapper" in source


def test_owner_feedback_parser_recognises_irrelevant_events() -> None:
    source = _source("career_engine/owner_feedback.py")
    assert "role_marked_irrelevant" in source
    assert "role_irrelevant_retracted" in source
