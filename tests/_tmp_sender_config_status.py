from __future__ import annotations
import json
from runtime.outscraper_sheet_runner import rclone_access_token
from career_engine.central_outreach_sender import _read_campaign_config,DEFAULT_STATUS,DEFAULT_LEDGER

def test_status():
    c=_read_campaign_config(rclone_access_token())
    status=json.loads(DEFAULT_STATUS.read_text()) if DEFAULT_STATUS.is_file() else {}
    ledger=json.loads(DEFAULT_LEDGER.read_text()) if DEFAULT_LEDGER.is_file() else {}
    entries=ledger.get('entries') or {}
    from collections import Counter
    counts=Counter(str(v.get('status') or '') for v in entries.values())
    print(json.dumps({'start_at':c['start_at'].isoformat(),'cadence_seconds':c['cadence_seconds'],'daily_cap':c['daily_cap'],'window':[c['window_start_hour'],c['window_end_hour']],'content_version':c['content_version'],'sender':c['sender_email'],'subject':c['subject'],'status':status,'ledger_counts':dict(counts),'ledger_entries':len(entries)},ensure_ascii=False,indent=2))
    assert c['sender_email']=='hameedfarah@gmail.com'
