"""Durable local deployment control plane with fenced leases and real HTTP child.
No Docker/Kubernetes integration and no remote code execution are provided.
"""
from __future__ import annotations
import contextlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

SCHEMA='''
CREATE TABLE IF NOT EXISTS desired(service TEXT PRIMARY KEY, version TEXT NOT NULL, generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT NOT NULL, version TEXT NOT NULL, generation INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING', lease_token INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY, service TEXT NOT NULL, generation INTEGER NOT NULL, version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS active(service TEXT PRIMARY KEY, version TEXT NOT NULL, generation INTEGER NOT NULL, port INTEGER NOT NULL, pid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, event TEXT, service TEXT, generation INTEGER, detail TEXT);
'''

class Controller:
    def __init__(self,path:str):
        self.path=str(path)
        with self.connect() as db: db.executescript(SCHEMA)
    @contextlib.contextmanager
    def tx(self):
        db=self.connect();db.execute('BEGIN IMMEDIATE')
        try: yield db;db.commit()
        except: db.rollback();raise
        finally: db.close()
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000');db.execute('PRAGMA journal_mode=WAL')
        return db
    def event(self,db,event,service,generation,detail=''):
        db.execute('INSERT INTO history(ts,event,service,generation,detail) VALUES (?,?,?,?,?)',
                   (time.time(),event,service,generation,detail))
    def deploy(self,service,version,key):
        if not service.isidentifier() or not version.isidentifier() or not key or len(key)>128:
            raise ValueError('invalid service/version/key')
        with self.tx() as db:
            old=db.execute('SELECT service,generation,version FROM idempotency WHERE key=?',(key,)).fetchone()
            if old:
                if (old['service'],old['version'])!=(service,version):raise ValueError('idempotency collision')
                return int(old['generation'])
            prev=db.execute('SELECT generation FROM desired WHERE service=?',(service,)).fetchone()
            gen=(prev['generation']+1) if prev else 1
            db.execute('INSERT INTO desired VALUES (?,?,?) ON CONFLICT(service) DO UPDATE SET version=excluded.version,generation=excluded.generation',(service,version,gen))
            db.execute('INSERT INTO jobs(service,version,generation,status) VALUES (?,?,?,"PENDING")',(service,version,gen))
            db.execute('INSERT INTO idempotency VALUES (?,?,?,?)',(key,service,gen,version))
            self.event(db,'REQUESTED',service,gen,version)
            return gen
    def claim(self,now=None,lease=10):
        now=time.time() if now is None else now
        with self.tx() as db:
            job=db.execute('''SELECT j.* FROM jobs j JOIN desired d ON j.service=d.service AND j.generation=d.generation
                 WHERE (j.status='PENDING' OR (j.status='CLAIMED' AND j.lease_until<?)) ORDER BY j.id LIMIT 1''',(now,)).fetchone()
            if not job:return None
            token=job['lease_token']+1
            db.execute('UPDATE jobs SET status="CLAIMED",lease_token=?,lease_until=? WHERE id=?',(token,now+lease,job['id']))
            self.event(db,'CLAIMED',job['service'],job['generation'],str(token))
            return dict(job)|{'lease_token':token}
    def promote(self,job,port,pid):
        with self.tx() as db:
            desired=db.execute('SELECT generation FROM desired WHERE service=?',(job['service'],)).fetchone()
            lease=db.execute('SELECT status,lease_token,lease_until FROM jobs WHERE id=?',(job['id'],)).fetchone()
            if not desired or desired['generation']!=job['generation'] or not lease or \
                    lease['status']!='CLAIMED' or lease['lease_token']!=job['lease_token'] or lease['lease_until']<=time.time():
                self.event(db,'FENCED',job['service'],job['generation'],'stale worker attempted promotion')
                return False
            prior=db.execute('SELECT pid FROM active WHERE service=?',(job['service'],)).fetchone()
            db.execute('INSERT INTO active VALUES (?,?,?,?,?) ON CONFLICT(service) DO UPDATE SET version=excluded.version,generation=excluded.generation,port=excluded.port,pid=excluded.pid',
                (job['service'],job['version'],job['generation'],port,pid))
            db.execute('UPDATE jobs SET status="SUCCEEDED" WHERE id=?',(job['id'],))
            self.event(db,'PROMOTED',job['service'],job['generation'],f'port={port}')
            return prior['pid'] if prior else True
    def status(self,service):
        with self.connect() as db:
            desired=db.execute('SELECT * FROM desired WHERE service=?',(service,)).fetchone()
            active=db.execute('SELECT * FROM active WHERE service=?',(service,)).fetchone()
            return {'desired':dict(desired) if desired else None,'active':dict(active) if active else None}
    def audit(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT event,service,generation,detail FROM history ORDER BY id')]

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def alive(port,expected,timeout=2):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.2) as resp:
                if json.loads(resp.read())['version']==expected:return True
        except (OSError,ValueError,KeyError):pass
        time.sleep(.025)
    return False

def run_one(controller:Controller,cleanup_old=True):
    job=controller.claim()
    if not job:return None
    # POSIX: reserve a *listening* endpoint and transfer its descriptor to the
    # worker. Selecting a free port and closing it before spawn has a TOCTOU
    # race and can accidentally health-check somebody else's process.
    if os.name=='posix':
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',0))
            listener.listen(16)
            port=listener.getsockname()[1]
            fd=listener.fileno()
            p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('worker_app.py')),
                                str(port),job['version'],'--listen-fd',str(fd)],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,pass_fds=(fd,))
    else:
        # Windows cannot use POSIX pass_fds. This fallback is still subject to
        # port-rebind race; it is NOT claimed as an atomic launch handshake.
        port=free_port()
        p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('worker_app.py')),
                            str(port),job['version']],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        if not alive(port,job['version']):raise RuntimeError('health gate failed')
        result=controller.promote(job,port,p.pid)
        if not result:
            p.terminate();p.wait(timeout=2);return {'fenced':True}
        if cleanup_old and type(result)==int and result!=p.pid:
            try:os.kill(result,15)
            except ProcessLookupError:pass
        return {'promoted':True,'version':job['version'],'port':port,'pid':p.pid}
    except Exception:
        p.terminate();p.wait(timeout=3)
        with controller.tx() as db:
            db.execute('UPDATE jobs SET status="FAILED" WHERE id=? AND lease_token=?',(job['id'],job['lease_token']))
            controller.event(db,'FAILED',job['service'],job['generation'],'health gate failure')
        raise
