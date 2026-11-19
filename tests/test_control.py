import os,signal,time
from fastapi.testclient import TestClient
from src.control import Controller,run_one,alive
from src.api import app

def test_idempotency_and_stale_fence(tmp_path):
    c=Controller(tmp_path/'a.db')
    assert c.deploy('demo','v1','key1')==1
    assert c.deploy('demo','v1','key1')==1
    j=c.claim(lease=10)
    c.deploy('demo','v2','key2')
    assert c.promote(j,5555,123)==False
    assert c.claim() is not None
    assert any(x['event']=='FENCED' for x in c.audit())

def test_real_http_reconcile(tmp_path):
    c=Controller(tmp_path/'real.db')
    pids=[]
    try:
        c.deploy('svc','first','a');r1=run_one(c);pids.append(r1['pid'])
        assert alive(r1['port'],'first')
        c.deploy('svc','second','b');r2=run_one(c);pids.append(r2['pid'])
        assert c.status('svc')['active']['version']=='second'
        assert alive(r2['port'],'second')
        assert len([x for x in c.audit() if x['event']=='PROMOTED'])==2
    finally:
        for pid in pids:
            try:os.kill(pid,signal.SIGTERM)
            except ProcessLookupError:pass

def test_http_endpoints(tmp_path,monkeypatch):
    monkeypatch.setenv('CONTROL_DB',str(tmp_path/'http.db'))
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        ans=client.post('/deploy',json={'service':'web','version':'v1','idempotency_key':'h1'})
        assert ans.status_code==200
        assert client.get('/status/web').json()['desired']['generation']==1
        assert client.post('/deploy',json={'service':'web','version':'v2','idempotency_key':'h1'}).status_code==400
