import time
from src.control import Controller

def test_expired_lease_cannot_publish_after_takeover(tmp_path):
    c=Controller(tmp_path/'fence.db');c.deploy('svc','v1','key')
    first=c.claim(lease=1)
    with c.tx() as db:db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(time.time()-10,first['id']))
    second=c.claim();assert first['id']==second['id'] and second['lease_token']==first['lease_token']+1
    assert c.promote(first,10001,333)==False
    # Testing CAS only (no real child); successful update requires latest lease token.
    assert c.promote(second,10002,444) is True
    assert c.status('svc')['active']['port']==10002
