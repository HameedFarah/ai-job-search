from __future__ import annotations
import json
from pathlib import Path
from runtime.outscraper_sheet_runner import read_queue,rclone_access_token

OUT=Path('runtime/acceptance/rega-ready-promotion-20260907/candidates.jsonl')
READY={'READY_VERIFIED_OUTSCRAPER','DRAFT_VERIFIED_READY_TO_SEND','READY_TO_SEND','READY'}

def test_prepare():
    q=read_queue(rclone_access_token()); out=[]
    for r in q:
        src=str(r.get('Source_Dataset') or '')
        if 'REGA' not in src.upper(): continue
        if str(r.get('Send_State') or '').upper() not in READY: continue
        if str(r.get('Outscraper_Status') or '').upper()!='RECEIVING': continue
        email=str(r.get('Email') or '').strip().lower(); company=str(r.get('Company_or_Office') or '').strip()
        if not email or not company: continue
        local=email.split('@',1)[0].replace('.','').replace('-','').replace('_','')
        if local in {'customercare','customerservice','cs'}: continue
        domain=email.rsplit('@',1)[1]
        out.append({
            'queue_id':'REGA-PROMOTE-'+str(r.get('Queue_ID') or ''),
            'email':email,'company_name':company,'domain':domain,
            'identity_gate':'confirmed_official_email','verification':'RECEIVING',
            'route_kind':'recruitment' if email.split('@',1)[0] in {'career','careers','jobs','job','hr','recruitment','recruit'} else 'general',
            'source':'REGA_READY_PROMOTION_20260907',
            'evidence':f"source_send_queue={r.get('Queue_ID')} | source_dataset={src} | source_verification={r.get('Source_Verification')} | outscraper={r.get('Outscraper_Verification')}"
        })
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out),encoding='utf-8')
    print(json.dumps({'candidates':len(out),'path':str(OUT),'sample':out[:10]},ensure_ascii=False,indent=2))
    assert out
