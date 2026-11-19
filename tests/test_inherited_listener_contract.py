"""An inherited socket must be an actual loopback listening endpoint."""
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

APP=Path(__file__).resolve().parents[1]/'src'/'release_app.py'

@pytest.mark.skipif(os.name!='posix',reason='pass_fds requires POSIX')
def test_wrong_nonlistening_descriptor_is_rejected():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
        outcome=subprocess.run([sys.executable,str(APP),str(port),'v1','nonce',str(sock.fileno())],
                               pass_fds=(sock.fileno(),),capture_output=True,text=True,timeout=5)
    assert outcome.returncode!=0
    assert 'not the expected loopback listener' in outcome.stderr

@pytest.mark.skipif(os.name!='posix',reason='pass_fds requires POSIX')
def test_wrong_port_listener_is_rejected():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0));sock.listen()
        port=sock.getsockname()[1]
        outcome=subprocess.run([sys.executable,str(APP),str(port+1),'v1','nonce',str(sock.fileno())],
                               pass_fds=(sock.fileno(),),capture_output=True,text=True,timeout=5)
    assert outcome.returncode!=0
    assert 'not the expected loopback listener' in outcome.stderr
