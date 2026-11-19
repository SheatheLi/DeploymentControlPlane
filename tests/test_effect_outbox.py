import pytest
from src.effect_outbox import EffectCoordinator,IdempotentReceiver,crash_window_experiment

def test_cross_db_crash_window(tmp_path):
    out=crash_window_experiment(tmp_path/'controller.db',tmp_path/'receiver.db')
    assert out==dict(receiver_first_accepted=True,receiver_duplicate_accepted=False,
                     stale_ack_rejected=True,receiver_unique_effects=1,attempts=2)

def test_generation_fence_and_payload_collision(tmp_path):
    c=EffectCoordinator(tmp_path/'a.db')
    c.change_generation('x',4)
    assert c.enqueue('x',4,'fx',{'command':'start'})
    assert not c.enqueue('x',4,'fx',{'command':'start'})
    with pytest.raises(ValueError):c.enqueue('x',4,'fx',{'command':'stop'})
    t=c.claim('fx');c.change_generation('x',5)
    with pytest.raises(ValueError):c.ack('fx',t)
    with pytest.raises(ValueError):c.claim('fx')
