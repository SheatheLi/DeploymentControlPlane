#!/usr/bin/env bash
# Same fail-closed PostgreSQL integration gate on Linux and Windows.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python ops/verify_postgres.py
