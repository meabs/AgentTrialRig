#!/usr/bin/env bash
# Run the backend tests from backend/. Applies the migrations first, because a new database has no tables, and sets
# FASTAPI_ENV=development, which skips the frontend-build check that otherwise fails test collection.
set -e

export FASTAPI_ENV=development
uv run alembic upgrade head
uv run pytest tests/ "$@"
