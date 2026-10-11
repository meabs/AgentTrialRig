# Agent Trial Rig

[![CI](https://github.com/meabs/AgentTrialRig/actions/workflows/test.yml/badge.svg)](https://github.com/meabs/AgentTrialRig/actions/workflows/test.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Run repeatable A/B trials of coding agents (Claude Code, Codex, Cursor, GitHub Copilot) and get a report that says how much to trust the result.

Does a documentation layer, a code graph, a skill or a prompt change make an agent cheaper or more correct? Agent runs are expensive and noisy, so a single comparison tells you very little. This tool runs the same questions across arms, agents and repetitions, each in a fresh clone, measures cost and tool use from the agent's own output (not what the agent says about itself), scores answers against keys you write, and reports differences with intervals.

**[Project page with real output](https://meabs.github.io/AgentTrialRig/)**

It came out of the benchmarks behind [AgentGraphWorkTree](https://github.com/meabs/AgentGraphWorkTree) and [AgentFrictionLoop](https://github.com/meabs/AgentFrictionLoop), where most of the effort went into the harness and the caveats.

## Install

Needs Python 3.11 or newer and git, plus whichever agent CLIs you want to test. There are no other dependencies.

```sh
pip install git+https://github.com/meabs/AgentTrialRig
trialrig init trial.toml      # writes an example trial file
trialrig run trial.toml --dry-run
trialrig run trial.toml
```

## A trial file

```toml
name = "graph-vs-plain"

[defaults]
reps = 3               # repetitions per (question, arm, agent)
parallel = 4
timeout = 900          # seconds per run
baseline = "plain"     # arms are compared with this one
prompt_suffix = "Answer briefly and say which tools or files you used."

[[repos]]
name = "click"
source = "https://github.com/pallets/click"    # a URL or local path; every run gets a fresh clone
# ref = "8.1.7"

[[arms]]
name = "plain"                                  # nothing added

[[arms]]
name = "graph"
prepare = ["..."]                               # once per trial, before any run (shared installs)
setup = ["python3 install.py --repo ."]         # in each fresh clone, before the agent starts
uses = "agent-knowledge/bin/kg"                 # a run "used the tool" if a tool call matches this regex

[[agents]]
name = "claude"                                 # built in: claude, codex, cursor, copilot

[[agents]]
name = "codex"

[[questions]]
id = "inherit"
repo = "click"
prompt = "Which classes in src/click inherit from Parameter?"
expect = ["\\bOption\\b", "\\bArgument\\b"]     # regexes the answer must match
min_recall = 1.0                                # fraction of `expect` that must match
```

| Field | Meaning |
| --- | --- |
| `repos` | Where to clone from (URL or path) and an optional `ref`. |
| `arms` | What differs between conditions. `overlay` copies a directory over the clone; `repos = { app = "other" }` clones a different repository for the same question; `setup` runs commands in the clone, one run at a time unless `parallel_setup = true` (setup often installs into a shared folder); `prepare` runs once. `uses` is a regex over the agent's tool calls. |
| `agents` | `claude`, `codex`, `cursor` or `copilot`, with optional `model`, `args`, `env`, `edits = true` (allow file changes), `sandbox` (Codex), or a custom `command` with `{prompt}` and a `kind` that says how to read its output. |
| `questions` | A `prompt`, an answer key (`expect`, `min_recall`, `forbid`) and an optional `verify` command whose exit code is recorded for change tasks. `forbid` matches anywhere in the answer, including a mention. |
| `defaults` | `reps`, `parallel`, `timeout`, `baseline`, `prompt_suffix`. |

Environment variables available to `prepare`, `setup` and `verify`: `TRIAL_OUT` (the results folder), `TRIAL_DIR` (the clone), `TRIAL_ID` (unique per run, safe for database names), `TRIAL_QUESTION`, `TRIAL_ARM`, `TRIAL_AGENT`, `TRIAL_REP`.

## Commands

```sh
trialrig run trial.toml [--out DIR] [--reps N] [--question ids] [--arm names] [--agent names] [--retry-failed] [--dry-run]
trialrig report DIR [--baseline ARM] [--question ids] [--json]    # print the report again
trialrig rescore trial.toml DIR                  # score stored answers again after editing a key
trialrig plan --cv 0.3 --effect 0.2              # runs per arm needed to detect a difference
trialrig plan --results out                      # per agent and question: clear, open or futile, and the spend left
trialrig snapshot out --to baseline.json         # save a baseline, then: trialrig regress trial.toml --snapshot baseline.json
trialrig parse codex saved-output.jsonl          # read tokens and tool calls from a saved output
```

A trial resumes where it stopped: finished runs are kept, and `--retry-failed` runs again any that did not finish cleanly. A failed arm setup or a crashed agent is recorded as a failed run and does not stop the others.

## Planning and stopping early

`trialrig plan --cv 0.3` gives a run count from a spread you already know. Better, give it a pilot's results folder and it plans per agent and question, from the spread each cell actually showed, and adds up what finishing would cost:

```sh
trialrig plan --results out --max-reps 12 --price-per-mtok 3
```

```text
| agent | question | runs per arm | relative cost (95% CI) | state | what it means |
| --- | --- | --- | --- | --- | --- |
| codex | callers_deep | 8 | graph 0.37 (0.29 to 0.48) | clear | every arm's interval excludes 1.00 |
| codex | express_send | 2 | graph 1.20 (1.02 to 1.40) | continue | fewer than 4 runs per arm so far |
| codex | inherit | 2 | graph 1.15 (0.97 to 1.42) | continue | fewer than 4 runs per arm so far |
| copilot | callers_deep | 8 | graph 1.13 (0.98 to 1.30) | continue | about 4 more runs per arm |
| copilot | express_send | 2 | graph 1.03 (1.02 to 1.03) | continue | fewer than 4 runs per arm so far |
| copilot | inherit | 2 | graph 1.04 (1.04 to 1.04) | continue | fewer than 4 runs per arm so far |
| cursor | callers_deep | 8 | graph 0.34 (0.26 to 0.45) | clear | every arm's interval excludes 1.00 |
| cursor | express_send | 2 | graph 1.84 (1.77 to 1.92) | continue | fewer than 4 runs per arm so far |
| cursor | inherit | 2 | graph 1.07 (1.03 to 1.11) | continue | fewer than 4 runs per arm so far |

Projected further spend to settle the cells still open: about 9.2M tokens (about $28 at $3.0/M). Token totals differ in meaning between agents, so price each agent separately if you convert.
```

A cell is **clear** when every other arm's interval excludes 1.00 (with at least `--min-runs` runs per arm, default 4), **futile** when the spread says it needs more than `--max-reps` runs to see the difference you care about, and otherwise **continue**. Above, the multi-level question is settled for Codex and Cursor, Copilot's needs about four more runs per arm, and the one-hop questions have too few runs to say. Read the dollar figure as a rough size: totals mean different things for different agents.

`trialrig run trial.toml --reps 12 --stop-when-decided` applies the same rule while running: it does one repetition at a time and stops each agent and question as soon as it is clear or futile, with `--reps` as the cap. Stopping when the interval first excludes 1.00 is a sequential look at the data, which makes false positives more likely than a fixed-size trial; the minimum of four runs per arm limits that, and a result you will act on deserves a confirming run at fixed size.

## Regression checks

Models, agent CLIs and instruction files change, and nobody notices whether the agent got better or worse at your tasks. Save a snapshot when a trial looks right, then compare later runs with it:

```sh
trialrig snapshot first --to baseline.json --name codex-0.130   # what each agent, question and arm did
trialrig compare second --snapshot baseline.json                # compare a finished results folder
trialrig regress trial.toml --snapshot baseline.json            # run the trial, then compare; exit 1 on a regression
```

A snapshot keeps, per cell, how many runs passed (the `verify` result, else the answer key) and the tokens each used, plus the agent CLI version and model. A cell is **regressed** when its pass rate is lower with a one-sided exact test below 0.1, **dearer** or **improved** when the token interval excludes 1.00 (with at least four runs a side, and beyond `--tolerance`, default 10%), and **same** otherwise. `--fail-on regressed,dearer` sets what makes the exit code 1. A drop that the sample cannot distinguish from noise is reported as a lower pass rate "within noise", not as a regression, so a small suite will miss small regressions.

`--old-arm` and `--new-arm` compare one arm of the snapshot with a different arm of the new runs. As a worked example, the snapshot of the plain-search arm of the graph trial against that trial's graph arm, which shows two cells improved (the multi-level callers question for Codex and Cursor) and the rest unchanged or too small to call:

```sh
trialrig compare out --snapshot baseline.json --old-arm plain --new-arm graph
```

```text
# Regression check against snapshot `plain-search`

| agent | question | arm | passed (old to new) | verdict | detail |
| --- | --- | --- | --- | --- | --- |
| codex | callers_deep | graph | 7/8 to 8/8 | improved | pass rate up, 7/8 to 8/8; tokens 0.37x (0.29 to 0.48), clearly fewer |
| codex | express_send | graph | 0/2 to 1/2 | same | pass rate up, 0/2 to 1/2; fewer than 4 runs on one side, so no token verdict |
| codex | inherit | graph | 2/2 to 2/2 | same | fewer than 4 runs on one side, so no token verdict |
| copilot | callers_deep | graph | 8/8 to 8/8 | same | no clear change |
| copilot | express_send | graph | 2/2 to 2/2 | same | fewer than 4 runs on one side, so no token verdict |
| copilot | inherit | graph | 2/2 to 2/2 | same | fewer than 4 runs on one side, so no token verdict |
| cursor | callers_deep | graph | 8/8 to 8/8 | improved | tokens 0.34x (0.26 to 0.45), clearly fewer |
| cursor | express_send | graph | 0/2 to 2/2 | same | pass rate up, 0/2 to 2/2; fewer than 4 runs on one side, so no token verdict |
| cursor | inherit | graph | 2/2 to 2/2 | same | fewer than 4 runs on one side, so no token verdict |

2 improved, 7 same. Verdicts rest on a few runs per cell; `same` means no change could be shown, not that none exists.
```

**A live check, and what it taught.** Codex (`gpt-5.6-luna`) on Click's "which classes inherit from Parameter" question, four runs: that is the snapshot (4 of 4 correct, 47k tokens a run; two runs near 33k, two at 50k and 73k). The same trial run again passed 4 of 4 at a uniform 33k, and the first version of `compare` called that *improved* (0.70x, interval 0.52 to 1.00): with four runs and a skewed baseline, the interval was too eager. Comparisons now also need the whole interval to lie beyond `--tolerance` (default 10%) before a token change is called, and that rerun reads `same`. A third run deleted `src/click/core.py` from the clone: the agent still answered correctly, by reading it from git history (`git show HEAD:src/click/core.py`), but at 2.99x the tokens (2.20 to 4.31), which is reported as `dearer` and fails the exit code with `--fail-on regressed,dearer`. A pass-rate drop was not provoked there, because the agent recovered; so a fourth run cut the timeout to 4 seconds (the baseline runs took about 11), standing in for an agent that has become too slow. All four runs timed out, and the check read `4/4 to 0/4 regressed` (p=0.01) and exited 1. Timed-out and errored runs now count as failures in a scored cell, which the first version missed.

[`examples/regression/`](examples/regression/) has a weekly GitHub Actions workflow, which costs tokens on every run with a real agent. `.github/workflows/regression-example.yml` runs the same steps on GitHub with a stand-in agent that needs no credentials, and checks that a healthy run passes and that a degraded one (`DEGRADE=1`) makes `trialrig regress` exit 1; it passed on a manual dispatch and on push. What is not exercised is the install of a real agent CLI on the runner, which you adapt to your agent.

## What is recorded

Each run is saved as `runs/<question>/<arm>/<agent>/<rep>/run.json` beside the raw output (`raw.out`, `raw.err`, `verify.log`):

- the final answer and its score (`correct`, `recall`, which patterns were missing),
- tokens (input, output, cache read and write, total), cost where the agent reports it, turns and seconds,
- every tool call the agent made, so `uses` is measured from what it did,
- whether verification passed, and whether the run timed out.

## How the report reads the numbers

Costs vary a lot between questions, so each run's tokens are divided by the baseline arm's mean for the same question and agent. A relative cost of 0.65 means 35% cheaper than the baseline. The interval is a bootstrap over those relative costs. When it contains 1.00 the report says *no clear difference*; verdicts based on fewer than 8 runs per arm are marked *provisional*, because a bootstrap on very few runs is too optimistic. The report also estimates how many runs per arm you would need to detect a 20% difference, given the spread it saw.

Example, from a real 16-run trial on Click (two questions, two repetitions):

```
Baseline arm: **plain**. Relative cost is each run's tokens divided by the baseline's mean for the same question; 1.00 means equal, lower is cheaper.

## claude

| arm | runs | correct | tokens per run (95% CI) | relative cost (95% CI) | verdict | turns | used tool | seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| plain | 4 | 4/4 | 68k (48k to 87k) | 1.00 | baseline | 2.8 | - | 9 |
| graph | 4 | 4/4 | 73k (72k to 74k) | 1.18 (0.84 to 1.52) | no clear difference | 3.0 | 4/4 | 9 |

Run-to-run spread (coefficient of variation of relative cost) is 0.34. To detect a 20% difference with 80% power you would need about **45 runs per arm** (larger differences need fewer); this trial has 4.

## codex

| arm | runs | correct | tokens per run (95% CI) | relative cost (95% CI) | verdict | turns | used tool | seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| plain | 4 | 4/4 | 93k (52k to 135k) | 1.00 | baseline | 4.0 | - | 15 |
| graph | 4 | 4/4 | 57k (52k to 67k) | 0.65 (0.52 to 0.79) | cheaper (provisional) | 2.2 | 4/4 | 18 |

Run-to-run spread (coefficient of variation of relative cost) is 0.26. To detect a 20% difference with 80% power you would need about **27 runs per arm** (larger differences need fewer); this trial has 4.

## Tokens by question (mean, thousands)

| question | claude/graph | claude/plain | codex/graph | codex/plain |
| --- | --- | --- | --- | --- |
| callers | 73k | 87k | 62k | 120k |
| inherit | 73k | 48k | 53k | 67k |

Token totals are not comparable between agents (see adapters.py). Treat a verdict as evidence only when the interval excludes 1.00, and as provisional below 8 runs per arm.
```

Read that as: Codex looks cheaper with the graph but four runs per arm is too few to call it, and Claude Code shows no clear difference.

## Verification claims

Set `claim = true` under `[defaults]` and each prompt ends by asking the agent for one line, `VERIFICATION: {"ran": [...], "result": "passed|failed|not-run", "could_not_verify": [...], "missing": [...]}`. The report then sets that account against the `verify` command: an agent that says *passed* when the independent check failed has **overclaimed**; one that says it could not verify when the check passed has **underclaimed**. It also lists the `missing` and `could_not_verify` notes agents repeated, marked as self-reported.

A real example, from [`examples/fastapi-doc-layers/verification-loop.toml`](examples/fastapi-doc-layers/verification-loop.toml): Codex on the FastAPI change task, three runs per arm, with and without the [AgentFrictionLoop](https://github.com/meabs/AgentFrictionLoop) instructions. All six runs passed the independent check. In four of the six the agent could not run pytest the obvious way (collection failed because a generated `backend/app/frontend` directory was missing), and the arms differed in what they left behind:

- With the friction block, all three runs filed a note naming the cause, for example `--origin verification "pytest tests/api/routes/test_items.py could not collect because generated backend/app/frontend directory is missing; static checks were run instead"`.
- Without it, two runs reported `result: "failed"` (an underclaim, since the change was fine) and left `missing` and `could_not_verify` empty, so nothing recorded why.
- Cost: 0.86 of baseline (interval 0.60 to 1.08), no clear difference at three runs per arm.

That recurring blocker is a documentation gap an agent cannot fix for itself: the test command and its prerequisite belong in `AGENTS.md`. Writing it down and re-running is the loop, and it was run (same task, Codex, three runs per arm, both arms with the friction instructions; one adds an `AGENTS.md` giving the test command and the reason `FASTAPI_ENV=development` is needed):

| | friction only | with the fix documented |
| --- | --- | --- |
| Verified by the independent check | 3 of 3 | 3 of 3 |
| Runs that filed a verification or environment note | 3 of 3 | 1 of 3 |
| Notes naming the missing frontend directory | 3 of 3 | 0 of 3 |
| Mean turns | 15.3 | 9.3 |
| Tokens per run | 647k | 469k, relative cost 0.73 (0.46 to 1.09), no clear difference |

The targeted gap did not come back in any documented-arm run, and those runs needed fewer turns. The one note filed there named a different gap: the database had no tables until `uv run alembic upgrade head` ran, even though the text said to migrate first. (An earlier version of this table counted 1 of 3 friction-arm notes naming the frontend directory; recounting the stored tool calls gives 3 of 3, and that is corrected here.)

That next line was sharpened (the overlay now says a new database has no tables, shows the migrate command on its own, and names the `relation ... does not exist` symptom) and the documented arm was run again, three runs, same task and model, same friction runs for comparison:

| | friction only | documented, sharpened |
| --- | --- | --- |
| Verified by the independent check | 3 of 3 | 3 of 3 |
| Notes naming the missing frontend directory | 3 of 3 | 0 of 3 |
| Runs that filed any note | 3 of 3 | 1 of 3 |
| Mean turns | 15.3 | 11.3 |
| Tokens per run | 647k | 509k, relative cost 0.79 (0.56 to 1.24), no clear difference |
| Claimed result against verify | 2 agree, 1 no claim | 3 agree |

The sharpening did not remove the database note: one run again ran pytest before migrating, hit empty tables, applied the documented migration and filed it. Agents sometimes ran the tests first even when told the order, so the next fix is to put the prerequisite in the command rather than in the prose.

That was done: a third arm, `scripted`, overlays `backend/scripts/test.sh` so that one command applies the migrations, sets `FASTAPI_ENV=development` and runs pytest, and its `AGENTS.md` says to run only that. Three more runs, same task and model:

| | friction only | documented, sharpened | scripted |
| --- | --- | --- | --- |
| Verified by the independent check | 3 of 3 | 3 of 3 | 3 of 3 |
| Runs that filed any verification or environment note | 3 of 3 | 1 of 3 | 0 of 3 |
| Mean turns | 15.3 | 11.3 | 7.7 |
| Tokens per run | 647k | 509k | 314k |
| Claimed result against verify | 2 agree, 1 no claim | 3 agree | 3 agree |

All three scripted runs used `bash scripts/test.sh` and none filed a note. Tokens fell from 647k to 314k, but the scripted arm was run on its own after the others, so the report cannot give a relative cost with an interval, and three runs per arm is too few to call the difference. The direction and the notes are what the loop was for: each step moved the cause of the notes out of the agent's judgement and into the repository. With three runs per arm the counts move between runs.

## Agents

| Agent | Command used | Reads | Notes |
| --- | --- | --- | --- |
| `claude` | `claude -p ... --output-format stream-json --verbose` | answer, tokens, cost, tool calls | Allows Bash, Read, Grep and Glob (and Edit and Write with `edits = true`). |
| `codex` | `codex exec --json` | answer, tokens, shell commands | Read-only sandbox, or `workspace-write` with `edits = true`. Stdin is closed, otherwise it waits for input. |
| `cursor` | `cursor-agent -p --force --output-format stream-json` | answer, tokens, tool calls | `--force` lets it run commands. |
| `copilot` | `copilot -p ... --allow-all-tools --output-format json` | answer, tokens, tool calls | Tokens come from the session's usage checkpoint; cached input is counted inside its input total. |

These flags give the agent permission to run commands in a disposable clone, which is what a trial needs. Do not point a trial at a repository or credentials you cannot afford to have an agent touch.

Token totals are not comparable between agents: Claude Code reports cache reads and writes separately, while Codex and Copilot include cached input in their input count. Compare an agent only with itself.

## Limits

- Trials are small by nature. Run-to-run spread is large, so expect to need 20 or more runs per arm to detect a 20% difference.
- Answer keys are regexes you write. A key written from memory or from the tool under test can be wrong; the `rescore` command exists because that happens.
- Cost is only part of the picture: the rig measures tokens, time and correctness, not whether an answer is useful.
- Agent CLIs change their output formats. The parsers are tested against trimmed output recorded from each real CLI (`tests/samples`), so a format change shows up as a failing test, but they may still need updating.

## Development

```sh
pip install -e ".[test]"
python -m pytest -q
```

The tests use a fake agent that prints Claude-style output, so no real CLI or network is needed. CI runs them on Linux, macOS and Windows.

## License

[MIT](LICENSE)
