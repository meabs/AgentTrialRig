# Backend tests

Run them from `backend/`:

```sh
FASTAPI_ENV=development uv run pytest tests/
```

Prerequisites: a Postgres reachable at `DATABASE_URL` (the repository's `.env` sets it), migrated first with `uv run alembic upgrade head`.

`FASTAPI_ENV=development` is required. Without it, importing the app fails during test collection with `Frontend directory .../backend/app/frontend does not exist`, because that directory only exists after a frontend build. With it, the check is skipped and the whole suite runs.
