"""Planning from a pilot, and deciding when a cell of a trial has said enough.

A cell is one agent on one question. Its arms are compared with the baseline arm on relative cost (a run's tokens over
the baseline's mean for that cell). A cell is `clear` when every other arm's interval excludes 1.00, `futile` when the
spread says it would need more runs than the cap, and `continue` otherwise.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean, stdev

from . import stats


def _tok(r):
    return r.get("tokens_total") or (r.get("tokens") or {}).get("total") or 0


def cells(runs: list[dict], baseline: str) -> dict:
    ok = [r for r in runs if r["status"] in ("ok", "timeout", "error") and not r.get("error") and _tok(r)]
    by = defaultdict(lambda: defaultdict(list))
    for r in ok:
        by[(r["agent"], r["question"])][r["arm"]].append(_tok(r))
    return by


def decide(runs: list[dict], baseline: str, effect: float = 0.2, min_runs: int = 4, max_reps: int = 12) -> list[dict]:
    out = []
    for (agent, question), arms in sorted(cells(runs, baseline).items()):
        base = arms.get(baseline, [])
        n = min((len(v) for v in arms.values()), default=0)
        row = {"agent": agent, "question": question, "runs_per_arm": n, "mean_tokens": mean(t for v in arms.values() for t in v),
               "arms": len(arms), "state": "continue", "detail": "", "needed": None, "ratios": {}}
        if not base or len(arms) < 2:
            row["detail"] = "no baseline runs yet"
            out.append(row)
            continue
        bm = mean(base)
        rel_all, clear_all = [], True
        for arm, toks in arms.items():
            if arm == baseline:
                continue
            ci = stats.ratio_ci(toks, base)
            if ci:
                row["ratios"][arm] = ci
                clear_all &= ci["clear"]
            else:
                clear_all = False
            rel_all += [t / bm for t in toks]
        cv = stdev(rel_all) / mean(rel_all) if len(rel_all) >= 3 and mean(rel_all) else None
        row["needed"] = stats.runs_needed(cv, effect) if cv else None
        if n >= min_runs and clear_all and row["ratios"]:
            row["state"], row["detail"] = "clear", "every arm's interval excludes 1.00"
        elif n >= min_runs and row["needed"] and row["needed"] > max_reps and n >= max_reps // 2:
            row["state"], row["detail"] = "futile", f"would need about {row['needed']} runs per arm to see a {effect:.0%} difference; the cap is {max_reps}"
        elif row["needed"]:
            row["detail"] = f"about {max(0, min(row['needed'], max_reps) - n)} more runs per arm"
        else:
            row["detail"] = f"fewer than {min_runs} runs per arm so far"
        out.append(row)
    return out


def projected_tokens(rows: list[dict], max_reps: int) -> int:
    total = 0
    for r in rows:
        if r["state"] == "continue":
            more = max(0, min(r["needed"] or max_reps, max_reps) - r["runs_per_arm"])
            total += more * r["arms"] * r["mean_tokens"]
    return int(total)


def table(rows: list[dict], max_reps: int, price: float | None = None) -> str:
    lines = ["| agent | question | runs per arm | relative cost (95% CI) | state | what it means |", "| --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        rc = "; ".join(f"{arm} {v['ratio']:.2f} ({v['lo']:.2f} to {v['hi']:.2f})" for arm, v in r["ratios"].items()) or "-"
        lines.append(f"| {r['agent']} | {r['question']} | {r['runs_per_arm']} | {rc} | {r['state']} | {r['detail']} |")
    rest = projected_tokens(rows, max_reps)
    lines.append("")
    lines.append(f"Projected further spend to settle the cells still open: about {rest/1e6:.1f}M tokens" + (f" (about ${rest/1e6*price:.0f} at ${price}/M)" if price else "") + ". Token totals differ in meaning between agents, so price each agent separately if you convert.")
    return "\n".join(lines)
