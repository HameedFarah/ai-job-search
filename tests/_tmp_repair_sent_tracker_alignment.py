from __future__ import annotations
import json
from urllib.parse import quote
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID
from career_engine.central_outreach_sender import DEFAULT_LEDGER
SHEET='Sent Email Tracker'; START=5002; END=5875

def test_repair_alignment():
    token=rclone_access_token(); src=quote(f'{SHEET}!L{START}:AA{END}',safe='!:')
    body=sheets_request(token,'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{src}?valueRenderOption=FORMATTED_VALUE')
    vals=body.get('values') or []
    assert len(vals)==END-START+1, (len(vals),END-START+1)
    norm=[]
    for i,row in enumerate(vals,start=START):
        r=[str(x) for x in row]+['']*(16-len(row))
        assert len(r)==16 and r[0] and r[3], f'bad recovery row {i}: {r[:5]}'
        norm.append(r[:16])
    # Write recovered rows into their intended A:P positions using RAW to avoid reinterpretation.
    for off in range(0,len(norm),100):
        data=[]
        for j,row in enumerate(norm[off:off+100],start=START+off):
            data.append({'range':f"'{SHEET}'!A{j}:P{j}",'values':[row]})
        sheets_request(token,'POST',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchUpdate',{'valueInputOption':'RAW','data':data})
    # Source spill beyond the legitimate target width is Q:AA; L:P was overwritten above with correct target values.
    sheets_request(token,'POST',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchClear',{'ranges':[f"'{SHEET}'!Q{START}:AA{END}"]})
    # Verify every central-sender Gmail id is now in canonical D column.
    d=quote(f'{SHEET}!D2:D{END}',safe='!:')
    got=(sheets_request(token,'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{d}').get('values') or [])
    ids={str(r[0]).strip() for r in got if r and str(r[0]).strip()}
    ledger=json.loads(DEFAULT_LEDGER.read_text()).get('entries') or {}
    ledger_ids={str(e.get('gmail_message_id') or '').strip() for e in ledger.values() if str(e.get('status') or '').upper()=='SENT' and str(e.get('gmail_message_id') or '').strip()}
    missing=sorted(ledger_ids-ids)
    print(json.dumps({'repaired_rows':len(norm),'ledger_ids':len(ledger_ids),'missing_after':len(missing)},ensure_ascii=False))
    assert not missing
