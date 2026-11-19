"""Transaction-fenced durable effect claims with a bounded lease.

The coordinator serializes claims using SQLite BEGIN IMMEDIATE; no claim of
multi-node/global-clock safety. Receiver-side dedup is required for effects.
"""
from __future__ import annotations
import sqlite3, time, uuid
from .effect_outbox import EffectCoordinator

class LeasedCoordinator(EffectCoordinator):
    def __init__(self, path, clock=None):
        super().__init__(path)
        self.clock = clock or time.time
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS claim_lease (
                effect_id TEXT PRIMARY KEY, token TEXT NOT NULL,
                expires_at REAL NOT NULL)''')

    def claim_leased(self, effect_id: str, lease_seconds: float = 30.0):
        if not 0 < lease_seconds <= 86400: raise ValueError('lease_seconds must be (0,86400]')
        token = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT workflow,generation,state FROM outbox WHERE effect_id=?', (effect_id,)).fetchone()
            if row is None or row['state'] == 'ACKED': return None
            owner = db.execute('SELECT generation FROM workflow_epoch WHERE workflow=?', (row['workflow'],)).fetchone()
            if owner is None or owner['generation'] != row['generation']: raise ValueError('generation fenced')
            previous = db.execute('SELECT expires_at FROM claim_lease WHERE effect_id=?', (effect_id,)).fetchone()
            now = self.clock()
            if previous is not None and previous['expires_at'] > now: return None
            db.execute('''INSERT INTO claim_lease(effect_id,token,expires_at)
                VALUES (?,?,?) ON CONFLICT(effect_id) DO UPDATE SET
                token=excluded.token, expires_at=excluded.expires_at''',
                (effect_id,token,now+lease_seconds))
            db.execute("UPDATE outbox SET state='CLAIMED',dispatch_token=?,attempts=attempts+1 WHERE effect_id=?",(token,effect_id))
            return token

    def ack_leased(self, effect_id: str, token: str):
        # This avoids a SELECT / UPDATE time-of-check gap across processes.
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT o.workflow,o.generation,o.state,o.dispatch_token,l.expires_at,
                l.token FROM outbox o JOIN claim_lease l USING(effect_id) WHERE o.effect_id=?''',(effect_id,)).fetchone()
            if (row is None or row['state']!='CLAIMED' or row['token']!=token
                or row['dispatch_token']!=token or row['expires_at'] <= self.clock()):
                raise ValueError('expired or fenced acknowledgment')
            owner=db.execute('SELECT generation FROM workflow_epoch WHERE workflow=?',(row['workflow'],)).fetchone()
            if owner is None or owner['generation']!=row['generation']:
                raise ValueError('stale generation acknowledgement')
            db.execute("UPDATE outbox SET state='ACKED' WHERE effect_id=?",(effect_id,))
            db.execute('DELETE FROM claim_lease WHERE effect_id=?',(effect_id,))
