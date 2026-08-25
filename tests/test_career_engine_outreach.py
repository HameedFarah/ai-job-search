import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from career_engine import gmail
from career_engine.outreach import confirmation_token, process_queue, _riyadh_date

def queue(tmp_path, **changes):
    pdf=tmp_path/'cv.pdf'; pdf.write_bytes(b'%PDF approved')
    row={'outreach_id':'o-1','company':'Acme','priority_tier':1,'primary_email':'hr@acme.example','verification':{'status':'valid','evidence':'zero-bounce','email':'hr@acme.example'},'status':'queued','subject':'Interest - Acme','body':'Hello Acme.','cv_pdf_path':str(pdf),'cv_pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
    row.update(changes); path=tmp_path/'queue.json'; path.write_text(json.dumps([row])); return path

def test_default_preflight_never_sends(tmp_path, monkeypatch):
    q=queue(tmp_path); monkeypatch.setattr(gmail,'send_application_message',lambda _: pytest.fail('send'))
    out=process_queue(q,tmp_path/'ledger.json'); assert out['mode']=='preflight'; assert out['sent']==0

def test_apply_requires_token_and_profile(tmp_path, monkeypatch):
    q=queue(tmp_path); token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest())
    with pytest.raises(ValueError): process_queue(q,tmp_path/'l.json',apply=True,confirmation='wrong')
    monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: False)
    with pytest.raises(RuntimeError): process_queue(q,tmp_path/'l.json',apply=True,confirmation=token)

def test_apply_requires_distinct_prior_preflight(tmp_path, monkeypatch):
    q=queue(tmp_path); token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest())
    monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True)
    sent=[]; monkeypatch.setattr(gmail,'send_application_message',lambda raw: sent.append(raw) or {'id':'1'})
    result=process_queue(q,tmp_path/'l.json',apply=True,confirmation=token)
    assert result['failed'] == 1 and not sent

def test_preflight_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    q=queue(tmp_path); ledger=tmp_path/'l.json'; process_queue(q,ledger)
    rows=json.loads(q.read_text()); rows[0]['body']='changed'; q.write_text(json.dumps(rows))
    token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest())
    monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True)
    sent=[]; monkeypatch.setattr(gmail,'send_application_message',lambda raw: sent.append(raw) or {'id':'1'})
    result=process_queue(q,ledger,apply=True,confirmation=token)
    assert result['failed'] == 1 and not sent

@pytest.mark.parametrize('name', ['max_run','max_per_hour','max_day'])
def test_caps_must_be_positive(tmp_path, name):
    q=queue(tmp_path)
    with pytest.raises(ValueError, match='must be positive'):
        process_queue(q,tmp_path/'l.json',**{name:0})

def test_priority_tier_is_required(tmp_path):
    q=queue(tmp_path, priority_tier=None)
    result=process_queue(q,tmp_path/'l.json')
    assert result['failed'] == 1

def test_daily_cap_uses_riyadh_calendar_date():
    assert _riyadh_date('2026-08-23T23:30:00+00:00') == '2026-08-24'
    assert _riyadh_date('2026-08-24T00:30:00+00:00') == '2026-08-24'

@pytest.mark.parametrize('status', ['', 'catch-all', 'catch_all', 'unknown', 'invalid', 'spamtrap', 'abuse', 'do_not_mail', 'rejected'])
def test_only_valid_default(status,tmp_path):
    q=queue(tmp_path,verification={'status':status,'evidence':'x','email':'hr@acme.example'}); result=process_queue(q,tmp_path/'l.json'); assert result['failed']==1

def test_catch_all_override_is_distinguishable(tmp_path):
    q=queue(tmp_path,verification={'status':'catch_all','evidence':'x','email':'hr@acme.example'}); result=process_queue(q,tmp_path/'l.json',allow_catch_all=True)
    assert result['processed']==1; assert json.loads((tmp_path/'l.json').read_text())['entries']['o-1']['status']=='preflighted'

