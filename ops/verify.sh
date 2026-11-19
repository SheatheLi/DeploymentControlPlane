#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHONPATH=. python -m pytest -q tests tests_round12 --disable-warnings
# Re-generate UI and validate the compiled script. No node_modules or network.
tsc --strict --target ES2019 --lib ES2020,DOM --outFile web/react/app.js web/react/app.ts
node --check web/react/app.js
node tests_round12/ui_contract.cjs
PYTHONPATH=. python ops/http_smoke.py
python -m compileall -q src
printf 'LOCAL_STATIC_AND_HTTP_GATES=PASS\n'
printf 'LIVE_POSTGRESQL=NOT_RUN unless DCP_TEST_PG_URL and psycopg + PostgreSQL exist\n'
