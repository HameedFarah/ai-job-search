from __future__ import annotations
from collections import defaultdict
import json
from runtime.outscraper_sheet_runner import rclone_access_token
from career_engine.outreach_reconciler import _read_queue_sheet,normalise_row,_email_domain

def test_pending_domain_dupes():
    rows=[normalise_row(r) for r in _read_queue_sheet(rclone_access_token())]
    pending=[r for r in rows if r['status'] in {'PENDING','SENDING','FAILED_TEMPORARY'}]
    by=defaultdict(list)
    for r in pending:
        d=_email_domain(r['email'])
        if d: by[d].append(r)
    dup={d:[{'queue_id':r['queue_id'],'company':r['company'],'email':r['email'],'source':r['source'],'status':r['status']} for r in rs] for d,rs in by.items() if len(rs)>1 and d not in {'gmail.com','hotmail.com','outlook.com','yahoo.com','icloud.com','live.com'}}
    print(json.dumps({'pending':len(pending),'duplicate_domains':len(dup),'rows_on_duplicate_domains':sum(len(v) for v in dup.values()),'duplicates':dup},ensure_ascii=False,indent=2))
