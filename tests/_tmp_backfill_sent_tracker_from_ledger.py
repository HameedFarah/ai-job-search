from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote,urlencode
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID
from career_engine.outreach_reconciler import SENDER_GWS_CONFIG_DIR,gmail_access_token_for_context,_profile_email
from career_engine.central_outreach_sender import DEFAULT_LEDGER,_read_campaign_config

SHEET='Sent Email Tracker'

def read_tracker(token):
    rng=quote(f'{SHEET}!A:P',safe='!:')
    vals=(sheets_request(token,'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}').get('values') or [])
    if not vals:return [],[]
    hdr=[str(x) for x in vals[0]]; rows=[]
    for raw in vals[1:]:
        row=[str(x) for x in raw]+['']*(len(hdr)-len(raw)); rows.append(dict(zip(hdr,row)))
    return hdr,rows

def gmail_subject(token,mid):
    params=[('format','metadata'),('metadataHeaders','Subject')]
    body=sheets_request(token,'GET',f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}?'+urlencode(params))
    hdr={str(x.get('name') or '').lower():str(x.get('value') or '') for x in ((body.get('payload') or {}).get('headers') or [])}
    return hdr.get('subject','').strip()

def test_backfill():
    sheet_token=rclone_access_token(); gmail_token=gmail_access_token_for_context(SENDER_GWS_CONFIG_DIR)
    assert _profile_email(SENDER_GWS_CONFIG_DIR)=='hameedfarah@gmail.com'
    headers,tracker=read_tracker(sheet_token)
    expected=['Recipient_Email','Recipient_Domain','Sender_Account','Gmail_Message_ID','Sent_At','Subject','Career_Related','Company_or_Office','Source_Queue_ID','Delivery_State','Bounce_State','Last_Reconciled_At','Content_Language','Content_Version','Resume_File','Campaign_Subject']
    assert headers==expected
    existing={str(r.get('Gmail_Message_ID') or '').strip() for r in tracker if str(r.get('Gmail_Message_ID') or '').strip()}
    ledger=json.loads(DEFAULT_LEDGER.read_text()).get('entries') or {}; config=_read_campaign_config(sheet_token); current_subject=config['subject']
    missing=[]
    for e in ledger.values():
        if str(e.get('status') or '').upper()!='SENT':continue
        mid=str(e.get('gmail_message_id') or '').strip(); email=str(e.get('email') or '').strip().lower(); at=str(e.get('sent_at') or '').strip()
        if not mid or not email or not at or mid in existing:continue
        missing.append(e)
    # Fetch all authoritative Gmail subjects before any write.
    rows=[]; now=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    for e in missing:
        mid=str(e['gmail_message_id']); email=str(e['email']).lower(); subject=gmail_subject(gmail_token,mid)
        assert subject, f'missing Gmail subject for {mid}'
        lang='AR' if any('\u0600' <= ch <= '\u06ff' for ch in subject) else 'EN'
        version=str(config.get('content_version') or '') if subject==current_subject else ''
        rows.append([email,email.rsplit('@',1)[1],'hameedfarah@gmail.com',mid,str(e['sent_at']),subject,'YES',str(e.get('company') or ''),str(e.get('queue_id') or ''),'SENT','',now,lang,version,'',subject])
    print(json.dumps({'tracker_before':len(tracker),'ledger_sent':sum(1 for e in ledger.values() if str(e.get('status') or '').upper()=='SENT'),'missing_before':len(rows)},ensure_ascii=False))
    if rows:
        rng=quote(f'{SHEET}!A:P',safe='!:')
        url=f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS'
        for i in range(0,len(rows),100):
            sheets_request(sheet_token,'POST',url,{'values':rows[i:i+100]})
    _h,after=read_tracker(sheet_token); after_ids={str(r.get('Gmail_Message_ID') or '').strip() for r in after if str(r.get('Gmail_Message_ID') or '').strip()}
    ledger_ids={str(e.get('gmail_message_id') or '').strip() for e in ledger.values() if str(e.get('status') or '').upper()=='SENT' and str(e.get('gmail_message_id') or '').strip()}
    remaining=sorted(ledger_ids-after_ids)
    print(json.dumps({'tracker_after':len(after),'ledger_ids':len(ledger_ids),'missing_after':len(remaining),'appended':len(rows)},ensure_ascii=False))
    assert not remaining
