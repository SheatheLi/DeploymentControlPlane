from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from src.lease_claim import LeasedCoordinator
from src.effect_outbox import IdempotentReceiver
import pytest


def test_concurrent_claim_and_crash_recovery(tmp_path:Path):
    now = [1000.0]
    path=tmp_path/'coordinator.sqlite';recv_path=tmp_path/'receiver.sqlite'
    c=LeasedCoordinator(path,clock=lambda:now[0]);r=IdempotentReceiver(recv_path)
    c.change_generation('robot:2',3)
    c.enqueue('robot:2',3,'deploy:3',{'version':3})
    # Controlled contention: the DB transaction must allow only one current owner.
    with ThreadPoolExecutor(max_workers=12) as executor:
        tokens=list(executor.map(lambda _:c.claim_leased('deploy:3',40),range(64)))
    owned=[x for x in tokens if x is not None]
    assert len(owned)==1
    assert r.apply('deploy:3',{'version':3})
    # Simulate crashing after receiver commits but before local ack.
    after_crash=LeasedCoordinator(path,clock=lambda:now[0])
    assert after_crash.claim_leased('deploy:3',40) is None
    now[0]+=41
    recovered=after_crash.claim_leased('deploy:3',40)
    assert recovered and recovered != owned[0]
    assert not r.apply('deploy:3',{'version':3})
    with pytest.raises(ValueError): after_crash.ack_leased('deploy:3',owned[0])
    after_crash.ack_leased('deploy:3',recovered)
    assert r.count()==1
    assert c.state('deploy:3')=={'state':'ACKED','attempts':2}


def test_generation_fence_and_lease_expiry(tmp_path):
    now=[150.0];c=LeasedCoordinator(tmp_path/'db',clock=lambda:now[0]);c.change_generation('x',1)
    c.enqueue('x',1,'e',{'v':1});token=c.claim_leased('e',4)
    now[0]+=4
    with pytest.raises(ValueError):c.ack_leased('e',token)
    c.change_generation('x',2)
    with pytest.raises(ValueError):c.claim_leased('e',4)
    with pytest.raises(ValueError):c.ack_leased('e',token)
