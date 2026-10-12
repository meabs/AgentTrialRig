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


def test_a_relative_out_path_still_gives_scripts_an_absolute_trial_dir(tmp_path, repo, monkeypatch):
    checker = tmp_path / "check.py"
    checker.write_text("import os, sys\nsys.exit(0 if os.path.isabs(os.environ['TRIAL_DIR']) and os.path.isabs(os.environ['TRIAL_OUT']) else 1)\n")
    f = write_trial(tmp_path, repo)
    with_verify(f, f"{Path(sys.executable).as_posix()} {checker.as_posix()}")
    monkeypatch.chdir(tmp_path)
    main(["run", str(f), "--out", "rel-out", "--question", "q1", "--arm", "plain", "--reps", "1"])
    assert runner.load_runs(tmp_path / "rel-out")[0]["verified"] is True


def test_claim_parsing_and_comparison():
    from trialrig import claims
    good = 'Done.\nVERIFICATION: {"ran": ["pytest -q"], "result": "passed", "could_not_verify": ["frontend"], "missing": ["a test command"]}'
    c = claims.extract(good)
    assert c["result"] == "passed" and c["missing"] == ["a test command"] and c["ran"] == ["pytest -q"]
    assert claims.extract("VERIFICATION: not json") is None and claims.extract("VERIFICATION: {\"result\": \"maybe\"}") is None
    assert claims.compare(c, False) == "overclaim" and claims.compare(c, True) == "agree"
    assert claims.compare({"result": "not-run", "ran": [], "could_not_verify": [], "missing": []}, True) == "underclaim"
    assert claims.compare(None, True) == "none" and claims.compare(c, None) is None


def test_trial_with_claims_reports_overclaims_and_missing_notes(tmp_path, repo):
    f = write_trial(tmp_path, repo)
    text = f.read_text().replace("timeout = 60", "timeout = 60\nclaim = true")
    claim_json = '{"ran": ["pytest"], "result": "passed", "could_not_verify": [], "missing": ["no documented test command"]}'
    text = text.replace("ANSWER: the classes are Alpha and Beta TOKENS: 1000", "ANSWER: the classes are Alpha and Beta TOKENS: 1000 CLAIM: " + claim_json.replace('"', '\\"'))
    f.write_text(text)
    with_verify(f, "test -f does-not-exist")  # the independent check fails, so a "passed" claim is an overclaim
    out = tmp_path / "out"
    main(["run", str(f), "--out", str(out), "--question", "q1", "--reps", "2"])
    runs = runner.load_runs(out)
    assert {r["claim_match"] for r in runs} == {"overclaim"}
    d = report.build(runs, "plain", ["plain", "tool"])
    assert d["agents"]["fake"]["arms"][0]["claims"]["overclaim"] == 2
    assert d["notes"]["missing"][0]["text"] == "no documented test command" and d["notes"]["missing"][0]["runs"] == 4
    md = report.markdown(d, "t")
    assert "2 overclaim" in md and "What agents said was missing" in md


def test_stop_when_decided_stops_once_the_difference_is_clear(tmp_path, repo, capsys):
    f = write_trial(tmp_path, repo)
    out = tmp_path / "out"
    assert main(["run", str(f), "--out", str(out), "--reps", "6", "--stop-when-decided", "--min-runs", "2"]) == 0
    assert len(runner.load_runs(out)) == 2 * 2 * 2  # both questions were clear after two runs per arm, so reps 3 to 6 never ran
    assert "[stop] fake/q1 after 2 runs per arm: clear" in capsys.readouterr().out


def test_plan_from_a_pilot_names_clear_open_and_hopeless_cells(capsys):
    from trialrig import plan

    def r(arm, tok, q="q"):
        return {"agent": "a", "question": q, "arm": arm, "status": "ok", "tokens": {"total": tok}}

    clear = [r("base", 1000 + i) for i in range(4)] + [r("new", 500 + i) for i in range(4)]
    assert plan.decide(clear, "base")[0]["state"] == "clear"
    noisy = [r("base", t, "n") for t in (400, 1600, 900, 2500, 300, 1800)] + [r("new", t, "n") for t in (500, 1500, 800, 2400, 350, 1700)]
    row = plan.decide(noisy, "base", max_reps=6)[0]
    assert row["state"] == "futile" and row["needed"] > 6
    few = plan.decide([r("base", 1000), r("new", 900)], "base")[0]
    assert few["state"] == "continue"
    text = plan.table(plan.decide(clear + noisy, "base", max_reps=6), 6, 3.0)
    assert "| a | q | 4 |" in text and "Projected further spend" in text and "$" in text


