import json
import subprocess
from pathlib import Path

import pytest

from bench.claude import build_argv
from bench.run import (checkout, first_call, fingerprint, hide_file, isolation_check, load_arm, measure,
                       reference_size, run_loop)
from bench.types import Blocked
from bench.store import TaskRow


@pytest.fixture
def mirrors(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(src), *a], check=True, capture_output=True, text=True).stdout.strip()
    g("init", "-q", "-b", "main")
    (src / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (src / "CLAUDE.md").write_text("## Lanes\nthe repo's own\n")
    g("add", "-A")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    base = g("rev-parse", "HEAD")
    m = tmp_path / "mirrors"
    m.mkdir()
    subprocess.run(["git", "clone", "--quiet", "--mirror", str(src), str(m / "tiny.git")], check=True)
    return m, base


def task(base):
    return TaskRow("tiny", "tiny", 0, None, base, base, "Fix add.", ["tests/test_calc.py"], "whole")


def fake(replies, edit=None):
    """replies: list of dicts returned in order (the last repeats); edit(cwd, n) runs before each reply."""
    calls = []

    def runner(argv, cwd=None, env=None, **kw):
        calls.append(argv)
        if edit:
            edit(Path(cwd), len(calls))
        reply = replies[min(len(calls), len(replies)) - 1]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(reply), stderr="")

    runner.calls = calls
    return runner


def test_arm_argv(tmp_path):
    t = task("b")
    argv, env = build_argv(first_call(t, load_arm("V"), tmp_path))
    assert "--disable-slash-commands" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert "CLAUDE_CONFIG_DIR" in env and "--bare" not in argv
    assert argv[-1].startswith("Fix add.\n\nWork in this checkout only.")
    argv, env = build_argv(first_call(t, load_arm("Aprime"), tmp_path))
    assert "CLAUDE_CONFIG_DIR" not in env
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[-1].startswith("Lane: Inline.\n\nFix add.")


def test_checkout_layout_and_pushes_disabled(mirrors, tmp_path):
    m, base = mirrors
    wt = checkout(task(base), "run1", mirrors=m, worktrees=tmp_path / "wt")
    g = lambda *a: subprocess.run(["git", "-C", str(wt), *a], capture_output=True, text=True).stdout.strip()
    assert g("rev-parse", "HEAD") == base
    assert g("branch", "--show-current") == "bench/run1"
    assert g("config", "remote.origin.pushurl") == "DISABLED"
    bare = tmp_path / "elsewhere.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    push = subprocess.run(["git", "-C", str(wt), "push", f"file://{bare}", "HEAD:refs/heads/x"], capture_output=True, text=True)
    assert push.returncode != 0 and "pushes are disabled" in push.stderr
    hide_file(wt, "CLAUDE.md")
    (wt / "CLAUDE.md").write_text("# bench-sentinel run1\n")
    g("add", "-A")
    assert g("diff", "--cached", base) == ""


def test_stall_guard_trips_at_three(mirrors, tmp_path):
    m, base = mirrors
    wt = checkout(task(base), "run2", mirrors=m, worktrees=tmp_path / "wt")
    (tmp_path / "res").mkdir()
    runner = fake([{"result": "Should I proceed?", "session_id": "s"}])
    outcome, rounds, results = run_loop(first_call(task(base), load_arm("V"), wt), wt, tmp_path / "res", runner)
    assert (outcome, rounds) == ("stalled", 3)
    assert len(results) == 4 and (tmp_path / "res" / "turn-4.json").exists()
    assert runner.calls[1][runner.calls[1].index("--resume") + 1] == "s"
    assert runner.calls[1][-1].startswith("Go with your recommendation.")


def test_editing_between_resumes_changes_fingerprint(mirrors, tmp_path):
    m, base = mirrors
    wt = checkout(task(base), "run3", mirrors=m, worktrees=tmp_path / "wt")
    before = fingerprint(wt)
    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    assert fingerprint(wt) != before
    g = lambda *a: subprocess.run(["git", "-C", str(wt), *a], capture_output=True, text=True)
    assert g("diff", "--cached", "--quiet").returncode == 0  # the arm's own index is untouched
    assert "calc.py" in g("diff", "--stat").stdout
    (tmp_path / "res").mkdir()
    edits = lambda cwd, n: (cwd / "calc.py").write_text(f"# {n}\n")
    outcome, rounds, _ = run_loop(first_call(task(base), load_arm("V"), wt), wt, tmp_path / "res",
                                  fake([{"result": "still going", "session_id": "s"}] * 5 + [{"result": "ok DONE"}], edits))
    assert (outcome, rounds) == ("done", 5)


def test_error_maps_to_failed(mirrors, tmp_path):
    m, base = mirrors
    wt = checkout(task(base), "run4", mirrors=m, worktrees=tmp_path / "wt")
    (tmp_path / "res").mkdir()
    assert run_loop(first_call(task(base), load_arm("V"), wt), wt, tmp_path / "res",
                    fake([{"is_error": True, "result": "boom"}]))[0] == "failed"
    assert run_loop(first_call(task(base), load_arm("V"), wt), wt, tmp_path / "res",
                    fake([{"result": "You've hit your usage limit"}]))[0] == "failed"


