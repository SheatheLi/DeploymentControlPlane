"""Native PostgreSQL end-to-end integration: API -> database -> external worker -> receiver.

Requires disposable PostgreSQL in DCP_TEST_PG_URL. No SQLite control-plane fallback.
The SQLite file belongs *only* to the separate external effect receiver.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from src.control import free_port
from src.pg_control import PgControl

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not os.getenv("DCP_TEST_PG_URL"), reason="disposable real PostgreSQL is required"
)


def _http(url: str, *, method="GET", payload=None, token=None, timeout=2):
    headers = {}
    if token is not None:
        headers["X-Admin-Token"] = token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            return resp.status, json.loads(content) if content and "json" in resp.headers.get("Content-Type", "") else content
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _wait_ready(url: str, process: subprocess.Popen, *, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"child exited before HTTP readiness: code={process.returncode}")
        try:
            status, _ = _http(url, timeout=.5)
            if status == 200:
                return
        except (OSError, TimeoutError):
            pass
        time.sleep(.06)
    raise AssertionError(f"HTTP process did not become ready in {timeout}s: {url}")


@contextmanager
def _spawn(*args, env):
    process = subprocess.Popen(
        [sys.executable, *args], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _stop_release_process(pid):
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def test_real_postgres_api_worker_and_receiver_transaction_chain(tmp_path):
    """Observe the full native chain crossing 4 OS processes and 2 durable stores."""
    try:
        import psycopg  # noqa: F401; no mock or fake DB
    except ImportError:
        pytest.fail("psycopg unavailable: native PostgreSQL integration must not fake success")

    dsn = os.environ["DCP_TEST_PG_URL"]
    db = PgControl(dsn)
    db.initialize()
    service = "e" + uuid.uuid4().hex[:16]
    admin_token = "local-test-admin-" + uuid.uuid4().hex
    effect_token = "local-test-effect-" + uuid.uuid4().hex
    api_port, sink_port = free_port(), free_port()
    while sink_port == api_port:
        sink_port = free_port()
    api_url, sink_url = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{sink_port}"
    receiver_db = tmp_path / "separate-external-receiver.sqlite"
    env = {**os.environ, "PYTHONPATH": str(ROOT), "DATABASE_URL": dsn,
           "DCP_ADMIN_TOKEN": admin_token, "DCP_EFFECT_TOKEN": effect_token,
           "DCP_EFFECT_URL": sink_url}
    released_pids = []
    try:
        with _spawn("-m", "src.webhook_sink", "--port", str(sink_port), "--db", str(receiver_db), env=env) as sink:
            _wait_ready(sink_url + "/health", sink)
            with _spawn("-m", "uvicorn", "src.pg_api:app", "--host", "127.0.0.1", "--port", str(api_port), env=env) as api:
                _wait_ready(api_url + "/", api)
                # Real HTTP authentication failure, before any PostgreSQL mutation.
                status, _ = _http(api_url + "/api/services")
                assert status == 403
                body = {"service": service, "version": "v1", "idempotency_key": "key-" + service}
                status, result = _http(api_url + "/api/deploy", method="POST", payload=body, token=admin_token)
                assert status == 200 and result["generation"] == 1, (status, result)
                status, retry = _http(api_url + "/api/deploy", method="POST", payload=body, token=admin_token)
                assert status == 200 and retry["generation"] == 1
                status, _ = _http(api_url + "/api/deploy", method="POST", payload={**body, "version": "v2"}, token=admin_token)
                assert status == 409, "same key with changed payload must not mutate state"

                worker = subprocess.run(
                    [sys.executable, "-m", "src.pg_worker", "once"], env=env, cwd=str(ROOT),
                    capture_output=True, text=True, timeout=25, check=False,
                )
                assert worker.returncode == 0, (worker.returncode, worker.stdout, worker.stderr)
                outcome = json.loads(worker.stdout.strip().splitlines()[-1])
                assert outcome["release"]["status"] == "PROMOTED", outcome
                released_pids.append(outcome["release"]["pid"])
                assert outcome["effect"] and outcome["effect"]["ack"] is True, outcome
                assert outcome["effect"]["receiver"]["duplicate"] is False

                status, observed = _http(api_url + "/api/status/" + service, token=admin_token)
                assert status == 200 and observed["service"]["active_version"] == "v1"
                assert observed["service"]["generation"] == 1
                assert len(observed["jobs"]) == 1 and observed["jobs"][0]["state"] == "PROMOTED"
                assert len(observed["effects"]) == 1 and observed["effects"][0]["state"] == "ACKED"
                assert observed["effects"][0]["attempts"] == 1

                # Independent receiver persisted exactly one effect after HTTP ACK.
                with sqlite3.connect(receiver_db) as conn:
                    count = conn.execute("SELECT count(*) FROM applied WHERE effect_id=?",
                                         (f"release:{service}:1",)).fetchone()[0]
                    assert count == 1
                    latest = conn.execute("SELECT generation FROM latest_generation WHERE service=?",
                                          (service,)).fetchone()[0]
                    assert latest == 1
                with db.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT count(*) AS n FROM deploy_requests WHERE service=%s", (service,))
                        assert cur.fetchone()["n"] == 1
                        cur.execute("SELECT count(*) AS n FROM audit_events WHERE service=%s AND event='EFFECT_ACKED'", (service,))
                        assert cur.fetchone()["n"] == 1
    finally:
        for pid in released_pids:
            _stop_release_process(pid)