def test_snapshot_and_compare_flag_a_regression_and_ignore_noise(tmp_path, repo, capsys):
    from trialrig import regress

    def runs(pass_k, n, tokens, arm="a"):
        return [{"agent": "x", "question": "q", "arm": arm, "status": "ok", "verified": i < pass_k, "tokens": {"total": t}} for i, t in zip(range(n), tokens)]

    old = regress.snapshot(runs(8, 8, [1000, 1100, 900, 1050, 950, 1000, 1020, 980]), "v1", None, {"x": {"cli": "1.0", "model": "m"}})
    same = regress.compare(old, runs(8, 8, [1010, 1090, 910, 1040, 960, 1000, 1030, 970]))
    assert [r["verdict"] for r in same["rows"]] == ["same"]
    worse = regress.compare(old, runs(3, 8, [1000, 1100, 900, 1050, 950, 1000, 1020, 980]))
    assert worse["rows"][0]["verdict"] == "regressed" and regress.failed(worse, {"regressed"})
    pricey = regress.compare(old, runs(8, 8, [2000, 2100, 1900, 2050, 1950, 2000, 2020, 1980]))
    assert pricey["rows"][0]["verdict"] == "dearer" and not regress.failed(pricey, {"regressed"})
    few = regress.compare(old, runs(8, 3, [3000, 3100, 2900]))
    assert few["rows"][0]["verdict"] == "same" and "no token verdict" in few["rows"][0]["detail"]
    assert regress.version_changes(old, {"x": {"cli": "2.0", "model": "m"}}) == ["x cli: 1.0 -> 2.0"]
    assert "| x | q | a | 8/8 to 3/8 | regressed |" in regress.markdown(worse)
    assert regress.fisher_worse(8, 8, 8, 8) == 1.0 and regress.fisher_worse(8, 8, 0, 8) < 0.001


def test_compare_command_exit_code_and_arm_mapping(tmp_path, repo, capsys):
    f = write_trial(tmp_path, repo)
    out = tmp_path / "out"
    assert main(["run", str(f), "--out", str(out)]) == 0
    snap = tmp_path / "base.json"
    assert main(["snapshot", str(out), "--to", str(snap)]) == 0
    assert main(["compare", str(out), "--snapshot", str(snap)]) == 0
    assert main(["compare", str(out), "--snapshot", str(snap), "--old-arm", "plain", "--new-arm", "tool", "--min-runs", "3", "--fail-on", "improved"]) == 1  # the tool arm uses half the tokens
    shown = capsys.readouterr().out
    assert "Regression check against snapshot" in shown and "improved" in shown


def test_regress_runs_a_trial_and_compares_it_with_the_snapshot(tmp_path, repo, capsys):
    f = write_trial(tmp_path, repo)
    first = tmp_path / "first"
    main(["run", str(f), "--out", str(first)])
    snap = tmp_path / "base.json"
    main(["snapshot", str(first), "--to", str(snap)])
    assert main(["regress", str(f), "--out", str(tmp_path / "second"), "--snapshot", str(snap)]) == 0
    assert "Regression check" in capsys.readouterr().out


def test_a_skewed_baseline_does_not_make_an_identical_rerun_look_improved():
    from trialrig import regress

    def runs(tokens):
        return [{"agent": "x", "question": "q", "arm": "a", "status": "ok", "verified": True, "tokens": {"total": t}} for t in tokens]

    old = regress.snapshot(runs([72619, 32918, 49786, 32952]), "v1")  # a live baseline: two cheap runs, two dear ones
    row = regress.compare(old, runs([32673, 32867, 32995, 32869]))["rows"][0]
    assert row["verdict"] == "same" and "not called" in row["detail"]
    assert regress.compare(old, runs([120666, 140881, 145391, 156895]))["rows"][0]["verdict"] == "dearer"


def test_timeouts_count_as_failures_in_a_scored_cell():
    from trialrig import regress

    ok = [{"agent": "x", "question": "q", "arm": "a", "status": "ok", "correct": True, "tokens": {"total": 1000 + i}} for i in range(4)]
    slow = [{"agent": "x", "question": "q", "arm": "a", "status": "timeout"} for _ in range(4)]
    row = regress.compare(regress.snapshot(ok, "v1"), slow)["rows"][0]
    assert row["verdict"] == "regressed" and row["old_pass"] == "4/4" and row["new_pass"] == "0/4"


@pytest.mark.skipif(sys.platform != "darwin", reason="isolate uses macOS sandbox-exec")
def test_isolate_blocks_reads_outside_the_work_folder(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "s.txt").write_text("SECRET")
    out = tmp_path / "out"
    work = out / "work" / "q" / "arm" / "a" / "0"
    work.mkdir(parents=True)
    (work / "ok.txt").write_text("fine")
    rdir = out / "runs"
    rdir.mkdir()
    cmd = runner._isolated(["sh", "-c", f"cat ok.txt; cat {secret}/s.txt"], [str(tmp_path)], work, out, rdir)
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
    assert "fine" in r.stdout and "SECRET" not in r.stdout and "not permitted" in r.stderr
