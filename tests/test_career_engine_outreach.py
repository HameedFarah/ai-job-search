import hashlib, json
from pathlib import Path
import pytest
from career_engine import gmail
from career_engine.outreach import confirmation_token, process_queue

def queue(tmp_path, **changes):
    pdf=tmp_path/'cv.pdf'; pdf.write_bytes(b'%PDF approved')
    row={'outreach_id':'o-1','company':'Acme','primary_email':'hr@acme.example','verification':{'status':'valid','evidence':'zero-bounce'},'status':'queued','subject':'Interest - Acme','body':'Hello Acme.','cv_pdf_path':str(pdf),'cv_pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}
    row.update(changes); path=tmp_path/'queue.json'; path.write_text(json.dumps([row])); return path

def test_default_preflight_never_sends(tmp_path, monkeypatch):
    q=queue(tmp_path); monkeypatch.setattr(gmail,'send_application_message',lambda _: pytest.fail('send'))
    out=process_queue(q,tmp_path/'ledger.json'); assert out['mode']=='preflight'; assert out['sent']==0

def test_apply_requires_token_and_profile(tmp_path, monkeypatch):
    q=queue(tmp_path); token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest())
    with pytest.raises(ValueError): process_queue(q,tmp_path/'l.json',apply=True,confirmation='wrong')
    monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: False)
    with pytest.raises(RuntimeError): process_queue(q,tmp_path/'l.json',apply=True,confirmation=token)

@pytest.mark.parametrize('status', ['', 'catch-all', 'catch_all', 'unknown', 'invalid', 'spamtrap', 'abuse', 'do_not_mail', 'rejected'])
def test_only_valid_default(status,tmp_path):
    q=queue(tmp_path,verification={'status':status,'evidence':'x'}); result=process_queue(q,tmp_path/'l.json'); assert result['failed']==1

def test_catch_all_override_is_distinguishable(tmp_path):
    q=queue(tmp_path,verification={'status':'catch_all','evidence':'x'}); result=process_queue(q,tmp_path/'l.json',allow_catch_all=True)
    assert result['processed']==1; assert json.loads((tmp_path/'l.json').read_text())['entries']['o-1']['status']=='preflighted'

def test_idempotent_and_pacing(tmp_path,monkeypatch):
    q=queue(tmp_path); pdf=tmp_path/'cv2.pdf'; pdf.write_bytes(b'%PDF 2'); rows=json.loads(q.read_text()); rows.append({**rows[0],'outreach_id':'o-2','primary_email':'two@acme.example','cv_pdf_path':str(pdf),'cv_pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}); q.write_text(json.dumps(rows))
    now=[0.0]; sleeps=[]; sent=[]; monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True); monkeypatch.setattr(gmail,'send_application_message',lambda raw: sent.append(raw) or {'id':str(len(sent))})
    token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest()); process_queue(q,tmp_path/'l.json',apply=True,confirmation=token,clock=lambda:now[0],sleep=lambda n:(sleeps.append(n),now.__setitem__(0,now[0]+n)))
    assert sleeps == [180.0]; assert len(sent)==2
    process_queue(q,tmp_path/'l.json',apply=True,confirmation=token,clock=lambda:now[0],sleep=lambda n:None); assert len(sent)==2

def test_quota_stops_and_checkpoints(tmp_path,monkeypatch):
    q=queue(tmp_path); monkeypatch.setattr(gmail,'verify_authenticated_mailbox',lambda: True); monkeypatch.setattr(gmail,'send_application_message',lambda _: (_ for _ in ()).throw(RuntimeError('quota exceeded')))
    token=confirmation_token(hashlib.sha256(q.read_bytes()).hexdigest()); result=process_queue(q,tmp_path/'l.json',apply=True,confirmation=token); assert result['stopped_on_error']; assert json.loads((tmp_path/'l.json').read_text())['entries']['o-1']['status']=='failed'
