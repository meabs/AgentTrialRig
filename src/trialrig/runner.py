"""Expand the trial into runs and execute them.

One run is one (question, arm, agent, repetition). Each gets its own fresh clone so runs cannot influence each other,
then the arm's setup commands, then the agent with a hard timeout, then the optional verification command. Results are
written as `runs/<question>/<arm>/<agent>/<rep>/run.json` next to the raw output, so an interrupted trial resumes
where it stopped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .adapters import build_command, parse
from .config import Agent, Arm, Question, Trial
from .score import score


@dataclass(frozen=True)
class Job:
    question: Question
    arm: Arm
    agent: Agent
    rep: int

    def key(self) -> str:
        return f"{self.question.id}/{self.arm.name}/{self.agent.name}/{self.rep}"


def expand(t: Trial, only: dict | None = None) -> list[Job]:
    only = only or {}
    jobs = []
    for rep in range(1, t.reps + 1):
        for q in t.questions:
            for arm in t.arms:
                for agent in t.agents:
                    if only.get("question") and q.id not in only["question"]:
                        continue
                    if only.get("arm") and arm.name not in only["arm"]:
                        continue
                    if only.get("agent") and agent.name not in only["agent"]:
                        continue
                    jobs.append(Job(q, arm, agent, rep))
    return jobs


def run_dir(out: Path, job: Job) -> Path:
    return out / "runs" / job.question.id / job.arm.name / job.agent.name / str(job.rep)


def _sh(cmd: str, cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def _rmtree(path: Path) -> None:
    """Remove a directory tree, including the read-only files git leaves in .git on Windows."""
    def onerror(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda f, p, e: onerror(f, p, e))
    else:
        shutil.rmtree(path, onerror=onerror)


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _clone(t: Trial, repo, dest: Path) -> None:
    source = repo.source
    if not re.match(r"^[a-z]+://|^git@", source):
        source = str((t.base_dir / source).resolve())
    r = subprocess.run(["git", "clone", "-q", source, str(dest)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git clone failed: {r.stderr.strip()[:300]}")
    if repo.ref:
        r = subprocess.run(["git", "-C", str(dest), "checkout", "-q", repo.ref], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"git checkout {repo.ref} failed: {r.stderr.strip()[:300]}")


def run_job(t: Trial, job: Job, out: Path, keep_workdir: bool = False) -> dict:
    rdir = run_dir(out, job)
    rdir.mkdir(parents=True, exist_ok=True)
    work = out / "work" / job.question.id / job.arm.name / job.agent.name / str(job.rep)
    if work.exists():
        _rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    rec = {"question": job.question.id, "arm": job.arm.name, "agent": job.agent.name, "rep": job.rep, "repo": job.question.repo,
           "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trialrig": __version__, "status": "error"}
    env = {**os.environ, **job.agent.env, "TRIAL_DIR": str(work), "TRIAL_ARM": job.arm.name, "TRIAL_AGENT": job.agent.name}
    try:
        _clone(t, t.repos[job.question.repo], work)
        if job.arm.overlay:
            src = (t.base_dir / job.arm.overlay).resolve()
            shutil.copytree(src, work, dirs_exist_ok=True)
        for cmd in job.arm.setup:
            r = _sh(cmd, work, env, t.timeout)
            if r.returncode:
                raise RuntimeError(f"arm setup failed ({cmd}): {(r.stderr or r.stdout).strip()[-300:]}")
        prompt = job.question.prompt + (" " + t.prompt_suffix if t.prompt_suffix else "")
        cmd = build_command(job.agent, prompt)
        t0 = time.monotonic()
        popen = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 start_new_session=(os.name != "nt"))
        try:
            stdout, stderr = popen.communicate(timeout=t.timeout)
            rec["exit_code"] = popen.returncode
        except subprocess.TimeoutExpired:
            _kill(popen)
            stdout, stderr = popen.communicate()
            rec["exit_code"] = None
            rec["timed_out"] = True
        rec["seconds"] = round(time.monotonic() - t0, 1)
        (rdir / "raw.out").write_text(stdout or "", encoding="utf-8")
        (rdir / "raw.err").write_text(stderr or "", encoding="utf-8")
        parsed = parse(job.agent.kind, stdout or "")
        rec.update(answer=parsed.answer, tokens=parsed.tokens, tool_calls=parsed.tool_calls, cost_usd=parsed.cost_usd, turns=parsed.turns)
        rec["parse_error"] = parsed.error
        if job.arm.uses:
            rec["used_tool"] = any(re.search(job.arm.uses, c["text"]) for c in parsed.tool_calls)
        rec.update(score(job.question, parsed.answer))
        if job.question.verify:
            try:
                v = _sh(job.question.verify, work, env, t.timeout)
                rec["verified"] = v.returncode == 0
                (rdir / "verify.log").write_text((v.stdout or "") + (v.stderr or ""), encoding="utf-8")
            except subprocess.TimeoutExpired:
                rec["verified"] = False
        rec["status"] = "timeout" if rec.get("timed_out") else ("ok" if not parsed.error else "error")
    except Exception as e:  # a failed run is data, not a crash of the whole trial
        rec["error"] = f"{type(e).__name__}: {e}"[:400]
    finally:
        if not keep_workdir and work.exists():
            _rmtree(work)
    (rdir / "run.json").write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return rec


def prepare_arms(t: Trial, arms: list[Arm], out: Path, log=print) -> None:
    env = {**os.environ, "TRIAL_OUT": str(out.resolve())}
    for arm in arms:
        for cmd in arm.prepare:
            log(f"preparing arm {arm.name}: {cmd}")
            r = _sh(cmd, t.base_dir, env, t.timeout)
            if r.returncode:
                raise RuntimeError(f"prepare failed for arm {arm.name} ({cmd}): {(r.stderr or r.stdout).strip()[-300:]}")


def run_all(t: Trial, out: Path, jobs: list[Job], keep_workdirs: bool = False, retry_failed: bool = False, log=print) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    if retry_failed:
        for j in jobs:
            p = run_dir(out, j) / "run.json"
            if p.exists() and json.loads(p.read_text(encoding="utf-8")).get("status") != "ok":
                p.unlink()
    todo = [j for j in jobs if not (run_dir(out, j) / "run.json").exists()]
    prepare_arms(t, [a for a in t.arms if any(j.arm.name == a.name for j in todo)], out, log)
    log(f"{len(jobs)} runs planned, {len(jobs) - len(todo)} already done, {len(todo)} to run (parallel {t.parallel})")
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, t.parallel)) as pool:
        futures = {pool.submit(run_job, t, j, out, keep_workdirs): j for j in todo}
        for f in as_completed(futures):
            done += 1
            j, r = futures[f], f.result()
            tok = (r.get("tokens") or {}).get("total")
            log(f"[{done}/{len(todo)}] {j.key()}: {r['status']}" + (f", {tok:,} tokens" if tok else "") + (f", correct={r['correct']}" if r.get("correct") is not None else "") + (f": {r.get('error') or r.get('parse_error')}" if r["status"] != "ok" else ""))
    return load_runs(out)


def load_runs(out: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "runs").glob("*/*/*/*/run.json"))]


def rescore(t: Trial, out: Path) -> int:
    qs = {q.id: q for q in t.questions}
    n = 0
    for p in sorted((out / "runs").glob("*/*/*/*/run.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        q = qs.get(rec["question"])
        if q and rec.get("answer") is not None:
            rec.update(score(q, rec["answer"]))
            p.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
            n += 1
    return n
