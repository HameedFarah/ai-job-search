from __future__ import annotations
import json
from urllib.parse import quote
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID
from career_engine.central_outreach_sender import DEFAULT_LEDGER

def test_verify():
    token=rclone_access_token(); rng=quote('Sent Email Tracker!D2:D10000',safe='!:')
    vals=sheets_request(token,'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}').get('values') or []
    tracker_ids={str(r[0]).strip() for r in vals if r and str(r[0]).strip()}
    ledger=json.loads(DEFAULT_LEDGER.read_text()).get('entries') or {}
    ledger_ids={str(e.get('gmail_message_id') or '').strip() for e in ledger.values() if str(e.get('status') or '').upper()=='SENT' and str(e.get('gmail_message_id') or '').strip()}
    missing=sorted(ledger_ids-tracker_ids)
    print(json.dumps({'ledger_sent_ids':len(ledger_ids),'tracker_gmail_ids':len(tracker_ids),'ledger_ids_missing_from_tracker':len(missing),'missing_sample':missing[:20]},ensure_ascii=False,indent=2))
    assert not missing
