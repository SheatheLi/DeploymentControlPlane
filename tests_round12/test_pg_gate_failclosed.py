"""PostgreSQL release gate cannot claim success on SKIP or unavailable DB."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ops.verify_postgres import inspect_junit

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('entry', [
    ['bash', 'ops/verify-postgres.sh'],
    [sys.executable, 'ops/verify_postgres.py'],
])
def test_live_pg_gate_requires_explicit_real_database_url(entry):
    env = {k:v for k,v in os.environ.items() if k != 'DCP_TEST_PG_URL'}
    run = subprocess.run(entry,cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
    assert run.returncode != 0
    assert 'REAL_POSTGRESQL_INTEGRATION=PASS' not in run.stdout + run.stderr


@pytest.mark.parametrize('entry', [
    ['bash', 'ops/verify-postgres.sh'],
    [sys.executable, 'ops/verify_postgres.py'],
])
def test_live_pg_gate_rejects_unreachable_database_not_skipped_success(entry):
    env = {**os.environ, 'DCP_TEST_PG_URL':'postgresql://nobody:none@127.0.0.1:1/nonexistent'}
    run=subprocess.run(entry,cwd=ROOT,env=env,capture_output=True,text=True,timeout=18)
    assert run.returncode != 0
    output=run.stdout + run.stderr
    assert 'REAL_POSTGRESQL_INTEGRATION=PASS' not in output
    assert 'REAL_POSTGRESQL_' in output


def test_junit_skip_is_a_failed_release_gate(tmp_path):
    file=tmp_path/'report.xml'
    file.write_text('<testsuite><testcase name="a"/><testcase name="b"><skipped /></testcase></testsuite>')
    assert inspect_junit(file)==(2,1,0)
    file.write_text('<testsuite><testcase name="a"/><testcase name="b"/></testsuite>')
    assert inspect_junit(file)==(2,0,0)
