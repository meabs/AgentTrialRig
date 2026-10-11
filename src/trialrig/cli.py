"""trialrig command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, report as rep, runner, stats
from .adapters import PARSERS, parse
from .config import ConfigError, load

EXAMPLE = Path(__file__).resolve().parent / "example.toml"


def _split(v):
    return [x for x in (v or "").split(",") if x] or None


def cmd_run(a):
    t = load(a.trial)
    out = Path(a.out or Path("trial-results") / t.name).resolve()
    if a.reps:
        t.reps = a.reps
    if a.parallel:
        t.parallel = a.parallel
    jobs = runner.expand(t, {"question": _split(a.question), "arm": _split(a.arm), "agent": _split(a.agent)})
    if a.dry_run:
        for j in jobs:
            print(j.key())
        print(f"{len(jobs)} runs")
        return 0
    runs = runner.run_all(t, out, jobs, a.keep_workdirs, a.retry_failed)
    (out / "trial.json").write_text(json.dumps({"name": t.name, "baseline": t.baseline, "arms": [x.name for x in t.arms]}, indent=2) + "\n", encoding="utf-8")
    print()
    print(_report(out, t.baseline, t.name, a.effect))
    return 0


def _report(out: Path, baseline, name, effect, questions=None):
    meta = json.loads((out / "trial.json").read_text(encoding="utf-8")) if (out / "trial.json").exists() else {}
    runs = [r for r in runner.load_runs(out) if not questions or r["question"] in questions]
    if not runs:
        return "no runs found"
    arms = meta.get("arms") or sorted({r["arm"] for r in runs})
    return rep.markdown(rep.build(runs, baseline or meta.get("baseline"), arms, effect), meta.get("name", name or out.name))


def cmd_report(a):
    out = Path(a.results)
    if a.json:
        meta = json.loads((out / "trial.json").read_text(encoding="utf-8")) if (out / "trial.json").exists() else {}
        runs = [r for r in runner.load_runs(out) if not a.question or r["question"] in _split(a.question)]
        arms = meta.get("arms") or sorted({r["arm"] for r in runs})
        print(json.dumps(rep.build(runs, a.baseline or meta.get("baseline"), arms, a.effect), indent=2))
    else:
        print(_report(out, a.baseline, None, a.effect, _split(a.question)))
    return 0


def cmd_rescore(a):
    t = load(a.trial)
    n = runner.rescore(t, Path(a.results))
    print(f"rescored {n} runs")
    return 0


def cmd_plan(a):
    n = stats.runs_needed(a.cv, a.effect, a.alpha, a.power)
    print(f"About {n} runs per arm to detect a {a.effect:.0%} difference in mean cost "
          f"(coefficient of variation {a.cv}, alpha {a.alpha}, power {a.power}).")
    return 0


def cmd_parse(a):
    text = Path(a.file).read_text(encoding="utf-8", errors="replace")
    p = parse(a.kind, text)
    print(json.dumps({"answer": p.answer[:500], "tokens": p.tokens, "tool_calls": p.tool_calls, "cost_usd": p.cost_usd, "turns": p.turns, "error": p.error}, indent=2))
    return 0


def cmd_init(a):
    dest = Path(a.path)
    if dest.exists():
        print(f"{dest} already exists", file=sys.stderr)
        return 1
    dest.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"wrote {dest}; edit the repos, arms and questions, then: trialrig run {dest} --dry-run")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trialrig", description="Repeatable A/B trials of coding agents.")
    ap.add_argument("--version", action="version", version=f"trialrig {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a trial (resumes if interrupted) and print the report")
    r.add_argument("trial")
    r.add_argument("--out")
    r.add_argument("--reps", type=int)
    r.add_argument("--parallel", type=int)
    r.add_argument("--question", help="comma-separated question ids")
    r.add_argument("--arm")
    r.add_argument("--agent")
    r.add_argument("--effect", type=float, default=0.2)
    r.add_argument("--keep-workdirs", action="store_true")
    r.add_argument("--retry-failed", action="store_true", help="run again any run that did not finish ok")
    r.add_argument("--dry-run", action="store_true", help="list the runs without executing")
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="print the report for a results folder")
    p.add_argument("results")
    p.add_argument("--baseline")
    p.add_argument("--question", help="comma-separated question ids to include")
    p.add_argument("--effect", type=float, default=0.2)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)
    s = sub.add_parser("rescore", help="score stored answers again after editing the answer keys")
    s.add_argument("trial")
    s.add_argument("results")
    s.set_defaults(fn=cmd_rescore)
    pl = sub.add_parser("plan", help="how many runs per arm to detect a given difference")
    pl.add_argument("--cv", type=float, required=True, help="coefficient of variation of cost (the report prints it)")
    pl.add_argument("--effect", type=float, default=0.2)
    pl.add_argument("--alpha", type=float, default=0.05)
    pl.add_argument("--power", type=float, default=0.8)
    pl.set_defaults(fn=cmd_plan)
    pa = sub.add_parser("parse", help="read tokens and tool calls from a saved agent output")
    pa.add_argument("kind", choices=sorted(PARSERS))
    pa.add_argument("file")
    pa.set_defaults(fn=cmd_parse)
    i = sub.add_parser("init", help="write an example trial file")
    i.add_argument("path", nargs="?", default="trial.toml")
    i.set_defaults(fn=cmd_init)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f"trial file error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
