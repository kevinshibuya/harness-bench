import json
import subprocess

import pytest

from bench.claude import ISOLATED, ClaudeCall, ClaudeError, build_argv, run_claude_json
from bench.config import CONFIG


def call(**kw):
    base = dict(prompt="hi", model=None, cwd=None, config_dir=None, resume=None, effort=None,
                permission_mode=None, setting_sources=None, disable_skills=False, strict_mcp=False, extra_args=())
    base.update(kw)
    return ClaudeCall(**base)


def test_isolated_call_flags():
    argv, env = build_argv(call(model="opus", effort="high", **ISOLATED))
    assert argv[:2] == ["claude", "-p"]
    assert "--disable-slash-commands" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "high"
    assert env["CLAUDE_CONFIG_DIR"] == str((CONFIG / "empty-claude").resolve())
    assert "--bare" not in argv
    assert argv[-1] == "hi"


def test_real_config_has_no_override_and_resume():
    argv, env = build_argv(call(resume="sess-1", permission_mode="bypassPermissions"))
    assert "CLAUDE_CONFIG_DIR" not in env
    assert argv[argv.index("--resume") + 1] == "sess-1"
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--disable-slash-commands" not in argv


def test_run_parses_json_and_raises_on_garbage():
    def ok(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": "ok", "session_id": "s"}), stderr="")
    assert run_claude_json(call(), runner=ok)["result"] == "ok"

    def bad(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="not json", stderr="boom")
    with pytest.raises(ClaudeError, match="boom"):
        run_claude_json(call(), runner=bad)


def test_single_finding_list_is_a_list():
    from bench.claude import parse_json_answer
    assert parse_json_answer('[{"title": "bug"}]') == [{"title": "bug"}]
    assert parse_json_answer('Here: ```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json_answer('{"pr": 12, "why": "[x]"}') == {"pr": 12, "why": "[x]"}


def test_child_env_drops_the_parent_session_vars(monkeypatch):
    for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_EFFORT"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("HOME_KEEP", "y")
    seen = {}

    def runner(argv, env=None, **kw):
        seen.update(env)
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    run_claude_json(call(**ISOLATED), runner=runner)
    assert not any(k.startswith("CLAUDECODE") or k.startswith("CLAUDE_CODE_") or k == "CLAUDE_EFFORT" for k in seen)
    assert seen["HOME_KEEP"] == "y" and seen["CLAUDE_CONFIG_DIR"]
