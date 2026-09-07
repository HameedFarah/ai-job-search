from __future__ import annotations
import json
from collections import Counter
from datetime import datetime
from runtime.outscraper_sheet_runner import rclone_access_token
from career_engine.outreach_reconciler import RIYADH
from career_engine.central_outreach_sender import DEFAULT_LEDGER

# local helper because read_table lives in REGA runtime, not sender module
def _sent_tracker(token):
    from runtime.outscraper_sheet_runner import sheets_request,SPREADSHEET_ID
    from urllib.parse import quote
    rng=quote('Sent Email Tracker!A:P',safe='!:')
    body=sheets_request(token,'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}')
    vals=body.get('values') or []; hdr=[str(x) for x in vals[0]]; out=[]
    for raw in vals[1:]:
        row=[str(x) for x in raw]+['']*(len(hdr)-len(raw)); out.append(dict(zip(hdr,row)))
    return out

def test_union():
    tracker=_sent_tracker(rclone_access_token()); ledger=json.loads(DEFAULT_LEDGER.read_text()).get('entries') or {}
    events={}
    for r in tracker:
        mid=str(r.get('Gmail_Message_ID') or '').strip(); email=str(r.get('Recipient_Email') or '').strip().lower(); at=str(r.get('Sent_At') or '').strip()
        if not mid or not email or not at or str(r.get('Career_Related') or '').upper()!='YES': continue
        events[mid]={'message_id':mid,'email':email,'sent_at':at,'sender':str(r.get('Sender_Account') or ''),'source':'sent_tracker'}
    tracker_ids=set(events)
    for e in ledger.values():
        if str(e.get('status') or '').upper()!='SENT': continue
        mid=str(e.get('gmail_message_id') or '').strip(); email=str(e.get('email') or '').strip().lower(); at=str(e.get('sent_at') or '').strip()
        if not mid or not email or not at: continue
        if mid not in events: events[mid]={'message_id':mid,'email':email,'sent_at':at,'sender':'hameedfarah@gmail.com','source':'central_sender_ledger'}
    perday=Counter(); sender=Counter(); source=Counter(); recipients=Counter()
    for e in events.values():
        try:d=datetime.fromisoformat(e['sent_at'].replace('Z','+00:00')).astimezone(RIYADH).date().isoformat()
        except Exception:d=e['sent_at'][:10]
        perday[d]+=1; sender[e['sender']]+=1; source[e['source']]+=1; recipients[e['email']]+=1
    print(json.dumps({'career_send_events_union':len(events),'unique_recipients':len(recipients),'duplicate_recipient_excess':sum(n-1 for n in recipients.values()),'per_day':dict(sorted(perday.items())),'by_sender':dict(sender),'source_contribution':dict(source),'ledger_sent':sum(1 for e in ledger.values() if str(e.get('status') or '').upper()=='SENT'),'tracker_message_ids':len(tracker_ids),'overlap_message_ids':sum(1 for e in ledger.values() if str(e.get('gmail_message_id') or '') in tracker_ids)},ensure_ascii=False,indent=2))
    assert events
