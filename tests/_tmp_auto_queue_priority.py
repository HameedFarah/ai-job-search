from __future__ import annotations
import json
from collections import Counter
from runtime.outscraper_sheet_runner import rclone_access_token
from career_engine.outreach_reconciler import _read_queue_sheet,normalise_row,QueueReconciler

def test_priority():
    token=rclone_access_token(); raw=_read_queue_sheet(token); rows=[normalise_row(r) for r in raw]
    active=[r for r in rows if r['status'] in {'PENDING','SENDING','FAILED_TEMPORARY'}]
    counts=Counter(r['status'] for r in rows); active_sources=Counter(r['source'] for r in active)
    rega=[r for r in active if 'REGA' in r['source'].upper()]
    balady=[r for r in active if 'BALADY' in r['source'].upper()]
    ordered=QueueReconciler(token).sort_by_priority(active)
    print(json.dumps({'rows':len(rows),'status_counts':dict(counts),'active':len(active),'active_sources':dict(active_sources),'active_rega':len(rega),'active_balady':len(balady),'top20':[{'queue_id':r['queue_id'],'email':r['email'],'company':r['company'],'source':r['source'],'priority':r['priority'],'status':r['status']} for r in ordered[:20]]},ensure_ascii=False,indent=2))
    assert all('REGA' in r['source'].upper() for r in ordered[:min(len(rega),20)]) if rega else True
