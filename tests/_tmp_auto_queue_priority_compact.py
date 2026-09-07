from __future__ import annotations
import json
from collections import Counter
from runtime.outscraper_sheet_runner import rclone_access_token
from career_engine.outreach_reconciler import _read_queue_sheet,normalise_row,QueueReconciler

def cat(src):
    u=str(src or '').upper()
    if 'REGA' in u:return 'REGA'
    if 'BALADY' in u:return 'BALADY'
    return 'OTHER'

def test_priority_compact():
    token=rclone_access_token(); rows=[normalise_row(r) for r in _read_queue_sheet(token)]
    active=[r for r in rows if r['status'] in {'PENDING','SENDING','FAILED_TEMPORARY'}]
    ordered=QueueReconciler(token).sort_by_priority(active)
    payload={'rows':len(rows),'status':dict(Counter(r['status'] for r in rows)),'active_by_lane':dict(Counter(cat(r['source']) for r in active)),'sent_by_lane':dict(Counter(cat(r['source']) for r in rows if r['status']=='SENT')),'top30':[{'queue_id':r['queue_id'],'company':r['company'],'email':r['email'],'lane':cat(r['source']),'source':r['source'],'status':r['status']} for r in ordered[:30]]}
    print(json.dumps(payload,ensure_ascii=False,indent=2))
    if any(cat(r['source'])=='REGA' for r in active): assert cat(ordered[0]['source'])=='REGA'
