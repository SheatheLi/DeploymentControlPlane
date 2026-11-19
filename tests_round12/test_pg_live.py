"""True PG integration gates, skipped unless a real PostgreSQL server is supplied.

Run: DCP_TEST_PG_URL=postgresql://... python -m pytest -q tests_round12/test_pg_live.py
Supply a dedicated, disposable PostgreSQL database; no SQLite fallback.
"""
import json
import os
import signal
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import pytest
from src.control import free_port
from src.pg_control import PgControl
from src.pg_worker import launch,send_effect
from src.webhook_sink import make_server

pytestmark=pytest.mark.skipif(not os.getenv('DCP_TEST_PG_URL'),reason='real PostgreSQL DCP_TEST_PG_URL unavailable')

@pytest.fixture
def pg():
    try:import psycopg
    except ImportError:pytest.fail('psycopg3 driver unavailable: live PostgreSQL test MUST fail, not skip')
    db=PgControl(os.environ['DCP_TEST_PG_URL'])
    try:db.initialize()
    except Exception as e:pytest.fail('real PostgreSQL schema initialization failed: '+str(e))
    return db

@pytest.fixture
def server(tmp_path):
    address=free_port();path=str(tmp_path/'external_effects.sqlite')
    s=make_server(address,path,'contract-secret')
    t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
    yield f'http://127.0.0.1:{address}',path
    s.shutdown();s.server_close();t.join(timeout=5)

def test_pg_claim_promote_outbox_ack_and_retry_on_external_crash(pg,server):
    sink,path=server
    service='r'+uuid.uuid4().hex[:16];key='case:'+service
    assert pg.deploy(service,'v1',key)==1
    assert pg.deploy(service,'v1',key)==1
    with pytest.raises(ValueError):pg.deploy(service,'v2',key)
    job=pg.claim_release('integration-worker',lease_seconds=10)
    assert job and job['service']==service
    child,endpoint=launch(job)
    try:
        assert pg.promote(job,endpoint,child.pid)
        assert pg.status(service)['service']['active_version']=='v1'
        effect=pg.claim_effect(lease_seconds=1)
        assert effect and effect['effect_id']==f'release:{service}:1'
        first=send_effect(effect,sink,'contract-secret')
        assert first['duplicate'] is False
        # Deliberately omit ACK, equivalent to worker death after receiver accepted.
        time.sleep(1.3)
        retry=pg.claim_effect(lease_seconds=2)
        assert retry and retry['effect_id']==effect['effect_id']
        assert retry['lease_token']==effect['lease_token']+1
        second=send_effect(retry,sink,'contract-secret')
        assert second['duplicate'] is True
        assert pg.ack_effect(effect) is False
        assert pg.ack_effect(retry) is True
        with sqlite3.connect(path) as d:
            assert d.execute('SELECT count(*) FROM applied').fetchone()[0]==1
    finally:
        child.terminate();child.wait(timeout=4)

def test_pg_release_fence_rejects_expired_claim(pg):
    service='r'+uuid.uuid4().hex[:16]
    pg.deploy(service,'v1','key:'+service)
    old=pg.claim_release('first',lease_seconds=1)
    assert old and old['service']==service
    time.sleep(1.3)
    new=pg.claim_release('second',lease_seconds=20)
    assert new and new['id']==old['id']
    assert new['lease_token']>old['lease_token']
    assert pg.promote(old,'http://127.0.0.1:9',42) is False
    assert pg.status(service)['service']['active_version'] is None
