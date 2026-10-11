"""Regression checks for agents: snapshot a trial's results, then compare a later run with the snapshot.

Models, agent CLIs and instruction files change under you. A snapshot keeps what each (agent, question, arm) cell did:
how many runs passed and the tokens each used. `compare` sets a newer run against it and says what got worse, what got
better and what is within the noise. With a handful of runs per cell most differences are within the noise, and the
comparison says so rather than guessing.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from . import __version__, stats

BINARY = {"claude": "claude", "codex": "codex", "cursor": "cursor-agent", "copilot": "copilot"}


def agent_versions(trial) -> dict:
    out = {}
    for a in trial.agents:
        binary = (a.command[0] if a.command else BINARY.get(a.kind, a.kind))
        path = shutil.which(binary)
        text = None
        if path:
            try:
                r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
                text = (r.stdout or r.stderr).strip().splitlines()[0][:80] if (r.stdout or r.stderr).strip() else None
            except (OSError, subprocess.TimeoutExpired):
                pass
        out[a.name] = {"cli": text, "model": a.model}
    return out


def passed(r: dict):
    """True or False for a run that has an outcome (verified, else correct), None when it has none."""
    for key in ("verified", "correct"):
        if r.get(key) is not None:
            return bool(r[key])
    return None


def cells(runs: list[dict], arm: str | None = None) -> dict:
    by = defaultdict(list)
    for r in runs:
        if r.get("status") in ("ok", "timeout", "error") and not r.get("error") and (arm is None or r["arm"] == arm):
            by[(r["agent"], r["question"], r["arm"])].append(r)
    out = {}
    for key, rs in by.items():
        outcomes = [passed(r) for r in rs if passed(r) is not None]
        toks = [(r.get("tokens") or {}).get("total") for r in rs if (r.get("tokens") or {}).get("total")]
        out[key] = {"n": len(rs), "scored": len(outcomes), "passed": sum(outcomes), "tokens": toks,
                    "turns": mean(r["turns"] for r in rs if r.get("turns") is not None) if any(r.get("turns") is not None for r in rs) else None}
    return out


def snapshot(runs: list[dict], name: str, trial_meta: dict | None = None, versions: dict | None = None) -> dict:
    meta = trial_meta or {}
    return {"name": name, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trialrig": __version__,
            "trial": meta.get("name"), "versions": versions or meta.get("versions") or {},
            "cells": [{"agent": a, "question": q, "arm": arm, **c} for (a, q, arm), c in sorted(cells(runs).items())]}


def fisher_worse(k0: int, n0: int, k1: int, n1: int) -> float:
    """One-sided exact p that the new pass rate is this much lower or lower than the old, if both come from one rate."""
    total, passes = n0 + n1, k0 + k1
    denom = math.comb(total, n1)
    return sum(math.comb(passes, x) * math.comb(total - passes, n1 - x) for x in range(max(0, n1 - (total - passes)), min(n1, passes) + 1) if x <= k1) / denom


def compare(old: dict, new_runs: list[dict], old_arm: str | None = None, new_arm: str | None = None, alpha: float = 0.1, min_runs: int = 4, tolerance: float = 0.1) -> dict:
    """Compare a snapshot with newer runs. With old_arm and new_arm, compare one arm of the snapshot with another of the new runs."""
    old_cells = {(c["agent"], c["question"], c["arm"]): c for c in old["cells"]}
    new_cells = cells(new_runs)
    rows = []
    for (agent, question, arm), n in sorted(new_cells.items()):
        if new_arm and arm != new_arm:
            continue
        o = old_cells.get((agent, question, old_arm or arm))
        if o is None:
            rows.append({"agent": agent, "question": question, "arm": arm, "verdict": "new cell", "detail": "not in the snapshot"})
            continue
        row = {"agent": agent, "question": question, "arm": arm, "old_n": o["n"], "new_n": n["n"], "verdict": "same", "detail": []}
        worse = False
        if o["scored"] and n["scored"]:
            row["old_pass"], row["new_pass"] = f"{o['passed']}/{o['scored']}", f"{n['passed']}/{n['scored']}"
            p = fisher_worse(o["passed"], o["scored"], n["passed"], n["scored"])
            if n["passed"] / n["scored"] < o["passed"] / o["scored"]:
                if p < alpha:
                    worse = True
                    row["detail"].append(f"pass rate fell {row['old_pass']} to {row['new_pass']} (p={p:.2f})")
                else:
                    row["detail"].append(f"pass rate lower, {row['old_pass']} to {row['new_pass']}, within noise at this size (p={p:.2f})")
            elif n["passed"] / n["scored"] > o["passed"] / o["scored"]:
                row["detail"].append(f"pass rate up, {row['old_pass']} to {row['new_pass']}")
        dearer = cheaper = False
        if len(o["tokens"]) < min_runs or len(n["tokens"]) < min_runs:
            row["detail"].append(f"fewer than {min_runs} runs on one side, so no token verdict")
        else:
            ci = stats.ratio_ci(n["tokens"], o["tokens"])
            if ci:
                row["tokens_ratio"] = ci
                if ci["clear"] and ci["lo"] <= 1 + tolerance and ci["hi"] >= 1 - tolerance:
                    row["detail"].append(f"tokens {ci['ratio']:.2f}x ({ci['lo']:.2f} to {ci['hi']:.2f}); the interval excludes 1.00 but not a {tolerance:.0%} change, so not called")
                elif ci["clear"] and ci["ratio"] > 1:
                    dearer = True
                    row["detail"].append(f"tokens {ci['ratio']:.2f}x ({ci['lo']:.2f} to {ci['hi']:.2f}), clearly more")
                elif ci["clear"]:
                    cheaper = True
                    row["detail"].append(f"tokens {ci['ratio']:.2f}x ({ci['lo']:.2f} to {ci['hi']:.2f}), clearly fewer")
        row["verdict"] = "regressed" if worse else "dearer" if dearer else "improved" if cheaper else "same"
        row["detail"] = "; ".join(row["detail"]) or "no clear change"
        rows.append(row)
    return {"snapshot": old.get("name"), "rows": rows}


def version_changes(old: dict, new_versions: dict) -> list[str]:
    out = []
    for agent, v in sorted(new_versions.items()):
        o = (old.get("versions") or {}).get(agent) or {}
        for k in ("cli", "model"):
            if o.get(k) != v.get(k) and (o.get(k) or v.get(k)):
                out.append(f"{agent} {k}: {o.get(k) or 'unrecorded'} -> {v.get(k) or 'unrecorded'}")
    return out


def markdown(result: dict, changes: list[str] | None = None) -> str:
    lines = [f"# Regression check against snapshot `{result['snapshot']}`", ""]
    if changes:
        lines += ["Changed since the snapshot: " + "; ".join(changes), ""]
    lines += ["| agent | question | arm | passed (old to new) | verdict | detail |", "| --- | --- | --- | --- | --- | --- |"]
    for r in result["rows"]:
        pw = f"{r.get('old_pass', '-')} to {r.get('new_pass', '-')}" if "old_pass" in r else "-"
        lines.append(f"| {r['agent']} | {r['question']} | {r['arm']} | {pw} | {r['verdict']} | {r['detail']} |")
    counts = {k: sum(1 for r in result["rows"] if r["verdict"] == k) for k in ("regressed", "dearer", "improved", "same", "new cell")}
    lines += ["", ", ".join(f"{v} {k}" for k, v in counts.items() if v) + ". Verdicts rest on a few runs per cell; `same` means no change could be shown, not that none exists."]
    return "\n".join(lines)


def failed(result: dict, fail_on: set) -> bool:
    return any(r["verdict"] in fail_on for r in result["rows"])
