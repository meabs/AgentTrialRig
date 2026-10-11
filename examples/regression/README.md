# Regression check for your agent tasks

1. Write a trial with a handful of real tasks and a `verify` command for each (see `../fastapi-doc-layers/trial.toml`).
2. Run it with 4 or more reps and keep the results folder when you are happy with them: `trialrig run agent-tasks/trial.toml --out first`.
3. Save the snapshot and commit it: `trialrig snapshot first --to agent-tasks/baseline.json --name codex-0.130`.
4. After upgrading the model, the agent CLI or your `AGENTS.md`, run `trialrig regress agent-tasks/trial.toml --snapshot agent-tasks/baseline.json`. It exits 1 when a cell passes clearly less often than before.

`regression.yml` runs step 4 weekly in GitHub Actions. It costs tokens on every run, so keep the task set small.
