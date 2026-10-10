import json
import subprocess
import sys
from pathlib import Path

import pytest

from trialrig import adapters, config, report, runner, score as scoring, stats
from trialrig.cli import main

HERE = Path(__file__).parent


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "src-repo"
    r.mkdir()
    (r / "a.txt").write_text("hello\n")
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "add", "."], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.email=a@b", "-c", "user.name=x", "commit", "-qm", "i"], check=True)
    return r


def write_trial(tmp_path, repo, extra=""):
    f = tmp_path / "trial.toml"
    py = Path(sys.executable).as_posix()
    fake = (HERE / "fake_agent.py").as_posix()
    f.write_text(f'''
name = "t"
[defaults]
reps = 3
parallel = 3
timeout = 60
baseline = "plain"
[[repos]]
name = "r"
source = "{repo.as_posix()}"
[[arms]]
name = "plain"
[[arms]]
name = "tool"
setup = ["\\"{py}\\" -c \\"open('MARKER','w').write('x')\\""]
uses = "bin/kg"
[[agents]]
name = "fake"
kind = "claude"
command = ["{py}", "{fake}", "{{prompt}}"]
[[questions]]
id = "q1"
repo = "r"
prompt = "ANSWER: the classes are Alpha and Beta TOKENS: 1000"
expect = ["Alpha", "Beta"]
[[questions]]
id = "q2"
repo = "r"
prompt = "ANSWER: only Alpha TOKENS: 2000"
expect = ["Alpha", "Beta"]
min_recall = 1.0
{extra}
''')
    return f


