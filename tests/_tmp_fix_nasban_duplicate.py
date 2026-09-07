from __future__ import annotations
import json
from urllib.parse import quote
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID
from career_engine.outreach_reconciler import QUEUE_SHEET_NAME,_read_queue_sheet

KEEP='OASQ-BALADY-EDE3C8BF5E40'
DROP={'OASQ-2BE43B88961C','OASQ-041878FD428D'}

def test_fix_nasban_duplicate():
    token=rclone_access_token(); rows=_read_queue_sheet(token); hdr=list(rows[0].keys()) if rows else []
    assert hdr
    positions={}
    for i,r in enumerate(rows,start=2):
        q=str(r.get('Queue_ID') or '')
        if q in DROP or q==KEEP: positions[q]=(i,r)
    assert set(positions)==DROP|{KEEP}
    def col(name): return chr(ord('A')+hdr.index(name))
    # Keep the direct-hiring mailbox but mark cross-source REGA coverage so sorting remains REGA-first.
    kr,keep=positions[KEEP]
    source=str(keep.get('Source') or '')
    evidence=str(keep.get('Evidence_or_Notes') or '')
    new_source='REGA + '+source if 'REGA' not in source.upper() else source
    new_ev=(evidence+' | cross_source_rega_company_coverage=nasban.com.sa | route_preference=direct_hiring_over_generic').strip(' |')
    for name,value in [('Source',new_source),('Evidence_or_Notes',new_ev)]:
        rng=f"'{QUEUE_SHEET_NAME}'!{col(name)}{kr}"
        sheets_request(token,'PUT',f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{quote(rng,safe='!:')}?valueInputOption=RAW",{'range':rng,'majorDimension':'ROWS','values':[[value]]})
    for q in DROP:
        rr,row=positions[q]
        rng=f"'{QUEUE_SHEET_NAME}'!{col('Status')}{rr}"
        sheets_request(token,'PUT',f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{quote(rng,safe='!:')}?valueInputOption=RAW",{'range':rng,'majorDimension':'ROWS','values':[['SKIPPED_DUPLICATE_BETTER_ROUTE']]})
    reread={str(r.get('Queue_ID') or ''):r for r in _read_queue_sheet(token) if str(r.get('Queue_ID') or '') in DROP|{KEEP}}
    print(json.dumps({q:{'email':r.get('Email'),'status':r.get('Status'),'source':r.get('Source')} for q,r in reread.items()},ensure_ascii=False,indent=2))
    assert reread[KEEP]['Status']=='PENDING'
    assert 'REGA' in str(reread[KEEP]['Source']).upper()
    assert all(reread[q]['Status']=='SKIPPED_DUPLICATE_BETTER_ROUTE' for q in DROP)
