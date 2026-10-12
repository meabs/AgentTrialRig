"""Trial definition, read from a TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("claude", "codex", "cursor", "copilot", "text")


class ConfigError(ValueError):
    pass


@dataclass
class Repo:
    name: str
    source: str
    ref: str | None = None


@dataclass
class Arm:
    name: str
    prepare: list[str] = field(default_factory=list)  # shell commands run once, before any run (shared installs, warm-ups)
    setup: list[str] = field(default_factory=list)  # shell commands run in each fresh clone before the agent starts
    overlay: str | None = None  # a directory copied over the clone
    uses: str | None = None  # regex; a run "used" the arm's tool if any tool call matches
    repos: dict[str, str] = field(default_factory=dict)  # logical repo name -> another [[repos]] entry to clone for this arm
    parallel_setup: bool = False  # setup commands run one at a time across runs unless this is true (they often touch shared state)


@dataclass
class Agent:
    name: str
    kind: str
    command: list[str] | None = None  # overrides the built-in command; "{prompt}" is replaced
    args: list[str] = field(default_factory=list)  # appended to the built-in command
    model: str | None = None
    edits: bool = False  # let the agent change files (needed for change tasks; read-only otherwise)
    sandbox: str | None = None  # codex only: read-only, workspace-write or danger-full-access (overrides `edits`)
    env: dict[str, str] = field(default_factory=dict)
    isolate: list[str] = field(default_factory=list)  # macOS only: folders the agent may not read or write; its own work folder stays open (use with codex sandbox = "danger-full-access")


@dataclass
class Question:
    id: str
    repo: str
    prompt: str
    expect: list[str] = field(default_factory=list)  # regexes the answer must match
    min_recall: float = 1.0  # fraction of `expect` that must match
    forbid: list[str] = field(default_factory=list)  # regexes the answer must not match
    verify: str | None = None  # shell command run in the clone after the agent; exit 0 = verified


@dataclass
class Trial:
    name: str
    reps: int = 2
    parallel: int = 4
    timeout: int = 900
    prompt_suffix: str = ""
    claim: bool = False  # ask each agent for a VERIFICATION line and compare it with the verify command
    baseline: str | None = None
    repos: dict[str, Repo] = field(default_factory=dict)
    arms: list[Arm] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    base_dir: Path = Path(".")


def _need(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"{where}: missing '{key}'")
    return d[key]


def load(path: str | Path) -> Trial:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    d = raw.get("defaults", {})
    t = Trial(name=raw.get("name", path.stem), reps=int(d.get("reps", 2)), parallel=int(d.get("parallel", 4)),
              timeout=int(d.get("timeout", 900)), prompt_suffix=d.get("prompt_suffix", ""), claim=bool(d.get("claim", False)), baseline=d.get("baseline"),
              base_dir=path.resolve().parent)
    for i, r in enumerate(raw.get("repos", [])):
        w = f"repos[{i}]"
        repo = Repo(_need(r, "name", w), _need(r, "source", w), r.get("ref"))
        t.repos[repo.name] = repo
    for i, a in enumerate(raw.get("arms", [])):
        w = f"arms[{i}]"
        t.arms.append(Arm(_need(a, "name", w), list(a.get("prepare", [])), list(a.get("setup", [])), a.get("overlay"), a.get("uses"), dict(a.get("repos", {})), bool(a.get("parallel_setup", False))))
    for i, a in enumerate(raw.get("agents", [])):
        w = f"agents[{i}]"
        name = _need(a, "name", w)
        kind = a.get("kind", name)
        if kind not in KINDS:
            raise ConfigError(f"{w}: kind '{kind}' is not one of {', '.join(KINDS)}")
        t.agents.append(Agent(name, kind, a.get("command"), list(a.get("args", [])), a.get("model"), bool(a.get("edits", False)), a.get("sandbox"), dict(a.get("env", {})), [str(x) for x in a.get("isolate", [])]))
    for i, q in enumerate(raw.get("questions", [])):
        w = f"questions[{i}]"
        qq = Question(_need(q, "id", w), _need(q, "repo", w), _need(q, "prompt", w), list(q.get("expect", [])),
                      float(q.get("min_recall", 1.0)), list(q.get("forbid", [])), q.get("verify"))
        if qq.repo not in t.repos:
            raise ConfigError(f"{w}: repo '{qq.repo}' is not defined under [[repos]]")
        t.questions.append(qq)
    for what, items in (("repos", t.repos), ("arms", t.arms), ("agents", t.agents), ("questions", t.questions)):
        if not items:
            raise ConfigError(f"no [[{what}]] defined")
    for what, names in (("arm", [a.name for a in t.arms]), ("agent", [a.name for a in t.agents]), ("question", [q.id for q in t.questions])):
        if len(set(names)) != len(names):
            raise ConfigError(f"duplicate {what} names")
        if any(("/" in n or " " in n) for n in names):
            raise ConfigError(f"{what} names may not contain '/' or spaces: {names}")
    for arm in t.arms:
        for logical, actual in arm.repos.items():
            if logical not in t.repos or actual not in t.repos:
                raise ConfigError(f"arm '{arm.name}': repos mapping {logical} -> {actual} names an undefined repo")
    if t.baseline and t.baseline not in {a.name for a in t.arms}:
        raise ConfigError(f"baseline '{t.baseline}' is not an arm")
    return t
