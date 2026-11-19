"""Real worker-process descriptor handoff regression, not a mock."""
import os,socket,subprocess,sys
from pathlib import Path
import pytest

APP=Path(__file__).resolve().parents[1]/'src'/'worker_app.py'

@pytest.mark.skipif(os.name!='posix',reason='POSIX descriptor transfer only')
def test_unlistening_socket_is_rejected():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0))
        p=subprocess.run([sys.executable,str(APP),str(sock.getsockname()[1]),'v1','--listen-fd',str(sock.fileno())],pass_fds=(sock.fileno(),),capture_output=True,text=True,timeout=4)
    assert p.returncode!=0
    assert 'expected loopback listener' in p.stderr

@pytest.mark.skipif(os.name!='posix',reason='POSIX descriptor transfer only')
def test_mismatched_listener_port_is_rejected():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0));sock.listen(5)
        p=subprocess.run([sys.executable,str(APP),str(sock.getsockname()[1]+1),'v1','--listen-fd',str(sock.fileno())],pass_fds=(sock.fileno(),),capture_output=True,text=True,timeout=4)
    assert p.returncode!=0
    assert 'expected loopback listener' in p.stderr
