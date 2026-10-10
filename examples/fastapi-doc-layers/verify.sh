#!/usr/bin/env bash
# Independent check after the agent finishes: migrations apply, the whole backend suite passes, and the task's own
# constraints hold. Exit 0 means verified. Runs in the clone (TRIAL_DIR) against a database of its own.
set -uo pipefail
task="${1:?simple_change or complex}"
cd "$TRIAL_DIR" || exit 2
db="verify_${TRIAL_ID}"
export PGPASSWORD=changethis
cleanup() { for d in "$db" "app_${TRIAL_ID}"; do psql -h localhost -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS \"$d\"" >/dev/null 2>&1; done; }
trap cleanup EXIT
psql -h localhost -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS \"$db\"" -c "CREATE DATABASE \"$db\"" || exit 2

fail() { echo "verify: $*" >&2; exit 1; }

if [ "$task" = simple_change ]; then
  git status --porcelain | grep -q ' frontend/' && fail "frontend files were touched"
  git status --porcelain | grep -q 'alembic/versions' && fail "a migration was created"
  git diff --quiet HEAD -- backend/app/api/routes/items.py && fail "items.py was not changed"
  grep -q 'order_by' backend/app/api/routes/items.py || fail "no order_by parameter"
  grep -rqE 'order_by' backend/tests || fail "no test mentions order_by"
fi

cd backend || exit 2
export FASTAPI_ENV=development SECRET_KEY=changethis-benchmark-secret-key-1234567890 PROJECT_NAME="Trial verify" \
       FIRST_SUPERUSER=admin@example.com FIRST_SUPERUSER_PASSWORD=changethis123 \
       DATABASE_URL="postgresql://postgres:changethis@localhost:5432/$db"
uv sync >/dev/null 2>&1
uv run alembic upgrade head >/dev/null 2>&1 || fail "migrations do not apply"
uv run pytest tests/ -q 2>&1 | tail -3
[ "${PIPESTATUS[0]}" -eq 0 ] || fail "backend tests failed"
