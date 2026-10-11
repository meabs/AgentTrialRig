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
    if a.stop_when_decided:
        from . import plan as planmod

        done_cells: set = set()
        cap = t.reps
        for rep in range(1, cap + 1):
            todo = [j for j in jobs if j.rep == rep and (j.agent.name, j.question.id) not in done_cells]
            if not todo:
                break
            runner.run_all(t, out, todo, a.keep_workdirs, a.retry_failed)
            for row in planmod.decide(runner.load_runs(out), t.baseline, a.effect, a.min_runs, cap):
                if row["state"] in ("clear", "futile"):
                    done_cells.add((row["agent"], row["question"]))
                    print(f"[stop] {row['agent']}/{row['question']} after {rep} runs per arm: {row['state']}, {row['detail']}")
        runs = []
    else:
        runs = runner.run_all(t, out, jobs, a.keep_workdirs, a.retry_failed)
    from . import regress

    (out / "trial.json").write_text(json.dumps({"name": t.name, "baseline": t.baseline, "arms": [x.name for x in t.arms], "versions": regress.agent_versions(t)}, indent=2) + "\n", encoding="utf-8")
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


def _meta(out: Path) -> dict:
    return json.loads((out / "trial.json").read_text(encoding="utf-8")) if (out / "trial.json").exists() else {}


def cmd_snapshot(a):
    from . import regress

    out = Path(a.results)
    runs = runner.load_runs(out)
    if not runs:
        raise SystemExit(f"no runs found in {out}")
    snap = regress.snapshot(runs, a.name or _meta(out).get("name") or out.name, _meta(out))
    Path(a.to).write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {a.to}: {len(snap['cells'])} cells from {len(runs)} runs")
    return 0


def _compare(a, new_runs, new_versions):
    from . import regress

    old = json.loads(Path(a.snapshot).read_text(encoding="utf-8"))
    result = regress.compare(old, new_runs, a.old_arm, a.new_arm, a.alpha, a.min_runs, a.tolerance)
    print(regress.markdown(result, regress.version_changes(old, new_versions)))
    return 1 if regress.failed(result, set(_split(a.fail_on) or [])) else 0


def cmd_compare(a):
    out = Path(a.results)
    return _compare(a, runner.load_runs(out), _meta(out).get("versions") or {})


def cmd_regress(a):
    from . import regress

    t = load(a.trial)
    out = Path(a.out or Path("trial-results") / (t.name + "-regress")).resolve()
    if a.reps:
        t.reps = a.reps
    if a.parallel:
        t.parallel = a.parallel
    jobs = runner.expand(t, {"question": _split(a.question), "arm": _split(a.arm), "agent": _split(a.agent)})
    runner.run_all(t, out, jobs, False, a.retry_failed)
    versions = regress.agent_versions(t)
    (out / "trial.json").write_text(json.dumps({"name": t.name, "baseline": t.baseline, "arms": [x.name for x in t.arms], "versions": versions}, indent=2) + "\n", encoding="utf-8")
    return _compare(a, runner.load_runs(out), versions)


def cmd_rescore(a):
    t = load(a.trial)
    n = runner.rescore(t, Path(a.results))
    print(f"rescored {n} runs")
    return 0


def cmd_plan(a):
    if a.results:
        from . import plan as planmod

        out = Path(a.results)
        meta = json.loads((out / "trial.json").read_text(encoding="utf-8")) if (out / "trial.json").exists() else {}
        runs = runner.load_runs(out)
        base = a.baseline or meta.get("baseline") or (meta.get("arms") or [None])[0]
        print(planmod.table(planmod.decide(runs, base, a.effect, a.min_runs, a.max_reps), a.max_reps, a.price_per_mtok))
        return 0
    if a.cv is None:
        raise SystemExit("plan needs --cv, or --results with a pilot's results folder")
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
    r.add_argument("--stop-when-decided", action="store_true", help="run one repetition at a time and stop each agent and question once its verdict is clear or hopeless; --reps is the cap")
    r.add_argument("--min-runs", type=int, default=4, help="with --stop-when-decided: runs per arm before a cell may stop")
    r.add_argument("--dry-run", action="store_true", help="list the runs without executing")
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("report", help="print the report for a results folder")
    p.add_argument("results")
    p.add_argument("--baseline")
    p.add_argument("--question", help="comma-separated question ids to include")
    p.add_argument("--effect", type=float, default=0.2)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)
    sn = sub.add_parser("snapshot", help="save what a results folder did, to compare later runs against")
    sn.add_argument("results")
    sn.add_argument("--to", default="baseline.json")
    sn.add_argument("--name")
    sn.set_defaults(fn=cmd_snapshot)

    def regress_args(p):
        p.add_argument("--snapshot", default="baseline.json")
        p.add_argument("--old-arm", help="compare the snapshot's arm of this name with the new runs' --new-arm (default: same arm)")
        p.add_argument("--new-arm")
        p.add_argument("--alpha", type=float, default=0.1, help="one-sided p below which a lower pass rate counts as a regression")
        p.add_argument("--min-runs", type=int, default=4, help="runs per side before a token verdict is given")
        p.add_argument("--tolerance", type=float, default=0.1, help="a token change smaller than this fraction is never called, even if the interval excludes 1.00")
        p.add_argument("--fail-on", default="regressed", help="comma-separated verdicts that make the exit code 1 (regressed, dearer)")

    cp = sub.add_parser("compare", help="compare a results folder with a snapshot; exit 1 on a regression")
    cp.add_argument("results")
    regress_args(cp)
    cp.set_defaults(fn=cmd_compare)
    rg = sub.add_parser("regress", help="run a trial and compare it with a snapshot in one step")
    rg.add_argument("trial")
    rg.add_argument("--out")
    rg.add_argument("--reps", type=int)
    rg.add_argument("--parallel", type=int)
    rg.add_argument("--question")
    rg.add_argument("--arm")
    rg.add_argument("--agent")
    rg.add_argument("--retry-failed", action="store_true")
    regress_args(rg)
    rg.set_defaults(fn=cmd_regress)
    s = sub.add_parser("rescore", help="score stored answers again after editing the answer keys")
    s.add_argument("trial")
    s.add_argument("results")
    s.set_defaults(fn=cmd_rescore)
    pl = sub.add_parser("plan", help="how many runs per arm to detect a given difference")
    pl.add_argument("--cv", type=float, help="coefficient of variation of cost (the report prints it)")
    pl.add_argument("--results", help="a pilot's results folder: plan per agent and question, with the spend still needed")
    pl.add_argument("--baseline")
    pl.add_argument("--min-runs", type=int, default=4, help="runs per arm before a cell may be called")
    pl.add_argument("--max-reps", type=int, default=12, help="the most runs per arm you are willing to spend")
    pl.add_argument("--price-per-mtok", type=float, help="optional price per million tokens, to show a dollar figure")
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
