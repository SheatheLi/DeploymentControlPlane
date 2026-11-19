from fastapi.testclient import TestClient
from src.api import app
from src.lease_claim import LeasedCoordinator

def test_worker_claim_api_protection_and_single_owner(tmp_path,monkeypatch):
    db=tmp_path/'effects.sqlite'
    monkeypatch.setenv('EFFECT_DB',str(db))
    monkeypatch.setenv('WORKER_INTERNAL_TOKEN','test-private-token')
    c=LeasedCoordinator(db);c.change_generation('svc',1);c.enqueue('svc',1,'effect-1',{'v':1})
    client=TestClient(app)
    assert client.post('/internal/effects/claim',json={'effect_id':'effect-1'}).status_code==403
    head={'X-Worker-Token':'test-private-token'}
    first=client.post('/internal/effects/claim',headers=head,json={'effect_id':'effect-1'}).json()
    assert first['claimed'] and first['token']
    second=client.post('/internal/effects/claim',headers=head,json={'effect_id':'effect-1'}).json()
    assert second['claimed'] is False
    assert client.post('/internal/effects/ack',headers=head,json={'effect_id':'effect-1','token':'bad'}).status_code==409
    assert client.post('/internal/effects/ack',headers=head,json={'effect_id':'effect-1','token':first['token']}).json()['acknowledged']
    assert client.post('/internal/effects/claim',headers=head,json={'effect_id':'effect-1'}).json()['claimed'] is False
