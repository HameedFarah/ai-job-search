from __future__ import annotations

from unittest.mock import patch

import pytest

from career_engine.incremental_outreach_replenisher import _append_rows_with_readback


def _row() -> dict[str, str]:
    return {
        "Queue_ID": "OASQ-TEST-1",
        "Email": "jobs@company.sa",
        "Company_or_Office": "Company",
        "Source": "REGA_TEST",
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


def test_append_fails_closed_on_readback_mismatch():
    responses = [{}, {"values": [["OASQ-TEST-1", "wrong@company.sa"]]}]
    with patch("career_engine.incremental_outreach_replenisher._read_queue_sheet", return_value=[]), \
         patch("career_engine.incremental_outreach_replenisher.sheets_request", side_effect=responses):
        with pytest.raises(RuntimeError, match="READBACK_MISMATCH"):
            _append_rows_with_readback("token", [_row()], "sheet-id")
