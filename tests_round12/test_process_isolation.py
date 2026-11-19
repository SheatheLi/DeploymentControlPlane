"""The external effect receiver is launched as a separate OS process."""
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from src.control import free_port
from src.pg_worker import send_effect

ROOT=Path(__file__).resolve().parents[1]

def test_external_sink_is_real_process(tmp_path):
    port=free_port()
    db_file=tmp_path/'separate_receiver.sqlite'
    env={**os.environ,'DCP_EFFECT_TOKEN':'separate-secret','PYTHONPATH':str(ROOT)}
    child=subprocess.Popen([sys.executable,'-m','src.webhook_sink','--port',str(port),'--db',str(db_file)],
                           cwd=str(ROOT),env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if child.poll() is not None:raise RuntimeError('sink process exited')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.1) as res:
                    if res.status==200:break
            except OSError:time.sleep(.03)
        else:raise AssertionError('separate receiver process did not become healthy')
        effect={'effect_id':'release:isolated:1','payload':{'service':'isolated','version':'v1','generation':1}}
        assert send_effect(effect,f'http://127.0.0.1:{port}','separate-secret')['duplicate'] is False
        assert send_effect(effect,f'http://127.0.0.1:{port}','separate-secret')['duplicate'] is True
        with sqlite3.connect(str(db_file)) as conn:
            assert conn.execute('SELECT count(*) FROM applied').fetchone()[0]==1
        assert child.poll() is None
    finally:
        child.terminate()
        try:child.wait(timeout=4)
        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=4)
        if child.stderr:child.stderr.close()


def test_separate_receiver_restart_preserves_dedup(tmp_path):
    """A receiver process restart keeps the accepted effect ID and version fence."""
    port=free_port()
    db_file=tmp_path/'restart_receiver.sqlite'
    env={**os.environ,'DCP_EFFECT_TOKEN':'resume-secret','PYTHONPATH':str(ROOT)}
    def start():
        proc=subprocess.Popen([sys.executable,'-m','src.webhook_sink','--port',str(port),'--db',str(db_file)],
                              cwd=str(ROOT),env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if proc.poll() is not None:raise RuntimeError('receiver did not survive launch')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.2):return proc
            except OSError:time.sleep(.04)
        raise AssertionError('receiver did not become ready')
    def stop(proc):
        proc.terminate()
        try:proc.wait(timeout=4)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=4)
    effect={'effect_id':'release:api:7','payload':{'service':'api','version':'v7','generation':7}}
    first=start()
    try:assert send_effect(effect,f'http://127.0.0.1:{port}','resume-secret')['duplicate'] is False
    finally:stop(first)
    resumed=start()
    try:
        assert send_effect(effect,f'http://127.0.0.1:{port}','resume-secret')['duplicate'] is True
        with sqlite3.connect(str(db_file)) as conn:
            assert conn.execute('SELECT count(*) FROM applied').fetchone()[0]==1
            assert conn.execute('SELECT generation FROM latest_generation WHERE service=?',('api',)).fetchone()[0]==7
    finally:stop(resumed)
