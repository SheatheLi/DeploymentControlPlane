"""Real OS-process contention against the SQLite-backed effect protocol.

Not a distributed consensus test: every process uses the *same local filesystem*
SQLite database. An actual independent process must observe an active lease;
crash/reclaim and duplicate receiver effects are validated across process death.
"""
import multiprocessing as mp
import queue
import time

from src.lease_claim import LeasedCoordinator
from src.effect_outbox import IdempotentReceiver


def _contend(db_path, received, begin, result, worker_number):
    # Each child has its own SQLite connection and its own interpreter state.
    try:
        received.put(worker_number)
        if not begin.wait(12):
            raise TimeoutError('parent never released contenders')
        token = LeasedCoordinator(db_path, clock=lambda: 1000.0).claim_leased('deploy:a', 30.0)
        result.put((worker_number, token, None))
    except Exception as exc:
        result.put((worker_number, None, repr(exc)))


def _receive_then_die(receiver_db, result):
    accepted=IdempotentReceiver(receiver_db).apply('deploy:a',{'release': 'v1'})
    result.put(accepted)
    # This worker exits without ever acknowledging its claim to the coordinator.


def test_real_multiprocess_single_live_lease_and_crash_recovery(tmp_path):
    ctx = mp.get_context('spawn')
    db = str(tmp_path / 'coordinator.sqlite3')
    receiver_db = str(tmp_path / 'receiver.sqlite3')
    coordinator=LeasedCoordinator(db, clock=lambda: 1000.0)
    coordinator.change_generation('app', 1)
    assert coordinator.enqueue('app', 1, 'deploy:a', {'release':'v1'})

    begin=ctx.Event()
    received=ctx.Queue()
    result=ctx.Queue()
    processes=[ctx.Process(target=_contend,args=(db, received, begin, result, n)) for n in range(8)]
    try:
        for p in processes:
            p.start()
        ready={received.get(timeout=12) for _ in processes}
        assert ready==set(range(8))
        begin.set()
        results=[result.get(timeout=15) for _ in processes]
        assert not [err for _, _, err in results if err], results
        tokens=[tok for _, tok, _ in results if tok is not None]
        assert len(tokens)==1, results
        assert coordinator.state('deploy:a')=={'state':'CLAIMED','attempts':1}

        # A separate child executes the external receiver's durable side effect.
        delivered=ctx.Queue()
        child=ctx.Process(target=_receive_then_die,args=(receiver_db,delivered))
        child.start()
        assert delivered.get(timeout=10) is True
        child.join(timeout=10)
        assert child.exitcode==0
        assert coordinator.claim_leased('deploy:a', 30.0) is None

        # A resumed worker at 1031 reclaims after the prior 1030 lease expiry.
        restarted=LeasedCoordinator(db,clock=lambda:1031.0)
        new_token=restarted.claim_leased('deploy:a',30.0)
        assert new_token and new_token!=tokens[0]
        assert IdempotentReceiver(receiver_db).apply('deploy:a',{'release':'v1'}) is False
        try:
            restarted.ack_leased('deploy:a',tokens[0])
        except ValueError:
            pass
        else:
            raise AssertionError('expired owner acknowledged effects after reclaim')
        restarted.ack_leased('deploy:a',new_token)
        assert coordinator.state('deploy:a')=={'state':'ACKED','attempts':2}
        assert IdempotentReceiver(receiver_db).count()==1
    finally:
        begin.set()
        for p in processes:
            p.join(timeout=3)
            if p.is_alive():
                p.terminate()
                p.join(timeout=3)
        for p in processes:
            assert p.exitcode==0, ('subprocess did not exit cleanly',p.pid,p.exitcode)
