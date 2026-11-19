"""Tests only local gate safety; never substitutes for native PostgreSQL execution."""
import os
import subprocess
import sys
from pathlib import Path
import pytest
from ops.verify_fullchain_docker import parse_port

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('raw,expected', [
    ('127.0.0.1:54231',54231),
    ('127.0.0.1:5432\n',5432),
])
def test_loopback_docker_port_parser(raw,expected):
    assert parse_port(raw)==expected

@pytest.mark.parametrize('raw', [
    '0.0.0.0:5432', '[::]:5432', '192.168.0.1:5432', '127.0.0.1:0', '127.0.0.1:65536', 'invalid',
])
def test_nonloopback_or_invalid_port_rejected(raw):
    with pytest.raises(ValueError):parse_port(raw)


def test_one_command_runner_never_passes_without_driver_or_database(tmp_path):
    env={k:v for k,v in os.environ.items() if k!='DCP_TEST_PG_URL'}
    env['PATH'] = str(tmp_path)  # Never accidentally start Docker during unit tests.
    result=subprocess.run([sys.executable,'ops/verify_fullchain_docker.py'],env=env,
                          cwd=ROOT,text=True,capture_output=True,timeout=20)
    # This is a *local* absence test only. Native gate must run with actual server.
    assert result.returncode != 0
    assert 'FULLCHAIN_ACCEPTANCE=BLOCKED' in result.stderr
    assert 'REAL_POSTGRESQL_INTEGRATION=PASS' not in result.stdout+result.stderr
