from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from career_engine.incremental_outreach_replenisher import (
    _append_rows_with_readback,
    _queue_row_for_insert,
    run_replenishment,
)


def _row() -> dict[str, str]:
    return {
        "Queue_ID": "OASQ-TEST-1",
        "Email": "jobs@company.sa",
        "Company_or_Office": "Company",
        "Source": "REGA_TEST",
        "Priority": "IMPORTANT",
        "Evidence_or_Notes": "verified",
    }


def test_append_uses_values_update_put_and_exact_readback():
    responses = [{}, {"values": [["OASQ-TEST-1", "jobs@company.sa"]]}]
    with patch("career_engine.incremental_outreach_replenisher._read_queue_sheet", return_value=[]), \
         patch("career_engine.incremental_outreach_replenisher.sheets_request", side_effect=responses) as request:
        inserted = _append_rows_with_readback("token", [_row()], "sheet-id")
    assert inserted[0]["queue_id"] == "OASQ-TEST-1"
    assert inserted[0]["email"] == "jobs@company.sa"
    first = request.call_args_list[0]
    assert first.args[1] == "PUT"
    assert "valueInputOption=RAW" in first.args[2]
    assert first.args[3]["values"][0][0:2] == ["OASQ-TEST-1", "jobs@company.sa"]
    assert first.args[3]["values"][0][4] == "IMPORTANT"


def test_rega_queue_rows_are_explicitly_important():
    rega = _queue_row_for_insert(
        "jobs@company.sa",
        "Company",
        "REGA_MANUAL_IDENTITY_RECOVERY_20260908",
        "verified",
    )
    balady = _queue_row_for_insert(
        "jobs@other.sa",
        "Other",
        "BALADY_AUTO_PREPARED",
        "verified",
    )
    assert rega["Priority"] == "IMPORTANT"
    assert balady["Priority"] == "NORMAL"


def test_append_fails_closed_on_readback_mismatch():
    responses = [{}, {"values": [["OASQ-TEST-1", "wrong@company.sa"]]}]
    with patch("career_engine.incremental_outreach_replenisher._read_queue_sheet", return_value=[]), \
         patch("career_engine.incremental_outreach_replenisher.sheets_request", side_effect=responses):
        with pytest.raises(RuntimeError, match="READBACK_MISMATCH"):
            _append_rows_with_readback("token", [_row()], "sheet-id")


def test_run_creates_nested_monitor_directory(tmp_path):
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(json.dumps({
        "email": "jobs@company.sa",
        "company_name": "Company",
        "domain": "company.sa",
        "identity_gate": "confirmed_official_email",
        "verification": "RECEIVING",
        "route_kind": "recruitment",
        "source": "REGA_TEST",
    }) + "\n", encoding="utf-8")
    monitor = tmp_path / "nested" / "evidence"
    result = run_replenishment(candidates, batch_size=1, apply=False, monitor_dir=str(monitor))
    assert result["ok"] is True
    assert monitor.is_dir()
    assert (monitor / "replenisher-checkpoint.json").is_file()
    assert (monitor / "replenisher-summary.json").is_file()
