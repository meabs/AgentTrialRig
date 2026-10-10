#!/usr/bin/env python3
"""Draw the diagrams on the project page from a real results folder.

  python generate.py RESULTS_DIR

RESULTS_DIR is a folder written by `trialrig run` (it contains runs/). workflow.svg needs no input.
"""
import json
import sys
from html import escape
from pathlib import Path
from statistics import mean

OUT = Path(__file__).resolve().parent
CSS = """
:root{--bg:#fbfaf7;--fg:#1d2330;--mut:#6b7385;--line:#c9cdd6;--box:#ffffff;--acc:#2f5fd0;--accbg:#e8eefc;--a:#2f5fd0;--b:#c2410c;--grid:#e6e8ee}
@media (prefers-color-scheme:dark){:root{--bg:#14171f;--fg:#e8eaf0;--mut:#9aa2b5;--line:#3a4152;--box:#1c212c;--acc:#7aa2ff;--accbg:#1f2a47;--a:#7aa2ff;--b:#f0a35a;--grid:#262c3a}}
text{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;fill:var(--fg);font-size:13px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.mut{fill:var(--mut)}.h{font-weight:600;font-size:12px;letter-spacing:.06em;text-transform:uppercase;fill:var(--mut)}
.box{fill:var(--box);stroke:var(--line)}.root{fill:var(--accbg);stroke:var(--acc)}
.ln{stroke:var(--acc);fill:none;stroke-width:1.6}.grid{stroke:var(--grid);stroke-width:1}.bg{fill:var(--bg)}
"""


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{escape(title)}">'
            f'<title>{escape(title)}</title><style>{CSS}</style><rect class="bg" width="{w}" height="{h}" rx="10"/>{body}</svg>')


def workflow():
    steps = [("Trial file", "repos, arms,\\nagents, questions"), ("Fresh clone", "one per run,\\narm setup applied"), ("Agent runs", "Claude Code, Codex,\\nCursor, Copilot"),
             ("Parse", "tokens, tool calls,\\nanswer, cost"), ("Score", "answer key,\\nverify command"), ("Report", "relative cost,\\ninterval, verdict")]
    bw, gap = 140, 28
    w = 24 * 2 + len(steps) * bw + (len(steps) - 1) * gap
    body = []
    for i, (t, s) in enumerate(steps):
        x = 24 + i * (bw + gap)
        body.append(f'<rect class="{"root" if i in (2, 5) else "box"}" x="{x}" y="30" width="{bw}" height="88" rx="8"/><text x="{x + 14}" y="58" style="font-weight:600">{escape(t)}</text>')
        for j, line in enumerate(s.replace("\\n", "\n").split("\n")):
            body.append(f'<text class="mut" x="{x + 14}" y="{80 + j * 17}">{escape(line)}</text>')
        if i:
            body.append(f'<path class="ln" d="M{x - gap + 4} 74 L{x - 5} 74"/><path class="ln" d="M{x - 11} 68 L{x - 5} 74 L{x - 11} 80"/>')
    body.append('<text class="mut" x="24" y="152">Every (question, arm, agent, repetition) is one run. Finished runs are kept, so an interrupted trial resumes.</text>')
    (OUT / "workflow.svg").write_text(svg(w, 172, "".join(body), "How a trial runs"), encoding="utf-8")


def spread(results):
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(results).glob("runs/*/*/*/*/run.json"))]
    runs = [r for r in runs if (r.get("tokens") or {}).get("total")]
    agents = sorted({r["agent"] for r in runs})
    arms = []
    for r in runs:
        if r["arm"] not in arms:
            arms.append(r["arm"])
    arms.sort(key=lambda a: (a != "plain", a))
    questions = sorted({r["question"] for r in runs})
    colors = {a: ("var(--a)" if i == 0 else "var(--b)") for i, a in enumerate(arms)}
    hi = max(r["tokens"]["total"] for r in runs)
    scale_max = (int(hi / 25000) + 1) * 25000
    pw, lx, rowh = 640, 150, 26
    body = ['<text class="h" x="24" y="26">Tokens per run: every run is a dot, the tick is the mean of its two repetitions</text>']
    y = 52
    for k, agent in enumerate(agents):
        body.append(f'<text style="font-weight:600" x="24" y="{y + 4}">{escape(agent)}</text>')
        y += 12
        for q in questions:
            for arm in arms:
                rs = [r["tokens"]["total"] for r in runs if r["agent"] == agent and r["question"] == q and r["arm"] == arm]
                if not rs:
                    continue
                cy = y + 14
                body.append(f'<text class="mono mut" x="24" y="{cy + 4}">{escape(q)}</text><text class="mono" x="{lx - 52}" y="{cy + 4}" fill="{colors[arm]}" style="fill:{colors[arm]}">{escape(arm)}</text>')
                body.append(f'<line class="grid" x1="{lx + 22}" y1="{cy}" x2="{lx + 22 + pw}" y2="{cy}"/>')
                for t in rs:
                    body.append(f'<circle cx="{lx + 22 + t / scale_max * pw:.1f}" cy="{cy}" r="5.5" style="fill:{colors[arm]}" fill-opacity=".85"/>')
                mx = lx + 22 + mean(rs) / scale_max * pw
                body.append(f'<line x1="{mx:.1f}" y1="{cy - 9}" x2="{mx:.1f}" y2="{cy + 9}" style="stroke:{colors[arm]}" stroke-width="2.2"/>')
                y += rowh
        y += 14
    ax_y = y - 2
    for t in range(0, scale_max + 1, 25000):
        x = lx + 22 + t / scale_max * pw
        body.append(f'<text class="mono mut" x="{x:.1f}" y="{ax_y + 16}" text-anchor="middle">{t // 1000}k</text>')
    h = ax_y + 56
    worst = 1.0
    for agent in agents:
        for q in questions:
            for arm in arms:
                rs = [r["tokens"]["total"] for r in runs if r["agent"] == agent and r["question"] == q and r["arm"] == arm]
                if len(rs) > 1:
                    worst = max(worst, max(rs) / min(rs))
    body.append(f'<text class="mut" x="24" y="{h - 12}">Real data from a {len(runs)}-run trial on Click. Repetitions of the same run differ by up to {worst:.1f} times.</text>')
    (OUT / "spread.svg").write_text(svg(lx + pw + 60, h, "".join(body), "Tokens per run in a real trial"), encoding="utf-8")


if __name__ == "__main__":
    workflow()
    if len(sys.argv) > 1:
        spread(sys.argv[1])
