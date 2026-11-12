"""The one way this package calls `claude -p`. Never `--bare` (spec §6)."""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bench.config import CONFIG

EMPTY_CONFIG_DIR = CONFIG / "empty-claude"


class ClaudeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaudeCall:
    prompt: str
    model: str | None = None
    cwd: Path | None = None
    config_dir: Path | None = None  # None = Kevin's real ~/.claude
    resume: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    setting_sources: str | None = None
    disable_skills: bool = False
    strict_mcp: bool = False
    extra_args: tuple[str, ...] = ()


ISOLATED = dict(config_dir=EMPTY_CONFIG_DIR, setting_sources="project", disable_skills=True, strict_mcp=True)


def build_argv(call: ClaudeCall) -> tuple[list[str], dict[str, str]]:
    argv = ["claude", "-p", "--output-format", "json"]
    if call.model:
        argv += ["--model", call.model]
    if call.resume:
        argv += ["--resume", call.resume]
    if call.effort:
        argv += ["--effort", call.effort]
    if call.permission_mode:
        argv += ["--permission-mode", call.permission_mode]
    if call.setting_sources:
        argv += ["--setting-sources", call.setting_sources]
    if call.disable_skills:
        argv.append("--disable-slash-commands")
    if call.strict_mcp:
        argv.append("--strict-mcp-config")
    argv += list(call.extra_args)
    argv.append(call.prompt)
    env = {"CLAUDE_CONFIG_DIR": str(Path(call.config_dir).resolve())} if call.config_dir else {}
    return argv, env


def child_env(overrides: dict[str, str]) -> dict[str, str]:
    """The environment minus whatever an invoking Claude Code session exported."""
    base = {
        k: v for k, v in os.environ.items()
        if not (k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_") or k == "CLAUDE_EFFORT")
    }
    return {**base, **overrides}


def run_claude_json(call: ClaudeCall, runner=subprocess.run) -> dict:
    argv, env = build_argv(call)
    proc = runner(
        argv, cwd=call.cwd, env=child_env(env), capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        raise ClaudeError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout or '')[:500]}")


def parse_json_answer(text: str):
    """The JSON object or list in a model's reply, tolerating a fenced block or prose around it."""
    text = text.strip()
    if "```" in text:
        inner = text.split("```")[1]
        text = inner.removeprefix("json").strip()
    pairs = sorted((("{", "}"), ("[", "]")), key=lambda p: (text.find(p[0]) == -1, text.find(p[0])))
    for opener, closer in pairs:  # the outermost value opens first
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON in reply: {text[:200]}")
