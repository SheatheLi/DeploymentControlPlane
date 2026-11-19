import os,signal
from src.control import Controller
from src.recovery import spawn_registered,recover,health_with_nonce

def terminate(pid):
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:pass

def test_crash_between_launch_and_promotion_recovers(tmp_path):
    db=tmp_path/'real.db';c=Controller(db)
    c.deploy('backend','alpha','key0');j=c.claim(lease=30)
    r=spawn_registered(c,j)
    try:
        # Simulated crash: destroy controller object before promote.
        new_controller=Controller(db)
        events=recover(new_controller)
        assert events[0]['state']=='ADOPTED'
        assert health_with_nonce(r['port'],'alpha',r['nonce'])
        assert new_controller.status('backend')['active']['generation']==1
        assert recover(new_controller)==[]
    finally:terminate(r['pid'])

def test_stale_launch_is_quarantined_without_pid_kill(tmp_path):
    db=tmp_path/'real.db';c=Controller(db)
    c.deploy('backend','alpha','key0');j=c.claim(lease=30)
    r=spawn_registered(c,j)
    try:
        c.deploy('backend','beta','key1')
        result=recover(Controller(db))
        assert result[0]['state']=='QUARANTINED'
        assert health_with_nonce(r['port'],'alpha',r['nonce']) # no unverified PID kill
        assert c.status('backend')['active'] is None
    finally:terminate(r['pid'])
