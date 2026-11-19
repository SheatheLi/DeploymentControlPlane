"""Actual PostgreSQL control-plane adapter (psycopg 3; no SQLite fallback).

Isolation contract:
* service row lock serializes desired generation changes;
* transaction advisory lock serializes same-key retry before idempotency lookup;
* SKIP LOCKED makes concurrent workers claim distinct jobs;
* token + epoch + unexpired lease fence promotion / ACK;
* outbox written in same transaction as promotion.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")

class PgControl:
    def __init__(self,dsn: str):
        if not dsn:
            raise ValueError("DATABASE_URL is required; PostgreSQL mode never falls back to SQLite")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL requires psycopg[binary]~=3.2") from exc
        self.psycopg = psycopg
        self.dict_row = dict_row
        self.dsn = dsn

    def connection(self):
        return self.psycopg.connect(self.dsn,row_factory=self.dict_row,connect_timeout=5)

    def initialize(self):
        schema=Path(__file__).resolve().parents[1]/"sql"/"postgres.sql"
        with self.connection() as conn:
            with conn.cursor() as cur:
                # Strip full-line comments before splitting simple DDL statements.
                # Do not confuse a semicolon in a SQL comment with a terminator.
                body="\n".join(line for line in schema.read_text(encoding="utf-8").splitlines()
                               if not line.lstrip().startswith("--"))
                for fragment in body.split(";"):
                    if fragment.strip():cur.execute(fragment,prepare=False)

    @staticmethod
    def _audit(cur,service,generation,event,detail=""):
        cur.execute("INSERT INTO audit_events(service,generation,event,detail) VALUES (%s,%s,%s,%s)",
                    (service,generation,event,detail))

    def deploy(self,service:str,version:str,key:str):
        if not _NAME.fullmatch(service) or not _VERSION.fullmatch(version) or not (1<=len(key)<=128):
            raise ValueError("invalid deployment input")
        with self.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # PG transaction-level key lock prevents duplicate-key race even if
                    # different requests target different services.
                    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",("dcp:key:"+key,))
                    cur.execute("SELECT service,version,generation FROM deploy_requests WHERE request_key=%s",(key,))
                    old=cur.fetchone()
                    if old:
                        if (old['service'],old['version'])!=(service,version):
                            raise ValueError("idempotency key reused with different request")
                        return old['generation']
                    cur.execute("INSERT INTO services(name,desired_version,generation) VALUES (%s,%s,1) ON CONFLICT(name) DO NOTHING",(service,version))
                    cur.execute("SELECT generation FROM services WHERE name=%s FOR UPDATE",(service,))
                    prior=cur.fetchone()['generation']
                    cur.execute("SELECT 1 FROM deploy_requests WHERE service=%s",(service,))
                    existed=cur.fetchone() is not None
                    generation=prior+1 if existed else prior
                    cur.execute("UPDATE services SET generation=%s,desired_version=%s,updated_at=now() WHERE name=%s",
                                (generation,version,service))
                    cur.execute("INSERT INTO deploy_requests(request_key,service,version,generation) VALUES (%s,%s,%s,%s)",
                                (key,service,version,generation))
                    cur.execute("INSERT INTO release_jobs(service,version,generation) VALUES (%s,%s,%s)",
                                (service,version,generation))
                    self._audit(cur,service,generation,"REQUESTED",version)
                    return generation

    def claim_release(self,worker:str,lease_seconds:int=30):
        if not 1<=lease_seconds<=600:raise ValueError("lease_seconds out of bounds")
        with self.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute('''SELECT j.id FROM release_jobs j JOIN services s ON s.name=j.service
                        AND s.generation=j.generation
                        WHERE j.state='PENDING' OR (j.state='CLAIMED' AND j.lease_until<now())
                        ORDER BY j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED''')
                    picked=cur.fetchone()
                    if not picked:return None
                    cur.execute('''UPDATE release_jobs SET state='CLAIMED',lease_token=lease_token+1,
                        lease_until=now()+(%s * interval '1 second'),attempts=attempts+1,claimed_by=%s
                        WHERE id=%s RETURNING *''',(lease_seconds,worker,picked['id']))
                    job=cur.fetchone()
                    self._audit(cur,job['service'],job['generation'],"CLAIMED",str(job['lease_token']))
                    return job

    def promote(self,job:dict,endpoint:str,pid:int)->bool:
        """Return False if lease or desired generation changed during health probe."""
        with self.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Lock job row before service to ensure stale claims cannot overwrite.
                    cur.execute('''SELECT j.*, (j.lease_until>now()) AS lease_valid FROM release_jobs j WHERE j.id=%s FOR UPDATE''',(job['id'],))
                    current=cur.fetchone()
                    cur.execute("SELECT generation FROM services WHERE name=%s FOR UPDATE",(job['service'],))
                    service=cur.fetchone()
                    valid=(current is not None and service is not None
                        and current['state']=='CLAIMED' and current['lease_token']==job['lease_token']
                        and current['lease_valid']
                        and service['generation']==job['generation'])
                    if not valid:
                        self._audit(cur,job['service'],job['generation'],"FENCED","promotion rejected")
                        return False
                    cur.execute('''UPDATE services SET active_version=%s, active_generation=%s,
                        active_endpoint=%s,active_pid=%s WHERE name=%s''',
                        (job['version'],job['generation'],endpoint,pid,job['service']))
                    cur.execute("UPDATE release_jobs SET state='PROMOTED' WHERE id=%s",(job['id'],))
                    effect_id=f"release:{job['service']}:{job['generation']}"
                    payload=json.dumps(dict(service=job['service'],version=job['version'],generation=job['generation']))
                    cur.execute('''INSERT INTO effect_outbox(effect_id,service,generation,payload)
                        VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT(effect_id) DO NOTHING''',
                        (effect_id,job['service'],job['generation'],payload))
                    self._audit(cur,job['service'],job['generation'],"PROMOTED",endpoint)
                    return True

    def claim_effect(self,lease_seconds:int=30):
        with self.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute('''UPDATE effect_outbox o SET state='FENCED'
                       FROM services s WHERE o.service=s.name AND o.state IN ('PENDING','CLAIMED')
                       AND (o.generation<>s.generation OR o.generation<>s.active_generation)''')
                    cur.execute('''SELECT o.effect_id FROM effect_outbox o JOIN services s ON s.name=o.service
                        AND s.generation=o.generation AND s.active_generation=o.generation
                        WHERE o.state='PENDING' OR (o.state='CLAIMED' AND o.lease_until<now())
                        ORDER BY o.created_at,o.effect_id LIMIT 1 FOR UPDATE OF o SKIP LOCKED''')
                    row=cur.fetchone()
                    if not row:return None
                    cur.execute('''UPDATE effect_outbox SET state='CLAIMED',lease_token=lease_token+1,
                        lease_until=now()+(%s * interval '1 second'),attempts=attempts+1
                        WHERE effect_id=%s RETURNING *''',(lease_seconds,row['effect_id']))
                    return cur.fetchone()

    def ack_effect(self,effect:dict)->bool:
        with self.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute('''UPDATE effect_outbox o SET state='ACKED',lease_until=NULL
                         FROM services s WHERE o.effect_id=%s AND o.service=s.name
                         AND o.state='CLAIMED' AND o.lease_token=%s AND o.lease_until>now()
                         AND o.generation=s.generation AND o.generation=s.active_generation
                         RETURNING o.effect_id,o.service,o.generation''',
                        (effect['effect_id'],effect['lease_token']))
                    ok=cur.fetchone()
                    if ok:self._audit(cur,ok['service'],ok['generation'],"EFFECT_ACKED",ok['effect_id'])
                    return ok is not None

    def status(self,service:str|None=None):
        with self.connection() as conn:
            with conn.cursor() as cur:
                if service:
                    cur.execute("SELECT * FROM services WHERE name=%s",(service,))
                    row=cur.fetchone()
                    if not row:return None
                    cur.execute('''SELECT id,state,lease_token,attempts,generation FROM release_jobs
                        WHERE service=%s ORDER BY id DESC LIMIT 20''',(service,))
                    jobs=cur.fetchall()
                    cur.execute('''SELECT effect_id,state,attempts,generation FROM effect_outbox
                        WHERE service=%s ORDER BY created_at DESC LIMIT 20''',(service,))
                    return {'service':row,'jobs':jobs,'effects':cur.fetchall()}
                cur.execute('''SELECT name,desired_version,generation,active_version,active_generation,
                   active_endpoint FROM services ORDER BY name LIMIT 100''')
                return cur.fetchall()

    def audit(self,limit:int=100):
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT %s",(max(1,min(limit,1000)),))
                return cur.fetchall()
