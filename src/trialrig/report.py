"""Summarise a finished trial.

Costs differ a lot between questions, so each run's tokens are divided by the baseline arm's mean for the same
question and agent before arms are compared. A relative cost of 1.10 means 10% more than the baseline. The interval
is a bootstrap over those relative costs; when it contains 1.00 the report says there is no clear difference.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from . import stats


PROVISIONAL_BELOW = 8  # a percentile bootstrap on fewer runs per arm is too optimistic to call firm


def _group(runs, *keys):
    g = defaultdict(list)
    for r in runs:
        g[tuple(r[k] for k in keys)].append(r)
    return g


def _tok(r):
    return (r.get("tokens") or {}).get("total")


def build(runs: list[dict], baseline: str | None, arms_order: list[str], effect: float = 0.2) -> dict:
    usable = [r for r in runs if r["status"] in ("ok", "timeout", "error") and not r.get("error")]
    base = baseline or arms_order[0]
    base_mean = {}
    for (q, agent), rs in _group([r for r in usable if r["arm"] == base and _tok(r)], "question", "agent").items():
        base_mean[(q, agent)] = mean(_tok(r) for r in rs)
    out = {"baseline": base, "agents": {}, "effect": effect}
    for agent in sorted({r["agent"] for r in usable}):
        ar = [r for r in usable if r["agent"] == agent]
        rows = []
        all_rel = []
        for arm in arms_order:
            rs = [r for r in ar if r["arm"] == arm]
            if not rs:
                continue
            toks = [_tok(r) for r in rs if _tok(r)]
            rel = [(_tok(r) / base_mean[(r["question"], agent)]) for r in rs if _tok(r) and (r["question"], agent) in base_mean]
            scored = [r for r in rs if r.get("correct") is not None]
            used = [r for r in rs if "used_tool" in r]
            row = {"arm": arm, "runs": len(rs), "failed": len([r for r in rs if r["status"] != "ok"]),
                   "correct": (sum(1 for r in scored if r["correct"]), len(scored)) if scored else None,
                   "recall": mean(r["recall"] for r in scored if r.get("recall") is not None) if any(r.get("recall") is not None for r in scored) else None,
                   "verified": (sum(1 for r in rs if r.get("verified")), len([r for r in rs if "verified" in r])) if any("verified" in r for r in rs) else None,
                   "tokens": stats.summary(toks) if toks else None,
                   "relative": None, "turns": mean(r["turns"] for r in rs if r.get("turns") is not None) if any(r.get("turns") is not None for r in rs) else None,
                   "used_tool": (sum(1 for r in used if r["used_tool"]), len(used)) if used else None,
                   "seconds": mean(r["seconds"] for r in rs if r.get("seconds") is not None) if any(r.get("seconds") is not None for r in rs) else None,
                   "cost_usd": mean(r["cost_usd"] for r in rs if r.get("cost_usd") is not None) if any(r.get("cost_usd") is not None for r in rs) else None}
            if len(rel) >= 2:
                ci = stats.mean_ci(rel)
                row["relative"] = {"value": mean(rel), "lo": ci[0], "hi": ci[1], "clear": arm != base and not (ci[0] <= 1.0 <= ci[1])}
                if arm != base:
                    all_rel += rel
            rows.append(row)
        cv = None
        if len(all_rel) >= 3:
            from statistics import stdev
            cv = stdev(all_rel) / mean(all_rel)
        out["agents"][agent] = {"arms": rows, "runs_needed": stats.runs_needed(cv, effect) if cv else None, "cv": cv}
    by_q = {}
    for (q, arm, agent), rs in _group([r for r in usable if _tok(r)], "question", "arm", "agent").items():
        by_q.setdefault(q, {})[f"{agent}/{arm}"] = mean(_tok(r) for r in rs)
    out["by_question"] = by_q
    return out


def _fmt_tokens(s):
    if not s:
        return "-"
    lo = f" ({s['ci95'][0]/1000:.0f}k to {s['ci95'][1]/1000:.0f}k)" if s.get("ci95") else ""
    return f"{s['mean']/1000:.0f}k{lo}"


def markdown(rep: dict, name: str) -> str:
    L = [f"# Trial report: {name}", "", f"Baseline arm: **{rep['baseline']}**. Relative cost is each run's tokens divided by the baseline's mean for the same question; 1.00 means equal, lower is cheaper."]
    for agent, d in rep["agents"].items():
        L += ["", f"## {agent}", "", "| arm | runs | correct | tokens per run (95% CI) | relative cost (95% CI) | verdict | turns | used tool | seconds |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in d["arms"]:
            rel = r["relative"]
            if r["arm"] == rep["baseline"]:
                relc, verdict = "1.00", "baseline"
            elif rel:
                relc = f"{rel['value']:.2f} ({rel['lo']:.2f} to {rel['hi']:.2f})"
                verdict = (("cheaper" if rel["value"] < 1 else "dearer") if rel["clear"] else "no clear difference")
                if rel["clear"] and r["runs"] < PROVISIONAL_BELOW:
                    verdict += " (provisional)"
            else:
                relc, verdict = "-", "too few runs"
            corr = f"{r['correct'][0]}/{r['correct'][1]}" if r["correct"] else "-"
            if r["verified"]:
                corr += f" (verified {r['verified'][0]}/{r['verified'][1]})"
            used = f"{r['used_tool'][0]}/{r['used_tool'][1]}" if r["used_tool"] else "-"
            runs = f"{r['runs']}" + (f" ({r['failed']} failed)" if r["failed"] else "")
            turns = "-" if r["turns"] is None else f"{r['turns']:.1f}"
            secs = "-" if r["seconds"] is None else f"{r['seconds']:.0f}"
            L.append(f"| {r['arm']} | {runs} | {corr} | {_fmt_tokens(r['tokens'])} | {relc} | {verdict} | {turns} | {used} | {secs} |")
        if d["runs_needed"]:
            L += ["", f"Run-to-run spread (coefficient of variation of relative cost) is {d['cv']:.2f}. To detect a {rep['effect']:.0%} difference with 80% power you would need about **{d['runs_needed']} runs per arm** (larger differences need fewer); this trial has {min(r['runs'] for r in d['arms'])}."]
    if rep["by_question"]:
        cols = sorted({k for v in rep["by_question"].values() for k in v})
        L += ["", "## Tokens by question (mean, thousands)", "", "| question | " + " | ".join(cols) + " |", "| --- |" + " --- |" * len(cols)]
        for q, v in rep["by_question"].items():
            L.append(f"| {q} | " + " | ".join(f"{v[c]/1000:.0f}k" if c in v else "-" for c in cols) + " |")
    L += ["", "Token totals are not comparable between agents (see adapters.py). Treat a verdict as evidence only when the interval excludes 1.00, and as provisional below 8 runs per arm."]
    return "\n".join(L) + "\n"
