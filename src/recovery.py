"""Local worker self-registration and crash-window recovery.

The child itself writes an identity-bound launch registration. If the controller
crashes after spawning it, a new controller may safely adopt the live child
only when desired generation, claim lease, and /health launch nonce agree.

This is NOT Kubernetes reconciliation: no distributed lease, container runtime,
or guarantee about a crash before the child self-registers.
"""
from __future__ import annotations
import json, sqlite3, secrets, subprocess, sys, time, urllib.request
from pathlib import Path
from src.control import Controller, free_port

LAUNCH_SCHEMA='''CREATE TABLE IF NOT EXISTS launches (
 nonce TEXT PRIMARY KEY, job_id INTEGER NOT NULL, lease_token INTEGER NOT NULL,
 service TEXT NOT NULL, version TEXT NOT NULL, generation INTEGER NOT NULL,
 port INTEGER NOT NULL, pid INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'REGISTERED',
 created_at REAL NOT NULL
)'''

def ensure_launch_table(c: Controller):
    with c.connect() as db:db.execute(LAUNCH_SCHEMA)

def health_with_nonce(port:int,version:str,nonce:str,timeout:float=.4)->bool:
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=timeout) as resp:
            value=json.loads(resp.read())
        return value.get('version')==version and value.get('launch_nonce')==nonce
    except (OSError,ValueError,KeyError):return False

def spawn_registered(c:Controller, job:dict, timeout:float=3.0):
    """Launch a trusted local built-in worker. Does NOT promote it."""
    ensure_launch_table(c)
    port=free_port(); nonce=secrets.token_hex(16)
    argv=[sys.executable,str(Path(__file__).with_name('worker_app.py')),
          str(port),job['version'],c.path,str(job['id']),str(job['lease_token']),nonce,
          job['service'],str(job['generation'])]
    proc=subprocess.Popen(argv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    expiry=time.monotonic()+timeout
    while time.monotonic()<expiry:
        with c.connect() as db:
            row=db.execute('SELECT * FROM launches WHERE nonce=?',(nonce,)).fetchone()
        if row is not None and health_with_nonce(port,job['version'],nonce):
            return {'nonce':nonce,'pid':proc.pid,'port':port,'job':job}
        if proc.poll() is not None:break
        time.sleep(.025)
    proc.terminate();proc.wait(timeout=3)
    raise RuntimeError('worker failed to register and serve health')

def recover(c:Controller):
    """Recover live registered launches, excluding stale epochs/claims.

    Refuses unknown identity rather than killing by PID (PID reuse risk).
    """
    ensure_launch_table(c); results=[]
    with c.connect() as db:
        rows=[dict(r) for r in db.execute("SELECT * FROM launches WHERE state='REGISTERED' ORDER BY created_at")]
    for row in rows:
        with c.connect() as db:
            job=db.execute('SELECT * FROM jobs WHERE id=?',(row['job_id'],)).fetchone()
            desired=db.execute('SELECT * FROM desired WHERE service=?',(row['service'],)).fetchone()
        live=health_with_nonce(row['port'],row['version'],row['nonce'])
        safe=(live and job and desired and desired['generation']==row['generation']
              and job['status']=='CLAIMED' and job['lease_token']==row['lease_token']
              and job['lease_until']>time.time() and job['generation']==row['generation'])
        if safe:
            ok=c.promote(dict(job),row['port'],row['pid'])
            state='ADOPTED' if ok else 'REJECTED'
        else:state='QUARANTINED'
        with c.tx() as db:
            db.execute('UPDATE launches SET state=? WHERE nonce=?',(state,row['nonce']))
            c.event(db,state,row['service'],row['generation'],row['nonce'][:8])
        results.append({'nonce':row['nonce'],'state':state,'live_identity_confirmed':live})
    return results

def reconcile_deployment(c:Controller):
    """Public controller path: recover registered children before new launch."""
    inherited=recover(c)
    if any(x['state']=='ADOPTED' for x in inherited):
        return {'action':'adopted','launches':inherited}
    job=c.claim()
    if job is None:return {'action':'idle','launches':inherited}
    started=spawn_registered(c,job)
    results=recover(c)
    if not any(x['nonce']==started['nonce'] and x['state']=='ADOPTED' for x in results):
        # The PID was verified only via the capability before any cleanup.
        # Do not signal it when identity/claim is uncertain.
        return {'action':'quarantined','port':started['port'],'launches':results}
    return {'action':'promoted','port':started['port'],'pid':started['pid'],'launches':results}
