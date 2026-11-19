"""Cross-transaction race regression: generation advance vs enqueue.

This checks the invariant over the database's serialization order, rather
than merely proving that a normal enqueue succeeds.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading

from src.effect_outbox import EffectCoordinator


def test_enqueue_is_fenced_in_same_transaction_as_generation_check(tmp_path):
    path = tmp_path / 'outbox.db'
    c = EffectCoordinator(path)
    c.change_generation('app', 1)
    # A second connection holds the SQLite writer lock before either operation.
    # Once released, either serial order is legal, but neither may produce an
    # effect whose generation was stale at its insertion's linearization point.
    guard = sqlite3.connect(path)
    guard.execute('BEGIN IMMEDIATE')
    ready = threading.Barrier(3)

    def insert_old():
        ready.wait()
        try:
            return c.enqueue('app', 1, 'effect-old', {'version': 1})
        except ValueError:
            return 'FENCED'

    def advance():
        ready.wait()
        c.change_generation('app', 2)
        return 'ADVANCED'

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(insert_old)
        b = pool.submit(advance)
        ready.wait()
        guard.commit()
        guard.close()
        assert b.result(timeout=8) == 'ADVANCED'
        assert a.result(timeout=8) in (True, 'FENCED')
    with c.db() as db:
        assert db.execute("SELECT generation FROM workflow_epoch WHERE workflow='app'").fetchone()[0] == 2
        rows = db.execute('SELECT generation FROM outbox').fetchall()
        assert all(row[0] == 1 for row in rows)


def test_claim_does_not_allow_two_live_claimers(tmp_path):
    c=EffectCoordinator(tmp_path/'outbox.db')
    c.change_generation('app', 1)
    c.enqueue('app',1,'task',{'kind':'deploy'})
    barrier=threading.Barrier(3)
    def claim():
        barrier.wait()
        return c.claim('task')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(claim)
        b=pool.submit(claim)
        barrier.wait()
        t1=a.result(timeout=8)
        t2=b.result(timeout=8)
    # The unleased API historically allows reclaims for crash recovery.
    # Both calls may return tokens, but only the newest token is authoritative.
    assert t1 != t2
    with c.db() as db:
        latest=db.execute('SELECT dispatch_token FROM outbox WHERE effect_id=?',('task',)).fetchone()[0]
    for t in (t1,t2):
        if t == latest:
            c.ack('task',t)
        else:
            try: c.ack('task',t)
            except ValueError: pass
            else: assert False,'stale token accepted'
    assert c.state('task')['state']=='ACKED'


def test_epoch_advance_committed_before_enqueue_must_reject_old_epoch(tmp_path):
    """Force an advancing generation to commit before a competing enqueue."""
    c = EffectCoordinator(tmp_path/'outbox.db')
    c.change_generation('app', 1)
    writer = sqlite3.connect(c.path)
    writer.execute('BEGIN IMMEDIATE')
    writer.execute('UPDATE workflow_epoch SET generation=2 WHERE workflow=?',('app',))
    ready = threading.Event()
    def insert():
        ready.set()
        try:
            c.enqueue('app',1,'stale',{'v':1})
        except ValueError:
            return 'FENCED'
        return 'INCORRECTLY_ACCEPTED'
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(insert)
        assert ready.wait(3)
        writer.commit()
        writer.close()
        assert future.result(timeout=8)=='FENCED'
    with c.db() as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0


def test_legacy_check_then_insert_exhibits_stale_generation(tmp_path):
    """Pinned counterexample to the pre-fix algorithm, not a claim about new code."""
    c=EffectCoordinator(tmp_path/'legacy.db')
    c.change_generation('app',1)
    # Legacy owner SELECT happened first and left no write lock held.
    with c.db() as db:
        observed=db.execute('SELECT generation FROM workflow_epoch WHERE workflow=?',('app',)).fetchone()[0]
    assert observed==1
    c.change_generation('app',2)
    # Legacy insert had no in-transaction generation condition.
    with c.db() as db:
        db.execute("INSERT INTO outbox(effect_id,workflow,generation,payload) VALUES(?,?,?,?)",('stale','app',observed,'{}'))
    with c.db() as db:
        epoch=db.execute("SELECT generation FROM workflow_epoch WHERE workflow='app'").fetchone()[0]
        effect_epoch=db.execute("SELECT generation FROM outbox WHERE effect_id='stale'").fetchone()[0]
    assert (epoch,effect_epoch)==(2,1)
