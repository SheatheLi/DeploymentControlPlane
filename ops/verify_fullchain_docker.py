"""Fail-closed, disposable real-PostgreSQL end-to-end acceptance.

Usage: python ops/verify_fullchain_docker.py
  * Requires Python dependencies (pip install -r requirements-postgres.txt).
  * Uses Docker to start an ephemeral, loopback-only PostgreSQL 17 database.
  * Runs real PostgreSQL + API + worker + external receiver integration.
  * Always attempts container removal, including test failure/interrupt.

If DCP_TEST_PG_URL is set, use the explicitly supplied disposable database
instead of creating a Docker container. No SQLite control-plane fallback.
"""
from __future__ import annotations

import importlib.util
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def blocked(reason: str) -> int:
    print(f"FULLCHAIN_ACCEPTANCE=BLOCKED: {reason}", file=sys.stderr, flush=True)
    return 1


def parse_port(raw: str) -> int:
    # Must be loopback, never an Internet-facing PostgreSQL binding.
    for line in raw.splitlines():
        m = re.fullmatch(r"127\.0\.0\.1:(\d{1,5})", line.strip())
        if m:
            port = int(m.group(1))
            if 1 <= port <= 65535:
                return port
    raise ValueError("Docker did not publish PostgreSQL on 127.0.0.1")


def run_gate(env: dict[str, str]) -> int:
    try:
        result = subprocess.run([sys.executable, str(ROOT / "ops" / "verify_postgres.py")],
                                cwd=ROOT, env=env, check=False, timeout=120)
        return result.returncode
    except subprocess.TimeoutExpired:
        return blocked("native integration exceeded 120s; this is not a PASS")


def main() -> int:
    if importlib.util.find_spec("psycopg") is None:
        return blocked("psycopg3 is not installed; run: python -m pip install -r requirements-postgres.txt")

    # An explicitly provided URL must refer to a separate disposable test DB.
    provided = os.environ.get("DCP_TEST_PG_URL")
    if provided:
        print("FULLCHAIN_DATABASE=EXTERNAL_EXPLICIT_DISPOSABLE; running live gate", flush=True)
        return run_gate(dict(os.environ))

    docker = shutil.which("docker")
    if not docker:
        return blocked("Docker not installed; supply DCP_TEST_PG_URL or install Docker Desktop")

    password = secrets.token_urlsafe(24)
    container = None
    gate_rc = 1
    cleanup_ok = True
    try:
        launched = subprocess.run([
            docker, "run", "--rm", "-d",
            "--label", "dcp-disposable-acceptance=1",
            "-e", "POSTGRES_USER=dcp_test",
            "-e", f"POSTGRES_PASSWORD={password}",
            "-e", "POSTGRES_DB=dcp_acceptance",
            "-p", "127.0.0.1::5432", "postgres:17-alpine",
        ], text=True, capture_output=True, check=False, timeout=180)
        if launched.returncode:
            return blocked("unable to start disposable PostgreSQL: " + launched.stderr.strip()[-500:])
        candidate = launched.stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{64}", candidate):
            return blocked("Docker returned an unexpected container identifier")
        container = candidate
        mapped = subprocess.run([docker, "port", container, "5432/tcp"],
                                text=True, capture_output=True, check=False, timeout=12)
        if mapped.returncode:
            return blocked("Docker cannot report the PostgreSQL loopback port")
        port = parse_port(mapped.stdout)

        deadline = time.monotonic() + 50
        ready = False
        while time.monotonic() < deadline:
            probe = subprocess.run([docker, "exec", container, "pg_isready", "-U", "dcp_test", "-d", "dcp_acceptance"],
                                   capture_output=True, check=False, timeout=8)
            if probe.returncode == 0:
                ready = True
                break
            time.sleep(.5)
        if not ready:
            return blocked("PostgreSQL did not report ready within 50s")
        dsn = f"postgresql://dcp_test:{quote(password, safe='')}@127.0.0.1:{port}/dcp_acceptance"
        env = {**os.environ, "DCP_TEST_PG_URL": dsn}
        print("FULLCHAIN_DATABASE=REAL_POSTGRESQL_17_DISPOSABLE; endpoint=127.0.0.1", flush=True)
        gate_rc = run_gate(env)
        return gate_rc
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return blocked(type(exc).__name__ + ": " + str(exc))
    finally:
        if container:
            try:
                removed = subprocess.run([docker, "rm", "-f", container],
                                         capture_output=True, check=False, timeout=20)
                cleanup_ok = removed.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                cleanup_ok = False
            print("DISPOSABLE_DATABASE_CLEANUP=" + ("PASS" if cleanup_ok else "FAILED_CHECK_DOCKER"),
                  flush=True)
            if not cleanup_ok:
                return blocked("Docker container cleanup failed; inspect local containers before retry")


if __name__ == "__main__":
    sys.exit(main())
