from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
from career_engine.central_outreach_sender import DEFAULT_LEDGER
from career_engine.outreach_reconciler import _read_queue_sheet
from runtime.outscraper_sheet_runner import rclone_access_token

def test_diag():
    ledger=json.loads(DEFAULT_LEDGER.read_text()).get('entries') or {}
    sent={qid:e for qid,e in ledger.items() if str(e.get('status') or '').upper()=='SENT'}
    qrows=_read_queue_sheet(rclone_access_token())
    byid={str(r.get('Queue_ID') or ''):r for r in qrows}
    missing=[]; stale=[]; mid_mismatch=[]
    for qid,e in sent.items():
        r=byid.get(qid)
        if not r:
            missing.append(qid); continue
        status=str(r.get('Status') or '').upper(); qm=str(r.get('Gmail_Message_ID') or '').strip(); lm=str(e.get('gmail_message_id') or '').strip()
        if status!='SENT': stale.append({'queue_id':qid,'queue_status':status,'ledger_mid':lm,'queue_mid':qm,'email':e.get('email')})
        if lm and qm and lm!=qm: mid_mismatch.append({'queue_id':qid,'ledger_mid':lm,'queue_mid':qm})
    print(json.dumps({'ledger_sent':len(sent),'queue_rows':len(qrows),'missing_queue_ids':len(missing),'stale_non_sent':len(stale),'message_id_mismatch':len(mid_mismatch),'stale':stale[:50],'missing_sample':missing[:50],'mismatch_sample':mid_mismatch[:50]},ensure_ascii=False,indent=2))