def test_idempotent_and_pacing(tmp_path,monkeypatch):
    q=queue(tmp_path); pdf=tmp_path/'cv2.pdf'; pdf.write_bytes(b'%PDF 2'); rows=json.loads(q.read_text()); rows.append({**rows[0],'outreach_id':'o-2','primary_email':'two@acme.example','verification':{'status':'valid','evidence':'zero-bounce','email':'two@acme.example'},'cv_pdf_path':str(pdf),'cv_pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}); q.write_text(json.dumps(rows))
    now=[0.0]; sleeps=[]; sent=[]; monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True); monkeypatch.setattr(gmail,'send_application_message',lambda raw: sent.append(raw) or {'id':str(len(sent))})
    ledger=tmp_path/'l.json'; process_queue(q,ledger)
    token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest()); process_queue(q,ledger,apply=True,confirmation=token,clock=lambda:now[0],sleep=lambda n:(sleeps.append(n),now.__setitem__(0,now[0]+n)))
    assert sleeps == [180.0]; assert len(sent)==2
    process_queue(q,ledger,apply=True,confirmation=token,clock=lambda:now[0],sleep=lambda n:None); assert len(sent)==2

def test_missing_cv_hash_fails_closed(tmp_path):
    q=queue(tmp_path, cv_pdf_sha256='')
    result=process_queue(q,tmp_path/'l.json')
    assert result['failed']==1
    assert 'cv_pdf_sha256 is required' in json.loads((tmp_path/'l.json').read_text())['entries']['o-1']['failure_reason']

def test_verification_must_bind_exact_recipient(tmp_path):
    q=queue(tmp_path, verification={'status':'valid','evidence':'zero-bounce','email':'other@acme.example'})
    result=process_queue(q,tmp_path/'l.json')
    assert result['failed']==1
    assert 'does not match primary_email' in json.loads((tmp_path/'l.json').read_text())['entries']['o-1']['failure_reason']

def test_send_without_gmail_message_id_never_records_sent(tmp_path,monkeypatch):
    q=queue(tmp_path); ledger=tmp_path/'l.json'; process_queue(q,ledger)
    token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest())
    monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True)
    monkeypatch.setattr(gmail,'send_application_message',lambda _: {'threadId':'thread-only'})
    result=process_queue(q,ledger,apply=True,confirmation=token)
    entry=json.loads(ledger.read_text())['entries']['o-1']
    assert result['failed']==1 and result['sent']==0
    assert entry['status']=='failed' and 'no message ID' in entry['failure_reason']

def test_rolling_hourly_cap_counts_historical_sends(tmp_path,monkeypatch):
    q=queue(tmp_path); ledger=tmp_path/'l.json'; process_queue(q,ledger)
    now=datetime(2026,8,25,5,0,0,tzinfo=timezone.utc)
    data=json.loads(ledger.read_text())
    data['entries']['old-send']={'status':'sent','sent_at':'2026-08-25T04:30:00+00:00','gmail_message_id':'old'}
    ledger.write_text(json.dumps(data))
    token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest())
    monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True)
    sent=[]; monkeypatch.setattr(gmail,'send_application_message',lambda raw: sent.append(raw) or {'id':'new'})
    result=process_queue(q,ledger,apply=True,confirmation=token,max_per_hour=1,wall_clock=lambda:now)
    assert result.get('capped') is True and result['sent']==0 and not sent

def test_quota_stops_and_checkpoints(tmp_path,monkeypatch):
    q=queue(tmp_path); monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True); monkeypatch.setattr(gmail,'send_application_message',lambda _: (_ for _ in ()).throw(RuntimeError('quota exceeded')))
    ledger=tmp_path/'l.json'; process_queue(q,ledger)
    token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest()); result=process_queue(q,ledger,apply=True,confirmation=token); assert result['stopped_on_error']; assert json.loads(ledger.read_text())['entries']['o-1']['status']=='failed'
