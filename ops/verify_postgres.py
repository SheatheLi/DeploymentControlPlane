"""Cross-platform PostgreSQL integration gate: no driver/DB/test skips => FAIL.

Windows: python ops/verify_postgres.py
Linux:   ./ops/verify-postgres.sh
Requires a dedicated disposable PostgreSQL >=14 via DCP_TEST_PG_URL.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def die(reason: str) -> int:
    print('REAL_POSTGRESQL_INTEGRATION=BLOCKED: ' + reason, file=sys.stderr, flush=True)
    return 1


def inspect_junit(xml_file: Path) -> tuple[int, int, int]:
    # pytest may exit 0 when every collected test was skipped.
    cases = ET.parse(xml_file).getroot().findall('.//testcase')
    skipped = sum(bool(c.findall('skipped')) for c in cases)
    failed = sum(bool(c.findall('failure') or c.findall('error')) for c in cases)
    return len(cases), skipped, failed


def main() -> int:
    url = os.environ.get('DCP_TEST_PG_URL')
    if not url:
        return die('DCP_TEST_PG_URL is required (disposable PostgreSQL database)')
    try:
        import psycopg
    except ImportError:
        return die('psycopg3 is unavailable')
    try:
        with psycopg.connect(url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_setting('server_version_num')::integer, 1")
                version, ok = cur.fetchone()
                if version < 140000 or ok != 1:
                    return die('PostgreSQL >=14 is required')
    except Exception as exc:
        return die('live PostgreSQL connection failed: ' + str(exc))
    print('REAL_POSTGRESQL_PREFLIGHT=PASS; server_version=' + str(version), flush=True)

    with tempfile.TemporaryDirectory(prefix='dcp-live-pg-') as directory:
        xml_file = Path(directory) / 'pytest.xml'
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                                 'tests_round12/test_pg_live.py',
                                 'tests_round12/test_pg_live_full_path.py', '-rs',
                                 '--junitxml=' + str(xml_file)],
                                cwd=ROOT, env=env, check=False)
        if result.returncode:
            return die('pytest exited nonzero: ' + str(result.returncode))
        try:
            total, skipped, failed = inspect_junit(xml_file)
        except (OSError, ET.ParseError) as exc:
            return die('invalid or missing JUnit evidence: ' + str(exc))
        if total < 3 or skipped or failed:
            return die(f'incomplete gate: total={total}, skipped={skipped}, failed={failed}')
        print(f'REAL_POSTGRESQL_INTEGRATION=PASS; executed={total}')
        return 0


if __name__ == '__main__':
    sys.exit(main())
