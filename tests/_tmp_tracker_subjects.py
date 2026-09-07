from __future__ import annotations
import json
from collections import Counter
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID
from urllib.parse import quote

def test_subjects():
    rng=quote('Sent Email Tracker!A:P',safe='!:'); vals=(sheets_request(rclone_access_token(),'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}').get('values') or [])
    hdr=[str(x) for x in vals[0]]; c=Counter(); cc=Counter()
    for raw in vals[1:]:
        row=[str(x) for x in raw]+['']*(len(hdr)-len(raw)); r=dict(zip(hdr,row))
        if str(r.get('Career_Related') or '').upper()!='YES': continue
        sub=str(r.get('Subject') or '').strip(); camp=str(r.get('Campaign_Subject') or '').strip()
        if sub:c[sub]+=1
        if camp:cc[camp]+=1
    print('SUBJECTS',json.dumps(c.most_common(),ensure_ascii=False,indent=2)); print('CAMPAIGN',json.dumps(cc.most_common(),ensure_ascii=False,indent=2))
