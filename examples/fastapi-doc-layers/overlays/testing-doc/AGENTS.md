# Backend tests

Run them from `backend/`:

```sh
FASTAPI_ENV=development uv run pytest tests/
```

Prerequisites: a Postgres reachable at `DATABASE_URL` (the repository's `.env` sets it). A new database has no tables, so run the migrations once before pytest, from `backend/` with the same `DATABASE_URL`:

```sh
uv run alembic upgrade head
```

If tests fail with `relation ... does not exist`, the migrations have not been applied to that database.

`FASTAPI_ENV=development` is required. Without it, importing the app fails during test collection with `Frontend directory .../backend/app/frontend does not exist`, because that directory only exists after a frontend build. With it, the check is skipped and the whole suite runs.
