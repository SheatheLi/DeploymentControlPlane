"""Independent PostgreSQL reconciler + durable HTTP outbox dispatcher.

Run with PG_DSN, DCP_EFFECT_URL, DCP_EFFECT_TOKEN. Health target is a trusted
local probe process (not a container orchestrator). No arbitrary commands.
"""
import argparse
import json
import os
import secrets
import socket
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from .control import free_port
from .pg_control import PgControl


def launch(job, timeout=4):
    """Launch an identity-bound local target without releasing its TCP port first.

    On POSIX the parent reserves and listens on the socket *before* spawning
    the child; the child inherits that listener, closing the free_port() TOCTOU
    gap. Windows retains the legacy path until a supported socket-transfer
    mechanism is implemented. This is a local probe, not a full deployment.
    """
    nonce = secrets.token_hex(16)
    reserved = None
    child = None
    try:
        if os.name == 'posix':
            reserved = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reserved.bind(('127.0.0.1', 0))
            reserved.listen(16)
            port = reserved.getsockname()[1]
            fd = reserved.fileno()
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('release_app.py')),
                 str(port), job['version'], nonce, str(fd)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, pass_fds=(fd,))
        else:
            port = free_port()
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).with_name('release_app.py')),
                 str(port), job['version'], nonce],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
    finally:
        if reserved is not None:
            reserved.close()   # child retains its inherited descriptor

    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/health', timeout=.2) as resp:
                    obj = json.loads(resp.read())
                if obj == {'status': 'healthy', 'version': job['version'],
                           'launch_nonce': nonce}:
                    return child, f'http://127.0.0.1:{port}'
            except (OSError, ValueError):
                pass
            time.sleep(.04)
    except BaseException:
        child.terminate()
        child.wait(timeout=3)
        raise
    child.terminate()
    child.wait(timeout=3)
    raise RuntimeError('release health probe failed')


def send_effect(effect,url,token):
    """The external process deduplicates by stable effect_id across retries."""
    if not url.startswith('http://127.0.0.1:'):
        raise ValueError('demo external effect endpoint must be loopback')
    raw=json.dumps(effect['payload']).encode() if isinstance(effect['payload'],dict) else str(effect['payload']).encode()
    request=urllib.request.Request(url.rstrip('/')+'/effects',data=raw,method='POST',
        headers={'Content-Type':'application/json','X-Effect-Id':effect['effect_id'],
                 'X-Effect-Token':token})
    with urllib.request.urlopen(request,timeout=3) as response:
        result=json.loads(response.read())
    if result.get('effect_id')!=effect['effect_id'] or result.get('status')!='accepted':
        raise RuntimeError('invalid external effect acknowledgement')
    return result


def cycle(db,worker_id,effect_url,effect_token,crash_after_send=False):
    out={'release':None,'effect':None}
    job=db.claim_release(worker_id)
    if job:
        child,endpoint=launch(job)
        if db.promote(job,endpoint,child.pid):
            out['release']={'status':'PROMOTED','service':job['service'],'generation':job['generation'],
                            'endpoint':endpoint,'pid':child.pid}
        else:
            child.terminate();child.wait(timeout=3)
            out['release']={'status':'FENCED','job_id':job['id']}
    effect=db.claim_effect()
    if effect:
        response=send_effect(effect,effect_url,effect_token)
        if crash_after_send:
            # Controlled crash injection: receiver ACKed, database not yet ACKed.
            # Next worker must wait for lease expiry, then redeliver same effect_id.
            os._exit(72)
        ack=db.ack_effect(effect)
        out['effect']={'id':effect['effect_id'],'receiver':response,'ack':ack}
    return out


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['init-db','once','run'])
    p.add_argument('--interval',type=float,default=0.5)
    p.add_argument('--crash-after-send',action='store_true')
    args=p.parse_args(argv)
    db=PgControl(os.getenv('DATABASE_URL',''))
    if args.action=='init-db':db.initialize();print('schema initialized');return
    effect_url=os.environ['DCP_EFFECT_URL']; token=os.environ['DCP_EFFECT_TOKEN']
    worker=f'pgworker:{os.getpid()}'
    while True:
        result=cycle(db,worker,effect_url,token,args.crash_after_send)
        print(json.dumps(result),flush=True)
        if args.action=='once':return
        if result['release'] is None and result['effect'] is None:time.sleep(args.interval)

if __name__=='__main__':main()
