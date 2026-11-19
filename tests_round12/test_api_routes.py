"""API contract against a fake DB adapter; does not imply PostgreSQL PASS."""
from fastapi.testclient import TestClient
from src import pg_api

class FakePg:
    def deploy(self,s,v,k):
        if k=='collision':raise ValueError('idempotency collision')
        return 12
    def status(self,s=None):
        if s:return {'service':{'name':s,'generation':12},'jobs':[],'effects':[]}
        return [{'name':'api','generation':12,'desired_version':'v12','active_generation':12}]
    def audit(self):return [{'id':1,'event':'REQUESTED','service':'api','generation':12}]

def test_routes_are_versioned_and_admin_guarded(monkeypatch):
    monkeypatch.setenv('DCP_ADMIN_TOKEN','test-admin')
    monkeypatch.setattr(pg_api,'db',lambda:FakePg())
    client=TestClient(pg_api.app)
    auth={'X-Admin-Token':'test-admin'}
    res=client.post('/api/deploy',headers=auth,
        json={'service':'api','version':'v12','idempotency_key':'fresh'})
    assert res.status_code==200 and res.json()['generation']==12
    assert client.get('/api/services',headers=auth).json()[0]['name']=='api'
    assert client.get('/api/status/api',headers=auth).json()['service']['generation']==12
    assert client.get('/api/audit',headers=auth).json()[0]['event']=='REQUESTED'
    assert client.post('/api/deploy',headers=auth,
        json={'service':'api','version':'v12','idempotency_key':'collision'}).status_code==409
    assert client.get('/api/audit').status_code==403
