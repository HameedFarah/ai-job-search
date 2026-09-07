from __future__ import annotations
import json
from pathlib import Path
from career_engine.central_outreach_sender import DEFAULT_LEDGER
from career_engine.outreach_reconciler import _read_queue_sheet, normalise_row
from runtime.outscraper_sheet_runner import rclone_access_token


def test_ledger_queue_diagnostic():
    token=rclone_access_token()
    rows=[normalise_row(r) for r in _read_queue_sheet(token)]
    by_qid={r['queue_id']:r for r in rows if r['queue_id']}
    ledger=json.loads(DEFAULT_LEDGER.read_text()).get('entries') or {}
    sent=[e for e in ledger.values() if str(e.get('status') or '').upper()=='SENT']
    missing=[]; mismatch=[]
    for e in sent:
        qid=str(e.get('queue_id') or '')
        row=by_qid.get(qid)
        if not row:
            missing.append(qid); continue
        mid=str(e.get('gmail_message_id') or '')
        if row['status']!='SENT' or str(row.get('gmail_message_id') or '')!=mid:
            mismatch.append({'queue_id':qid,'email':e.get('email'),'ledger_mid':mid,'ledger_sent_at':e.get('sent_at'),'queue_status':row['status'],'queue_mid':row.get('gmail_message_id'),'row_number':row['row_number']})
    print(json.dumps({'ledger_sent':len(sent),'auto_queue_rows':len(rows),'missing_queue_id':len(missing),'mismatch':len(mismatch),'missing_sample':missing[:20],'mismatch_sample':mismatch[:30]},ensure_ascii=False,indent=2))
    Path('runtime/acceptance/auto-send-queue/ledger-queue-mismatch-20260907.json').write_text(json.dumps({'missing':missing,'mismatch':mismatch},ensure_ascii=False,indent=2),encoding='utf-8')