def test_parsers_read_tokens_tools_and_answers():
    claude = "\n".join(json.dumps(e) for e in [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "kg callers x"}}]}},
        {"type": "result", "result": "done", "num_turns": 3, "total_cost_usd": 0.5, "usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 400}}])
    p = adapters.parse("claude", claude)
    assert p.answer == "done" and p.tokens["total"] == 433 and p.tool_calls == [{"tool": "Bash", "text": "kg callers x"}] and p.cost_usd == 0.5

    codex = "\n".join(json.dumps(e) for e in [
        {"type": "item.completed", "item": {"type": "command_execution", "command": "rg x"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 5}}])
    p = adapters.parse("codex", codex)
    assert p.answer == "ok" and p.tokens["total"] == 105 and p.tool_calls[0]["text"] == "rg x"

    cursor = "\n".join(json.dumps(e) for e in [
        {"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "ls"}}}},
        {"type": "result", "result": "yes", "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 5, "cacheWriteTokens": 1}}])
    p = adapters.parse("cursor", cursor)
    assert p.tokens["total"] == 18 and p.tool_calls == [{"tool": "shell", "text": "ls"}]
    assert adapters.parse("copilot", "just text").tokens is None


def test_empty_output_is_an_error_not_a_crash():
    assert adapters.parse("claude", "garbage").error
    assert adapters.parse("codex", "").error


def test_scoring_recall_and_forbidden(tmp_path):
    q = config.Question("q", "r", "p", expect=["A", "B"], min_recall=0.5, forbid=["Z"])
    assert scoring.score(q, "A only")["correct"] is True
    assert scoring.score(q, "A only Z")["correct"] is False
    assert scoring.score(q, "nothing")["recall"] == 0 and scoring.score(q, "nothing")["correct"] is False


def test_config_errors_are_specific(tmp_path):
    f = tmp_path / "t.toml"
    f.write_text('[[repos]]\nname="r"\nsource="x"\n[[arms]]\nname="a"\n[[agents]]\nname="claude"\n[[questions]]\nid="q"\nrepo="nope"\nprompt="p"\n')
    with pytest.raises(config.ConfigError, match="repo 'nope'"):
        config.load(f)
    f.write_text('[[repos]]\nname="r"\nsource="x"\n')
    with pytest.raises(config.ConfigError, match=r"no \[\[arms\]\]"):
        config.load(f)


def test_full_trial_runs_scores_and_reports(tmp_path, repo, capsys):
    f = write_trial(tmp_path, repo)
    out = tmp_path / "out"
    assert main(["run", str(f), "--out", str(out)]) == 0
    runs = runner.load_runs(out)
    assert len(runs) == 2 * 2 * 3  # questions x arms x reps
    by = {(r["question"], r["arm"]): r for r in runs}
    assert by[("q1", "plain")]["correct"] is True and by[("q2", "plain")]["correct"] is False
    assert by[("q1", "plain")]["tokens"]["total"] == 1010 and by[("q1", "tool")]["tokens"]["total"] == 510
    assert by[("q1", "tool")]["used_tool"] is True and "used_tool" not in by[("q1", "plain")]
    shown = capsys.readouterr().out
    assert "Baseline arm: **plain**" in shown and "| tool |" in shown
    assert not (out / "work" / "q1").exists() or not any((out / "work").rglob("a.txt"))  # work dirs are cleaned up


def test_resume_skips_finished_runs(tmp_path, repo, capsys):
    f = write_trial(tmp_path, repo)
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--arm", "plain"])
    capsys.readouterr()
    main(["run", str(f), "--out", str(out), "--arm", "plain"])
    assert "0 to run" in capsys.readouterr().out


def test_rescore_after_editing_the_key(tmp_path, repo):
    f = write_trial(tmp_path, repo)
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--arm", "plain", "--question", "q2"])
    assert all(r["correct"] is False for r in runner.load_runs(out))
    f.write_text(f.read_text().replace('expect = ["Alpha", "Beta"]\nmin_recall = 1.0', 'expect = ["Alpha"]\nmin_recall = 1.0'))
    assert main(["rescore", str(f), str(out)]) == 0
    assert all(r["correct"] is True for r in runner.load_runs(out))


def test_a_broken_arm_setup_is_recorded_and_does_not_stop_the_trial(tmp_path, repo):
    f = write_trial(tmp_path, repo)
    f.write_text(f.read_text().replace('setup = ["\\"', 'setup = ["exit 3", "\\"', 1))
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--question", "q1"])
    runs = runner.load_runs(out)
    assert {r["status"] for r in runs if r["arm"] == "tool"} == {"error"} and all(r["status"] == "ok" for r in runs if r["arm"] == "plain")
    assert "arm setup failed" in next(r for r in runs if r["arm"] == "tool")["error"]


def test_timeout_kills_the_agent(tmp_path, repo):
    f = write_trial(tmp_path, repo)
    text = f.read_text().replace("timeout = 60", "timeout = 1").replace(f'"{(HERE / "fake_agent.py").as_posix()}"', '"-c", "import time; time.sleep(30)", "--"')
    f.write_text(text)
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--question", "q1", "--arm", "plain", "--reps", "1"])
    r = runner.load_runs(out)[0]
    assert r["status"] == "timeout" and r["timed_out"] is True


def test_verify_command_is_recorded(tmp_path, repo):
    f = write_trial(tmp_path, repo, "")
    f.write_text(f.read_text().replace('expect = ["Alpha", "Beta"]\n[[questions]]', 'expect = ["Alpha", "Beta"]\nverify = "test -f a.txt"\n[[questions]]'))
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--question", "q1", "--arm", "plain", "--reps", "1"])
    assert runner.load_runs(out)[0]["verified"] is True


def test_report_calls_a_real_difference_clear_and_noise_unclear():
    mk = lambda arm, q, tok: {"question": q, "arm": arm, "agent": "a", "status": "ok", "tokens": {"total": tok}, "correct": True, "recall": 1.0, "turns": 2, "seconds": 5}
    runs = [mk("plain", "q1", t) for t in (1000, 1010, 990, 1005)] + [mk("graph", "q1", t) for t in (500, 510, 495, 505)]
    d = report.build(runs, "plain", ["plain", "graph"])
    g = next(r for r in d["agents"]["a"]["arms"] if r["arm"] == "graph")
    assert g["relative"]["clear"] and g["relative"]["value"] < 0.6
    noisy = [mk("plain", "q1", t) for t in (1000, 1500, 600, 1300)] + [mk("graph", "q1", t) for t in (1100, 700, 1600, 900)]
    d = report.build(noisy, "plain", ["plain", "graph"])
    g = next(r for r in d["agents"]["a"]["arms"] if r["arm"] == "graph")
    assert not g["relative"]["clear"]
    assert "no clear difference" in report.markdown(d, "x")


def test_stats_helpers():
    assert stats.runs_needed(0.3, 0.2) > stats.runs_needed(0.1, 0.2) >= 2
    assert stats.mean_ci([1.0]) is None
    lo, hi = stats.mean_ci([10, 11, 9, 10, 12, 8])
    assert lo < 10 < hi


def test_prepare_runs_once_per_trial_and_failed_runs_can_be_retried(tmp_path, repo):
    f = write_trial(tmp_path, repo)
    counter = tmp_path / "count.txt"
    py = Path(sys.executable).as_posix()
    f.write_text(f.read_text().replace('uses = "bin/kg"', f'uses = "bin/kg"\nprepare = ["\\"{py}\\" -c \\"open(\'{counter.as_posix()}\',\'a\').write(\'x\')\\""]'))
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out)])
    assert counter.read_text() == "x"  # once, not once per run
    # break one run, then retry only it
    victim = next((out / "runs").rglob("run.json"))
    rec = json.loads(victim.read_text())
    rec["status"] = "error"
    victim.write_text(json.dumps(rec))
    main(["run", str(f), "--out", str(out), "--retry-failed"])
    assert json.loads(victim.read_text())["status"] == "ok"


def commit_repo(path: Path, name: str, text: str) -> Path:
    path.mkdir()
    (path / name).write_text(text)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=a@b", "-c", "user.name=x", "commit", "-qm", "i"], check=True)
    return path


def with_verify(f: Path, command: str) -> None:
    text = f.read_text()
    f.write_text(text.replace('expect = ["Alpha", "Beta"]\n[[questions]]', f"expect = [\"Alpha\", \"Beta\"]\nverify = '{command}'\n[[questions]]", 1))


def test_an_arm_can_clone_a_different_repo_for_the_same_question(tmp_path, repo):
    other = commit_repo(tmp_path / "other-repo", "b.txt", "other\n")
    f = write_trial(tmp_path, repo)
    f.write_text(f.read_text().replace('[[arms]]\nname = "tool"', f'[[repos]]\nname = "r2"\nsource = "{other.as_posix()}"\n\n[[arms]]\nname = "tool"\nrepos = {{ r = "r2" }}', 1))
    with_verify(f, "test -f b.txt")
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--question", "q1", "--reps", "1"])
    by = {r["arm"]: r for r in runner.load_runs(out)}
    assert by["tool"]["verified"] is True and by["plain"]["verified"] is False  # only the tool arm cloned the other repo


def test_each_run_gets_a_unique_trial_id(tmp_path, repo):
    checker = tmp_path / "check.py"
    checker.write_text("import os, sys\nsys.exit(0 if os.environ['TRIAL_ID'] == 'q1_plain_fake_1' else 1)\n")
    f = write_trial(tmp_path, repo)
    with_verify(f, f'{Path(sys.executable).as_posix()} {checker.as_posix()}')
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--question", "q1", "--arm", "plain", "--reps", "1"])
    assert runner.load_runs(out)[0]["verified"] is True


def test_config_rejects_a_repo_mapping_to_an_undefined_repo(tmp_path):
    f = tmp_path / "t.toml"
    f.write_text('[[repos]]\nname="r"\nsource="x"\n[[arms]]\nname="a"\nrepos = { r = "nope" }\n[[agents]]\nname="claude"\n[[questions]]\nid="q"\nrepo="r"\nprompt="p"\n')
    with pytest.raises(config.ConfigError, match="undefined repo"):
        config.load(f)


SAMPLES = HERE / "samples"


def sample(name):
    return (SAMPLES / name).read_text(encoding="utf-8")


def test_parsers_read_recorded_output_from_the_real_clis():
    """tests/samples holds trimmed output recorded from each real CLI (paths scrubbed), so format drift shows up here."""
    c = adapters.parse("claude", sample("claude.jsonl"))
    assert not c.error and c.answer and c.tokens["total"] > 1000 and c.tool_calls and c.tool_calls[0]["tool"] == "Bash"
    assert c.tokens["total"] == c.tokens["input"] + c.tokens["output"] + c.tokens["cache_read"] + c.tokens["cache_write"]

    x = adapters.parse("codex", sample("codex.jsonl"))
    assert not x.error and x.answer and x.tokens["total"] > 1000 and x.tool_calls and x.tool_calls[0]["tool"] == "shell"

    u = adapters.parse("cursor", sample("cursor.jsonl"))
    assert not u.error and u.answer and u.tokens["total"] > 1000 and u.tool_calls
    assert {t["tool"] for t in u.tool_calls} >= {"read"}

    g = adapters.parse("copilot", sample("copilot.jsonl"))
    assert not g.error and g.answer.startswith("Done") and g.tokens["total"] == g.tokens["input"] + g.tokens["output"] > 1000
    assert g.tool_calls == [{"tool": "bash", "text": "echo hi"}]


def test_copilot_without_a_usage_checkpoint_has_no_tokens_and_a_failed_exit_is_an_error():
    lines = [json.dumps({"type": "assistant.message", "data": {"content": "ok"}}), json.dumps({"type": "result", "exitCode": 1})]
    p = adapters.parse("copilot", "\n".join(lines))
    assert p.tokens is None and p.error == "exit code 1"


def test_arm_setups_do_not_overlap_unless_the_arm_opts_out(tmp_path, repo):
    log = tmp_path / "setup.log"
    script = tmp_path / "setup.py"
    script.write_text(f"import os, time\nlog = open(r'{log}', 'a')\nlog.write('start ' + os.environ['TRIAL_ID'] + '\\n'); log.flush()\ntime.sleep(0.3)\nlog.write('end ' + os.environ['TRIAL_ID'] + '\\n'); log.close()\n")
    py = Path(sys.executable).as_posix()

    def overlapped(extra):
        log.write_text("")
        f = write_trial(tmp_path, repo)
        f.write_text(f.read_text().replace('[[arms]]\nname = "plain"', f'[[arms]]\nname = "plain"\nsetup = ["{py} {script.as_posix()}"]{extra}', 1).replace("reps = 3", "reps = 4"))
        main(["run", str(f), "--out", str(tmp_path / ("o" + str(bool(extra)))), "--question", "q1", "--arm", "plain", "--agent", "fake"])
        lines = log.read_text().split()
        depth = 0
        for tag in lines[::2]:
            depth += 1 if tag == "start" else -1
            if depth > 1:
                return True
        return False

    assert overlapped("") is False
    assert overlapped("\nparallel_setup = true") is True
