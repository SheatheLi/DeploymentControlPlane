"""Real local HTTP execution, independent of the unavailable PostgreSQL daemon."""
import json
import os
import secrets
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from src.control import free_port
from src.pg_worker import launch, send_effect, cycle
from src.webhook_sink import make_server

@pytest.fixture
def receiver(tmp_path):
    port=free_port()
    db_path=str(tmp_path/'effects.sqlite')
    server=make_server(port,db_path,'test-effect-token')
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:yield f'http://127.0.0.1:{port}',db_path
    finally:server.shutdown();server.server_close();thread.join(timeout=4)

def test_independent_receiver_commits_only_one_effect(receiver):
    url,path=receiver
    event={'effect_id':'release:api:7','payload':{'service':'api','generation':7,'version':'v7'}}
    first=send_effect(event,url,'test-effect-token')
    repeated=send_effect(event,url,'test-effect-token')
    assert first['duplicate'] is False
    assert repeated['duplicate'] is True
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM applied').fetchone()[0]==1

def test_receiver_rejects_key_reused_with_different_payload(receiver):
    url,_=receiver
    send_effect({'effect_id':'release:api:1','payload':{'service':'api','generation':1,'version':'a'}},url,'test-effect-token')
    with pytest.raises(urllib.error.HTTPError) as error:
        send_effect({'effect_id':'release:api:1','payload':{'service':'api','generation':1,'version':'b'}},url,'test-effect-token')
    assert error.value.code==409

def test_receiver_rejects_invalid_secret(receiver):
    url,_=receiver
    with pytest.raises(urllib.error.HTTPError) as error:
        send_effect({'effect_id':'release:api:1','payload':{}},url,'wrong')
    assert error.value.code==403

def test_fixed_release_child_serves_identity_bound_health():
    child,endpoint=launch({'version':'v17'},timeout=4)
    try:
        with urllib.request.urlopen(endpoint+'/health',timeout=2) as response:
            data=json.loads(response.read())
        assert data['status']=='healthy' and data['version']=='v17'
        assert len(data['launch_nonce'])==32
    finally:
        child.terminate();child.wait(timeout=3)

class FakeControl:
    """Only an adapter fake: this test does NOT validate PG transactions."""
    def __init__(self):
        self.claimed=False
        self.events=0
        self.promoted=False
        self.acked=False
    def claim_release(self,worker):
        if self.claimed:return None
        self.claimed=True
        return {'id':1,'service':'api','version':'v2','generation':2,'lease_token':1}
    def promote(self,job,endpoint,pid):
        with urllib.request.urlopen(endpoint+'/health',timeout=2) as res:
            assert json.load(res)['version']=='v2'
        self.promoted=True
        return True
    def claim_effect(self):
        if not self.promoted or self.events:return None
        self.events+=1
        return {'effect_id':'release:api:2','payload':{'service':'api','generation':2,'version':'v2'},'lease_token':1}
    def ack_effect(self,effect):
        self.acked=True
        return True

def test_real_worker_and_external_effect_are_separate_http_processes(receiver):
    url,path=receiver
    fake=FakeControl()
    result=cycle(fake,'worker-A',url,'test-effect-token')
    assert result['release']['status']=='PROMOTED'
    assert result['effect']['ack'] is True
    assert fake.acked is True
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM applied').fetchone()[0]==1
    # Do not leave the local test release process behind.
    import signal
    pid=result['release']['pid']
    os.kill(pid,signal.SIGTERM)
    os.waitpid(pid,0)


def test_no_effect_until_promotion(receiver):
    url,path=receiver
    class Rejected(FakeControl):
        def promote(self,*args):return False
    result=cycle(Rejected(),'worker-B',url,'test-effect-token')
    assert result['release']['status']=='FENCED' and result['effect'] is None
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM applied').fetchone()[0]==0


def test_receiver_fences_new_stale_generation(receiver):
    url,path=receiver
    newest={'effect_id':'release:api:3','payload':{'service':'api','generation':3,'version':'v3'}}
    assert send_effect(newest,url,'test-effect-token')['duplicate'] is False
    old={'effect_id':'release:api:2','payload':{'service':'api','generation':2,'version':'v2'}}
    with pytest.raises(urllib.error.HTTPError) as exc:
        send_effect(old,url,'test-effect-token')
    assert exc.value.code==409
    # Previously accepted effect may be retried, but cannot be re-applied.
    assert send_effect(newest,url,'test-effect-token')['duplicate'] is True
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM applied').fetchone()[0]==1
        assert db.execute('SELECT generation FROM latest_generation WHERE service=?',('api',)).fetchone()[0]==3


def test_launch_keeps_port_reserved_across_spawn(monkeypatch):
    """A simultaneous process cannot take the listener while child starts.

    Probe the parent->child hand-off through the real HTTP process. This test
    deliberately uses no database and does not claim PostgreSQL execution.
    """
    from concurrent.futures import ThreadPoolExecutor
    def probe(n):
        child, endpoint = launch({'version': f'v{n}'}, timeout=6)
        try:
            with urllib.request.urlopen(endpoint + '/health', timeout=2) as reply:
                payload = json.loads(reply.read())
            return endpoint, payload, child.pid
        finally:
            child.terminate()
            child.wait(timeout=3)

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(probe, range(10)))
    assert len({endpoint for endpoint, _, _ in results}) >= 1
    assert {item['version'] for _, item, _ in results} == {f'v{i}' for i in range(10)}
    assert all(item['status'] == 'healthy' and len(item['launch_nonce']) == 32
               for _, item, _ in results)
