"""Static contractual safeguards only; NOT a substitute for live PostgreSQL."""
from pathlib import Path
from src.pg_control import PgControl

ROOT=Path(__file__).resolve().parents[1]

def test_schema_has_durable_tables_and_fenced_lease_columns():
    sql=(ROOT/'sql/postgres.sql').read_text()
    for field in ['CREATE TABLE IF NOT EXISTS services','deploy_requests','release_jobs','effect_outbox',
                  'lease_token bigint','lease_until timestamptz','audit_events']:
        assert field in sql

def test_source_has_atomic_fence_and_skip_locked_and_outbox_promotion():
    code=(ROOT/'src/pg_control.py').read_text()
    assert 'LIMIT 1 FOR UPDATE OF j SKIP LOCKED' in code
    assert 'LIMIT 1 FOR UPDATE OF o SKIP LOCKED' in code
    assert 'state=\'PROMOTED\'' in code
    assert 'INSERT INTO effect_outbox' in code
    assert 'lease_token=%s AND o.lease_until>now()' in code
