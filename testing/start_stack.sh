#!/usr/bin/env bash
# Start the full TraceBack stack on a FRESH test database, in production mode,
# for the system and adversarial tests. Linux/macOS (or WSL on Windows).
#
# Needs: PostgreSQL running locally, backend/.venv with requirements-dev.txt
# installed, backend/.env with DB_PASSWORD and SECRET_KEY, and a built
# frontend (cd frontend && npm ci && npx next build).
#
# PSQL: how to reach the database as a superuser (change if needed).
# The test database is dropped and recreated each run: never point it at real data.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PSQL="${PSQL:-psql -h localhost -U postgres}"
TEST_DB="${TEST_DB:-lostfound_test_system}"
RUN="$HERE/run"
mkdir -p "$RUN"

cd "$ROOT/backend"
. .venv/bin/activate
$PSQL -q -c "DROP DATABASE IF EXISTS $TEST_DB;" -c "CREATE DATABASE $TEST_DB OWNER lostfound;"
export TZ=UTC API_DOCS=${API_DOCS:-false} DB_NAME="$TEST_DB" FRONTEND_ORIGIN=http://localhost:3000 EMBEDDING_BACKEND=hashing EMAIL_BACKEND=console
alembic upgrade head > /dev/null 2>&1
python -m app.seed > /dev/null
python -m app.cli make-admin admin.test@gmail.com > /dev/null
# UVICORN_ARGS: extra server options, e.g. "--workers 2" (perf tests).
setsid nohup uvicorn app.main:app --host 127.0.0.1 --port 8000 ${UVICORN_ARGS:-} > "$RUN/backend.log" 2>&1 &
setsid nohup python -m app.worker > "$RUN/worker.log" 2>&1 &
cd "$ROOT/frontend"
BACKEND_URL=http://127.0.0.1:8000 setsid nohup npx next start -H 127.0.0.1 -p 3000 > "$RUN/frontend.log" 2>&1 &
for i in $(seq 1 40); do
  curl -s http://127.0.0.1:3000/api/health 2>/dev/null | grep -q '"ok"' && echo "stack up" && exit 0
  sleep 1
done
echo "stack did not start (see $RUN/*.log)"; exit 1
