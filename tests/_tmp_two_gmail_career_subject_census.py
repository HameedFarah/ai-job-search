from __future__ import annotations
import json
from datetime import datetime,timedelta
from urllib.parse import urlencode,quote
from career_engine.outreach_reconciler import PRIMARY_GWS_CONFIG_DIR,SENDER_GWS_CONFIG_DIR,gmail_access_token_for_context,_profile_email,RIYADH
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID
from career_engine.central_outreach_sender import _read_campaign_config

def _subjects():
    rng=quote('Sent Email Tracker!A:P',safe='!:'); vals=(sheets_request(rclone_access_token(),'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}').get('values') or [])
    hdr=[str(x) for x in vals[0]]; s=set()
    for raw in vals[1:]:
        row=[str(x) for x in raw]+['']*(len(hdr)-len(raw)); r=dict(zip(hdr,row))
        if str(r.get('Career_Related') or '').upper()=='YES' and str(r.get('Subject') or '').strip(): s.add(str(r.get('Subject')).strip())
    s.add(_read_campaign_config(rclone_access_token())['subject'])
    return sorted(s)

def _count(token,q):
    total=0; page=''
    while True:
        params=[('q',q),('maxResults',500),('labelIds','SENT')]
        if page:params.append(('pageToken',page))
        body=sheets_request(token,'GET','https://gmail.googleapis.com/gmail/v1/users/me/messages?'+urlencode(params))
        total+=len(body.get('messages') or []); page=str(body.get('nextPageToken') or '')
        if not page:return total

def test_census():
    subjects=_subjects(); clause='{'+ ' '.join('subject:"'+x.replace('"','\\"')+'"' for x in subjects) +'}'
    contexts=(('hameedo@gmail.com',PRIMARY_GWS_CONFIG_DIR),('hameedfarah@gmail.com',SENDER_GWS_CONFIG_DIR))
    start=datetime(2026,8,27,tzinfo=RIYADH); end=datetime(2026,9,8,tzinfo=RIYADH); out={}
    for expected,cfg in contexts:
        token=gmail_access_token_for_context(cfg); assert _profile_email(cfg)==expected; d=start; days={}
        while d<end:
            nxt=d+timedelta(days=1); q=f'after:{int(d.timestamp())} before:{int(nxt.timestamp())} {clause}'; days[d.date().isoformat()]=_count(token,q); d=nxt
        out[expected]=days
    combined={d:sum(out[a].get(d,0) for a,_ in contexts) for d in sorted(next(iter(out.values())).keys())}
    print(json.dumps({'subjects':len(subjects),'per_account':out,'combined_per_day':combined,'combined_total':sum(combined.values())},ensure_ascii=False,indent=2))
