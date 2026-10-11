# Backend tests

From `backend/`, run:

```sh
bash scripts/test.sh
```

It applies the migrations to the database in `DATABASE_URL` (the repository's `.env` sets it) and then runs the whole suite. Pass pytest arguments after it, for example `bash scripts/test.sh tests/api/routes/test_items.py -q`. Do not run `pytest` directly: it needs the migrations applied and `FASTAPI_ENV=development`, both of which the script handles.
