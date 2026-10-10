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
| `arms` | What differs between conditions. `overlay` copies a directory over the clone; `repos = { app = "other" }` clones a different repository for the same question; `setup` runs commands in the clone; `prepare` runs once. `uses` is a regex over the agent's tool calls. |
| `agents` | `claude`, `codex`, `cursor` or `copilot`, with optional `model`, `args`, `env`, `edits = true` (allow file changes), `sandbox` (Codex), or a custom `command` with `{prompt}` and a `kind` that says how to read its output. |
| `questions` | A `prompt`, an answer key (`expect`, `min_recall`, `forbid`) and an optional `verify` command whose exit code is recorded for change tasks. `forbid` matches anywhere in the answer, including a mention. |
| `defaults` | `reps`, `parallel`, `timeout`, `baseline`, `prompt_suffix`. |

Environment variables available to `prepare`, `setup` and `verify`: `TRIAL_OUT` (the results folder), `TRIAL_DIR` (the clone), `TRIAL_ID` (unique per run, safe for database names), `TRIAL_QUESTION`, `TRIAL_ARM`, `TRIAL_AGENT`, `TRIAL_REP`.

## Commands

```sh
trialrig run trial.toml [--out DIR] [--reps N] [--question ids] [--arm names] [--agent names] [--retry-failed] [--dry-run]
trialrig report DIR [--baseline ARM] [--json]    # print the report again
trialrig rescore trial.toml DIR                  # score stored answers again after editing a key
trialrig plan --cv 0.3 --effect 0.2              # runs per arm needed to detect a difference
trialrig parse codex saved-output.jsonl          # read tokens and tool calls from a saved output
```

A trial resumes where it stopped: finished runs are kept, and `--retry-failed` runs again any that did not finish cleanly. A failed arm setup or a crashed agent is recorded as a failed run and does not stop the others.

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

## Agents

| Agent | Command used | Reads | Notes |
| --- | --- | --- | --- |
| `claude` | `claude -p ... --output-format stream-json --verbose` | answer, tokens, cost, tool calls | Allows Bash, Read, Grep and Glob (and Edit and Write with `edits = true`). |
| `codex` | `codex exec --json` | answer, tokens, shell commands | Read-only sandbox, or `workspace-write` with `edits = true`. Stdin is closed, otherwise it waits for input. |
| `cursor` | `cursor-agent -p --force --output-format stream-json` | answer, tokens, tool calls | `--force` lets it run commands. |
| `copilot` | `copilot -p ... --allow-all-tools` | answer only | Reports no tokens or tool calls. |

These flags give the agent permission to run commands in a disposable clone, which is what a trial needs. Do not point a trial at a repository or credentials you cannot afford to have an agent touch.

Token totals are not comparable between agents: Claude Code reports cache reads and writes separately, Codex includes cached input in its input count, and Copilot reports nothing. Compare an agent only with itself.

## Limits

- Trials are small by nature. Run-to-run spread is large, so expect to need 20 or more runs per arm to detect a 20% difference.
- Answer keys are regexes you write. A key written from memory or from the tool under test can be wrong; the `rescore` command exists because that happens.
- Cost is only part of the picture: the rig measures tokens, time and correctness, not whether an answer is useful.
- Agent CLIs change their output formats. The parsers are tested against recorded samples and may need updating.

## Development

```sh
pip install -e ".[test]"
python -m pytest -q
```

The tests use a fake agent that prints Claude-style output, so no real CLI or network is needed. CI runs them on Linux, macOS and Windows.

## License

[MIT](LICENSE)
