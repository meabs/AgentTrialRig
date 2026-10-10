"""Build each agent's command line and read its output.

Every adapter returns the same record so trials can compare agents: the final answer, token counts, a list of tool
calls (what the agent actually did, not what it says it did), cost where the agent reports it, and a turn count.

Token totals are not comparable between agents: Claude Code reports cache reads and writes separately, Codex folds
cached input into its input count, and Copilot reports nothing. Compare an agent only with itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import Agent


@dataclass
class Parsed:
    answer: str = ""
    tokens: dict | None = None  # input, output, cache_read, cache_write, total
    tool_calls: list[dict] = field(default_factory=list)  # {"tool": str, "text": str}
    cost_usd: float | None = None
    turns: int | None = None
    error: str | None = None


def build_command(agent: Agent, prompt: str) -> list[str]:
    if agent.command:
        cmd = [part.replace("{prompt}", prompt) for part in agent.command]
        return cmd + agent.args
    model = ["--model", agent.model] if agent.model else []
    if agent.kind == "claude":
        tools = ["Bash", "Read", "Grep", "Glob"] + (["Edit", "Write"] if agent.edits else [])
        return ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--allowedTools", *tools, *model, *agent.args]
    if agent.kind == "codex":
        return ["codex", "exec", "-s", "workspace-write" if agent.edits else "read-only", "--skip-git-repo-check", "--json", *model, *agent.args, prompt]
    if agent.kind == "cursor":
        return ["cursor-agent", "-p", "--force", "--output-format", "stream-json", *model, *agent.args, prompt]
    if agent.kind == "copilot":
        return ["copilot", "-p", prompt, "--allow-all-tools", *model, *agent.args]
    raise ValueError(f"kind '{agent.kind}' needs an explicit command")


def _events(text: str):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _tool_text(tool_input: dict) -> str:
    for key in ("command", "pattern", "file_path", "path", "query"):
        if isinstance(tool_input.get(key), str):
            return tool_input[key]
    return json.dumps(tool_input)[:200]


def parse_claude(text: str) -> Parsed:
    p = Parsed()
    for e in _events(text):
        if e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    p.tool_calls.append({"tool": c.get("name", ""), "text": _tool_text(c.get("input") or {})})
        elif e.get("type") == "result":
            u = e.get("usage") or {}
            if u:
                inp, out = u.get("input_tokens", 0), u.get("output_tokens", 0)
                cr, cw = u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0)
                p.tokens = {"input": inp, "output": out, "cache_read": cr, "cache_write": cw, "total": inp + out + cr + cw}
            p.cost_usd = e.get("total_cost_usd")
            p.turns = e.get("num_turns")
            p.answer = e.get("result") or ""
            if e.get("is_error"):
                p.error = str(e.get("subtype") or "error")
    if p.tokens is None and not p.answer:
        p.error = p.error or "no result event in the output"
    return p


def parse_codex(text: str) -> Parsed:
    p = Parsed()
    inp = out = cached = 0
    seen_usage = False
    for e in _events(text):
        item = e.get("item") or {}
        if e.get("type") == "item.completed":
            if item.get("type") == "command_execution":
                p.tool_calls.append({"tool": "shell", "text": item.get("command", "")})
            elif item.get("type") == "agent_message":
                p.answer = item.get("text", "")
        elif e.get("type") == "turn.completed":
            u = e.get("usage") or {}
            seen_usage = True
            inp += u.get("input_tokens", 0)
            out += u.get("output_tokens", 0)
            cached += u.get("cached_input_tokens", 0)
        elif e.get("type") in ("error", "turn.failed"):
            p.error = str(e.get("message") or e.get("error") or e.get("type"))[:200]
    if seen_usage:
        p.tokens = {"input": inp, "output": out, "cache_read": cached, "cache_write": 0, "total": inp + out}
    p.turns = len(p.tool_calls)
    if not p.answer and not p.error:
        p.error = "no agent message in the output"
    return p


def parse_cursor(text: str) -> Parsed:
    p = Parsed()
    for e in _events(text):
        if e.get("type") == "tool_call" and e.get("subtype") == "started":
            for key, val in (e.get("tool_call") or {}).items():
                if key.endswith("ToolCall") and isinstance(val, dict):
                    p.tool_calls.append({"tool": key[:-8], "text": _tool_text(val.get("args") or {})})
        elif e.get("type") == "result":
            p.answer = e.get("result") or ""
            u = e.get("usage") or {}
            if u:
                a, b, c, d = u.get("inputTokens", 0), u.get("outputTokens", 0), u.get("cacheReadTokens", 0), u.get("cacheWriteTokens", 0)
                p.tokens = {"input": a, "output": b, "cache_read": c, "cache_write": d, "total": a + b + c + d}
            if e.get("is_error"):
                p.error = str(e.get("subtype") or "error")
    p.turns = len(p.tool_calls)
    if not p.answer and not p.error:
        p.error = "no result event in the output"
    return p


def parse_text(text: str) -> Parsed:
    return Parsed(answer=text.strip(), error=None if text.strip() else "empty output")


PARSERS = {"claude": parse_claude, "codex": parse_codex, "cursor": parse_cursor, "copilot": parse_text, "text": parse_text}


def parse(kind: str, text: str) -> Parsed:
    return PARSERS[kind](text)
