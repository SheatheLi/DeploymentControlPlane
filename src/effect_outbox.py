"""Durable cross-database effect coordination for deployment/workflow workers.

Exactly-once effects are NOT guaranteed without a cooperating idempotent receiver.
The local journal, fence, and receiver dedup make this statement executable.
This is local SQLite research, not a Temporal SDK or Kubernetes upstream patch.
"""
from __future__ import annotations
from dataclasses import dataclass
import json,sqlite3,uuid

SCHEMA='''
CREATE TABLE IF NOT EXISTS outbox (
  effect_id TEXT PRIMARY KEY,
  workflow TEXT NOT NULL,
  generation INTEGER NOT NULL,
  payload TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'PENDING',
  dispatch_token TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  CHECK (state IN ('PENDING','CLAIMED','ACKED'))
);
CREATE TABLE IF NOT EXISTS workflow_epoch (
  workflow TEXT PRIMARY KEY,
  generation INTEGER NOT NULL
);
'''

class EffectCoordinator:
    def __init__(self,path):
        self.path=str(path)
        with self.db() as db:db.executescript(SCHEMA)
    def db(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.row_factory=sqlite3.Row
        return db
    def change_generation(self,workflow,epoch):
        """Advance fencing generation monotonically across concurrent DB writers.

        Legacy read/validate/write had a TOCTOU race: both versions 2 and 3
        could read 1, then version 2 overwrite already committed version 3.
        Atomic conditional UPSERT is the linearization point. SQLite's one
        writer-at-a-time constraint serializes the conflicting statements.
        """
        if not isinstance(epoch,int) or isinstance(epoch,bool) or epoch<0:
            raise ValueError('generation must be a nonnegative integer')
        with self.db() as db:
            result=db.execute('''INSERT INTO workflow_epoch(workflow,generation) VALUES (?,?)
                ON CONFLICT(workflow) DO UPDATE SET generation=excluded.generation
                WHERE excluded.generation>workflow_epoch.generation''',(workflow,epoch))
            if result.rowcount!=1:raise ValueError('stale generation')
    def enqueue(self,workflow,epoch,effect_id,payload):
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'))
        with self.db() as db:
            # Fence validation and durable enqueue must share a write transaction.
            # Otherwise a concurrent generation advance can commit between them.
            db.execute('BEGIN IMMEDIATE')
            owner=db.execute('SELECT generation FROM workflow_epoch WHERE workflow=?',(workflow,)).fetchone()
            if not owner or owner['generation']!=epoch:raise ValueError('fenced producer')
            old=db.execute('SELECT workflow,generation,payload FROM outbox WHERE effect_id=?',(effect_id,)).fetchone()
            if old:
                if tuple(old)!=(workflow,epoch,raw):raise ValueError('effect key collision')
                return False
            db.execute('INSERT INTO outbox(effect_id,workflow,generation,payload) VALUES (?,?,?,?)',(effect_id,workflow,epoch,raw))
            return True
    def claim(self,effect_id):
        token=uuid.uuid4().hex
        with self.db() as db:
            # Serialize the claim with generation advances and rival claimers.
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT workflow,generation,state FROM outbox WHERE effect_id=?',(effect_id,)).fetchone()
            if not row or row['state']=='ACKED':return None
            owner=db.execute('SELECT generation FROM workflow_epoch WHERE workflow=?',(row['workflow'],)).fetchone()
            if not owner or owner['generation']!=row['generation']:raise ValueError('fenced claim')
            db.execute("UPDATE outbox SET state='CLAIMED',dispatch_token=?,attempts=attempts+1 WHERE effect_id=?",(token,effect_id))
            return token
    def ack(self,effect_id,token):
        with self.db() as db:
            # The token/generation check must not race an intervening claim.
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT workflow,generation,state,dispatch_token FROM outbox WHERE effect_id=?',(effect_id,)).fetchone()
            if not row or row['state']!='CLAIMED' or row['dispatch_token']!=token:raise ValueError('stale or unknown acknowledgement')
            owner=db.execute('SELECT generation FROM workflow_epoch WHERE workflow=?',(row['workflow'],)).fetchone()
            if not owner or owner['generation']!=row['generation']:raise ValueError('stale generation acknowledgement')
            db.execute("UPDATE outbox SET state='ACKED' WHERE effect_id=?",(effect_id,))
    def state(self,effect_id):
        with self.db() as db:
            row=db.execute('SELECT state,attempts FROM outbox WHERE effect_id=?',(effect_id,)).fetchone()
            return dict(row) if row else None

class IdempotentReceiver:
    def __init__(self,path):
        self.path=str(path)
        with self.db() as db:db.executescript('CREATE TABLE IF NOT EXISTS applied(effect_id TEXT PRIMARY KEY,payload TEXT NOT NULL);')
    def db(self):return sqlite3.connect(self.path,timeout=5)
    def apply(self,effect_id,payload):
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'))
        with self.db() as db:
            result=db.execute('INSERT OR IGNORE INTO applied VALUES (?,?)',(effect_id,raw))
            previous=db.execute('SELECT payload FROM applied WHERE effect_id=?',(effect_id,)).fetchone()[0]
            if previous!=raw:raise ValueError('idempotency collision on receiver')
            return result.rowcount==1
    def count(self):
        with self.db() as db:return db.execute('SELECT count(*) FROM applied').fetchone()[0]


def crash_window_experiment(path,receiver_path)->dict:
    """Crash after external acceptance, before local ACK. Retry is required."""
    c=EffectCoordinator(path);r=IdempotentReceiver(receiver_path)
    c.change_generation('service',1);c.enqueue('service',1,'deploy:1',{'release':'v1'})
    first=c.claim('deploy:1');assert first
    initial=r.apply('deploy:1',{'release':'v1'})
    # process dies here; a restarted controller must redispatch the same ID.
    restart=EffectCoordinator(path)
    second=restart.claim('deploy:1');assert second!=first
    duplicate=r.apply('deploy:1',{'release':'v1'})
    try: restart.ack('deploy:1',first)
    except ValueError: old_ack_rejected=True
    else:old_ack_rejected=False
    restart.ack('deploy:1',second)
    return dict(receiver_first_accepted=initial,receiver_duplicate_accepted=duplicate,
                stale_ack_rejected=old_ack_rejected,receiver_unique_effects=r.count(),
                attempts=restart.state('deploy:1')['attempts'])
