from __future__ import annotations
import json
from datetime import datetime, timedelta
from email.utils import getaddresses
from urllib.parse import urlencode
from career_engine.outreach_reconciler import (
    PRIMARY_GWS_CONFIG_DIR,SENDER_GWS_CONFIG_DIR,gmail_access_token_for_context,
    _profile_email,RIYADH
)
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request
from career_engine.central_outreach_sender import _read_campaign_config

CONTEXTS=(('primary',PRIMARY_GWS_CONFIG_DIR),('sender',SENDER_GWS_CONFIG_DIR))
START='2026-08-27'
END='2026-09-08'

def _list(token, query):
    out=[]; page=''
    while True:
        params=[('q',query),('maxResults',500),('labelIds','SENT')]
        if page: params.append(('pageToken',page))
        body=sheets_request(token,'GET','https://gmail.googleapis.com/gmail/v1/users/me/messages?'+urlencode(params))
        out.extend(body.get('messages') or [])
        page=str(body.get('nextPageToken') or '')
        if not page:return out

def _meta(token,mid):
    params=[('format','metadata'),('metadataHeaders','To'),('metadataHeaders','Subject'),('metadataHeaders','From')]
    body=sheets_request(token,'GET',f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}?'+urlencode(params))
    return {str(x.get('name') or '').lower():str(x.get('value') or '') for x in ((body.get('payload') or {}).get('headers') or [])}

def test_census():
    sheet_token=rclone_access_token(); campaign=_read_campaign_config(sheet_token); current_subject=campaign['subject']
    print('CURRENT_CAMPAIGN_SUBJECT',current_subject)
    start=datetime.fromisoformat(START).replace(tzinfo=RIYADH); end=datetime.fromisoformat(END).replace(tzinfo=RIYADH)
    payload={}
    for name,cfg in CONTEXTS:
        token=gmail_access_token_for_context(cfg); profile=_profile_email(cfg)
        day_counts={}; current_subject_counts={}; recipient_counts={}
        d=start
        while d<end:
            nxt=d+timedelta(days=1)
            q=f'after:{int(d.timestamp())} before:{int(nxt.timestamp())}'
            msgs=_list(token,q); day=d.date().isoformat(); day_counts[day]=len(msgs)
            cur=0; rc=0
            for m in msgs:
                meta=_meta(token,str(m.get('id') or ''))
                if meta.get('subject','').strip()==current_subject.strip(): cur+=1
                if getaddresses([meta.get('to','')]): rc+=1
            current_subject_counts[day]=cur; recipient_counts[day]=rc
            d=nxt
        payload[name]={'profile':profile,'all_sent_per_day':day_counts,'current_campaign_subject_per_day':current_subject_counts,'messages_with_to_per_day':recipient_counts}
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    assert payload['primary']['profile']=='hameedo@gmail.com'
    assert payload['sender']['profile']=='hameedfarah@gmail.com'
