#!/usr/bin/env python3
"""Per-run setup for the FastAPI change tasks: a database of its own, the dependencies installed, .env pointed at it.

Runs in the fresh clone (TRIAL_DIR) with TRIAL_ID set by trialrig, so parallel runs never share a database.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

work = Path(os.environ["TRIAL_DIR"])
db = "app_" + os.environ["TRIAL_ID"]
env = {**os.environ, "PGPASSWORD": "changethis"}

for stmt in (f'DROP DATABASE IF EXISTS "{db}"', f'CREATE DATABASE "{db}"'):
    r = subprocess.run(["psql", "-h", "localhost", "-U", "postgres", "-d", "postgres", "-q", "-c", stmt], env=env, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"database setup failed: {r.stderr.strip()}")

dotenv = work / ".env"
dotenv.write_text(re.sub(r"(DATABASE_URL=.*/)app_[A-Za-z0-9_]+", rf"\g<1>{db}", dotenv.read_text(encoding="utf-8")), encoding="utf-8")

r = subprocess.run(["uv", "sync"], cwd=work / "backend", capture_output=True, text=True)
if r.returncode:
    sys.exit(f"uv sync failed: {r.stderr.strip()[-300:]}")
