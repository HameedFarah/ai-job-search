from __future__ import annotations
import json
from career_engine.central_outreach_sender import preflight, DEFAULT_LEDGER
from pathlib import Path

STATUS = Path('runtime/acceptance/auto-send-queue/preflight-status-20260907.json')

def test_live_sender_preflight_no_send():
    code = preflight(ledger_path=DEFAULT_LEDGER, status_path=STATUS)
    assert code == 0
    payload = json.loads(STATUS.read_text(encoding='utf-8'))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    assert payload['phase'] == 'preflight-ok'
    assert payload['sends'] == 0
    assert payload['sender'] == 'hameedfarah@gmail.com'
    if payload['eligible_rega']:
        assert 'REGA' in payload['next_source'].upper()
