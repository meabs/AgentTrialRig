# FastAPI change tasks

Does an agent-facing documentation layer change what a coding agent spends on real change tasks? This trial is ported from the original Codex task matrix: one FastAPI, SQLModel and PostgreSQL application (the upstream [full-stack-fastapi-template](https://github.com/fastapi/full-stack-fastapi-template)) cloned with byte-identical code and different documentation layers.

| Arm | Repository | Layer |
| --- | --- | --- |
| `baseline` | `repo-baseline` | none |
| `skill` | `repo-agent-skill` | `AGENTS.md` index plus six task-scoped skills |
| `recommended` | `repo-agent-recommended` | the same skills, with the load-bearing facts folded into the knowledge docs |

Two tasks: `simple_change` (add an `order_by` parameter to the item listing) and `complex` (an archive and soft-delete feature across backend, migration and frontend). The prompts are the original baseline prompts; the self-reported metrics block is dropped because trialrig measures tokens and tool calls itself.

## Requirements

A local PostgreSQL (role `postgres`, password `changethis`), [uv](https://docs.astral.sh/uv/), the Codex CLI, and the three repositories. Edit the `/path/to` placeholders in `trial.toml`.

## Files

- `trial.toml`: the trial. Questions name a logical repo `app`; each arm maps it to a different clone with `repos = { app = "..." }`.
- `prepare_run.py`: per-run setup. Creates a database named from `$TRIAL_ID`, points `.env` at it and installs dependencies, so parallel runs never share state.
- `verify.sh`: after the agent finishes, applies migrations, runs the whole backend suite and checks the task's own constraints (no migration, no frontend change for `simple_change`). Exit 0 means verified.

```sh
trialrig run trial.toml --dry-run
trialrig run trial.toml --question simple_change --reps 3
```

## What a pilot showed

Three runs per arm of `simple_change` with Codex, baseline against the recommended layer: 2 of 3 verified in each arm, and a relative cost of 1.12 (interval 0.78 to 1.47) for the recommended layer, which is no clear difference. After a single run each it had looked like baseline failing and recommended passing. Three runs per arm is far too few to rank the layers; the report estimates about 39 runs per arm to detect a 20% difference at that spread.
