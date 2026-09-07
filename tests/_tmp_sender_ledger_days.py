from __future__ import annotations
import json
from collections import Counter,defaultdict
from datetime import datetime
from career_engine.central_outreach_sender import DEFAULT_LEDGER
from career_engine.outreach_reconciler import RIYADH

def test_days():
    data=json.loads(DEFAULT_LEDGER.read_text()); entries=data.get('entries') or {}
    days=Counter(); src=Counter(); day_src=defaultdict(Counter)
    for e in entries.values():
        if str(e.get('status') or '').upper()!='SENT': continue
        stamp=str(e.get('sent_at') or '')
        try:d=datetime.fromisoformat(stamp).astimezone(RIYADH).date().isoformat()
        except Exception:d='unknown'
        days[d]+=1
        source=str(e.get('source') or '')
        src[source]+=1; day_src[d][source]+=1
    print(json.dumps({'total_sent':sum(days.values()),'per_day':dict(sorted(days.items())),'by_source':dict(src),'per_day_source':{d:dict(c) for d,c in sorted(day_src.items())}},ensure_ascii=False,indent=2))
    assert sum(days.values())>0