def test_isolation_check_catches_the_global_claude_md(tmp_path):
    leaked = fake([{"result": "Lane: Inline.\n# Global preferences\n# bench-sentinel r1\nEND"}])
    with pytest.raises(Blocked, match="isolation check failed"):
        isolation_check(tmp_path, "r1", None, leaked, markers=["## Lanes", "# Global preferences"])
    clean = fake([{"result": "# bench-sentinel r1\nEND"}])
    assert isolation_check(tmp_path, "r1", None, clean, markers=["## Lanes", "# Global preferences"])
    with pytest.raises(Blocked):
        isolation_check(tmp_path, "r1", None, fake([{"result": "END"}]), markers=[])


def test_done_with_punctuation_and_usage_limit_mention(mirrors, tmp_path):
    m, base = mirrors
    wt = checkout(task(base), "run5", mirrors=m, worktrees=tmp_path / "wt")
    (tmp_path / "res").mkdir()
    call = first_call(task(base), load_arm("V"), wt)
    assert run_loop(call, wt, tmp_path / "res", fake([{"result": "Fixed; no usage limit hit. **DONE**."}]))[0] == "done"
    assert run_loop(call, wt, tmp_path / "res", fake([{"result": "You have hit your usage limit"}]))[0] == "failed"


def _line(**kw):
    return json.dumps(kw)


def _session_file(d: Path, sid: str, prompt: str, tool: dict, usage_out: int):
    t = "2026-09-24T10:00:0{}Z"
    lines = [
        _line(type="user", uuid=f"{sid}-u", timestamp=t.format(0), sessionId=sid, cwd="/w",
              message={"role": "user", "content": prompt}),
        _line(type="assistant", uuid=f"{sid}-a", timestamp=t.format(1), sessionId=sid, cwd="/w", requestId="r",
              message={"id": f"{sid}-m", "model": "claude-opus-5-5", "content": [dict(type="tool_use", id=f"{sid}-t", **tool)],
                       "usage": {"input_tokens": 1, "output_tokens": usage_out}}),
        _line(type="user", uuid=f"{sid}-s", timestamp=t.format(2), sessionId=sid, cwd="/w",
              message={"role": "user", "content": "Go with your recommendation."}),
    ]
    (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")


def test_measure_reads_only_the_run_sessions_as_execution(tmp_path):
    from bench.config import transcript_dir_for
    wt = tmp_path / "wt"
    wt.mkdir()
    d = transcript_dir_for(wt, root=tmp_path / "cfg" / "projects")
    d.mkdir(parents=True)
    _session_file(d, "init", "/init", {"name": "Write", "input": {"file_path": "/w/CLAUDE.md"}}, 1000)
    _session_file(d, "iso", "Print the first line", {"name": "Read", "input": {}}, 1000)
    _session_file(d, "task", "Fix add.", {"name": "Bash", "input": {"command": "sed -i s/-/+/ calc.py"}}, 7)
    phases, _ = measure(wt, load_arm("V"), rounds=1, session_ids=["task"], config_dir=tmp_path / "cfg")
    assert [p.name for p in phases] == ["execution"]
    assert phases[0].session_ids == ["task"]
    assert phases[0].usage_by_model["claude-opus-5-5"].output == 7
    assert phases[0].human_turns == 1 and phases[0].attention_s == 0


def test_slice_reference_size_uses_its_own_diff(mirrors, tmp_path):
    m, base = mirrors
    wt = checkout(task(base), "run6", mirrors=m, worktrees=tmp_path / "wt")
    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    subprocess.run(["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x"], check=True)
    ref = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    sl = TaskRow("tiny-s1", "tiny", 0, 1, base, ref, "x", [], "slice")
    adds, dels, files, _, _ = reference_size(sl, wt)
    assert (adds, dels, files) == (4, 0, 1)  # git aligns the old return line with sub()'s


def test_cached_init_with_harness_text_is_refused(tmp_path, monkeypatch):
    import bench.run as run
    monkeypatch.setattr(run, "forbidden_markers", lambda: ["## Lanes", "# Global preferences"])
    cache = tmp_path / "tiny" / "init-claude.md"
    cache.parent.mkdir()
    cache.write_text("<!-- header -->\n# CLAUDE.md\n## Lanes\nleaked\n")
    with pytest.raises(Blocked, match="init CLAUDE.md for tiny carries harness text"):
        run.init_claude_md(task("b"), tmp_path, None, fake([{}]), cache_dir=tmp_path)
    cache.write_text("<!-- header -->\n# CLAUDE.md\nclean\n")
    assert run.init_claude_md(task("b"), tmp_path, None, fake([{}]), cache_dir=tmp_path) == "# CLAUDE.md\nclean\n"
