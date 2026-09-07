from __future__ import annotations
import json
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID
from urllib.parse import quote

def test_missing_ids():
    rng=quote('Sent Email Tracker!A:P',safe='!:'); vals=(sheets_request(rclone_access_token(),'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}').get('values') or [])
    hdr=[str(x) for x in vals[0]]; out=[]
    for raw in vals[1:]:
        row=[str(x) for x in raw]+['']*(len(hdr)-len(raw)); r=dict(zip(hdr,row))
        if str(r.get('Career_Related') or '').upper()=='YES' and str(r.get('Sent_At') or '').strip() and str(r.get('Recipient_Email') or '').strip() and not str(r.get('Gmail_Message_ID') or '').strip(): out.append({k:r.get(k) for k in ('Recipient_Email','Sent_At','Sender_Account','Delivery_State','Bounce_State','Subject')})
    print(json.dumps(out,ensure_ascii=False,indent=2)); print('COUNT',len(out))
