"""Replay a task under an arm (spec §6, §7). Spends Claude usage; manual only."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from bench.attribute import Attribution
from bench.claude import EMPTY_CONFIG_DIR, ISOLATED, ClaudeCall, ClaudeError, build_argv, run_claude_json
from bench.codex import codex_hook, load_registry
from bench.config import CONFIG, MIRRORS, PROMPTS, RUNS, TASKS, WORKTREES, RepoConfig, load_repos, transcript_dir_for
from bench.extract import EXTRACTOR_VERSION, harness_version_at, models_in, phase_costs
from bench.mirrors import ensure_mirror
from bench.phases import Phase, PhaseOverride, segment
from bench.price import load_prices
from bench.prs import load_prs
from bench.store import RecordRow, RunRow, Store, TaskRow, stratum_for
from bench.transcripts import read_session
from bench.types import Arm, Blocked

STALL_LIMIT = 3
USAGE_LIMIT = re.compile(r"(?i)usage limit")
DONE_RE = re.compile(r"\bDONE\b[\s*.`_)!]*$")
HOOKS_DIR = CONFIG.parent / "hooks"
LANES_HEADING = "## Lanes"
ISOLATION_PROMPT = "Print the first line of every CLAUDE.md you were given, then the word END."
INIT_FALLBACK_PROMPT = "Write the CLAUDE.md file /init would write for this repository, and nothing else."
REAL_CONFIG_DIR = Path.home() / ".claude"


@dataclass(frozen=True)
class ArmConfig:
    name: Arm
    model: str | None
    isolated: bool
    prompt_prefix: str
    effort: str
    permission_mode: str


def load_arm(name: str, path: Path = CONFIG / "arms") -> ArmConfig:
    d = tomllib.loads((path / f"{name}.toml").read_text())
    return ArmConfig(d["name"], d.get("model"), d["isolated"], d["prompt_prefix"], d["effort"], d["permission_mode"])


def git(worktree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(worktree), *args], check=True, capture_output=True, text=True).stdout.strip()


def checkout(task: TaskRow, run_id: str, mirrors: Path = MIRRORS, worktrees: Path = WORKTREES) -> Path:
    """A --shared clone of the mirror at the base commit, on branch bench/<run_id>, with pushes disabled."""
    worktree = worktrees / run_id
    worktrees.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", str(mirrors / f"{task.repo}.git"),
                    str(worktree)], check=True, capture_output=True)
    git(worktree, "config", "remote.origin.pushurl", "DISABLED")
    git(worktree, "config", "core.hooksPath", str(HOOKS_DIR))  # pre-push refuses any remote
    git(worktree, "checkout", "--quiet", "-b", f"bench/{run_id}", task.base_commit)
    return worktree


def fingerprint(worktree: Path) -> str:
    """HEAD plus the tree of the whole checkout, built in a private index so the arm's own is untouched."""
    head = git(worktree, "rev-parse", "HEAD")
    env = {**os.environ, "GIT_INDEX_FILE": str(worktree / ".git" / "bench-index")}
    run = lambda *a: subprocess.run(["git", "-C", str(worktree), *a], check=True, capture_output=True,
                                    text=True, env=env).stdout.strip()
    run("read-tree", "HEAD")
    run("add", "-A")
    return f"{head}:{run('write-tree')}"


def hide_file(worktree: Path, name: str) -> None:
    """Keep a file we write out of the diff: skip-worktree if tracked, info/exclude otherwise."""
    tracked = subprocess.run(["git", "-C", str(worktree), "ls-files", "--error-unmatch", name],
                             capture_output=True).returncode == 0
    if tracked:
        git(worktree, "update-index", "--skip-worktree", name)
    else:
        exclude = worktree / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as f:
            f.write(f"\n{name}\n")


def init_claude_md(task: TaskRow, worktree: Path, model: str | None, runner, cache_dir: Path = TASKS) -> str:
    """The cached /init CLAUDE.md for the repo, generated in this checkout on first use."""
    cache = cache_dir / task.repo / "init-claude.md"
    if cache.exists():
        content = cache.read_text().split("\n", 1)[1]
        leaked = [m for m in forbidden_markers() if m in content]
        if leaked:
            raise Blocked(f"init CLAUDE.md for {task.repo} carries harness text {leaked}: delete {cache} to regenerate")
        return content
    target = worktree / "CLAUDE.md"
    hide_file(worktree, "CLAUDE.md")
    target.unlink(missing_ok=True)  # /init would otherwise rework the repo's own CLAUDE.md
    base = dict(model=model, cwd=worktree, permission_mode="bypassPermissions", **{**ISOLATED, "disable_skills": False})
    run_claude_json(ClaudeCall(prompt="/init", **base), runner=runner)
    via = "/init"
    if target.exists() and target.read_text().strip():
        content = target.read_text()
    else:
        result = run_claude_json(ClaudeCall(prompt=INIT_FALLBACK_PROMPT, **base), runner=runner)
        content, via = result.get("result", ""), "prompt"
    if not content.strip():
        raise Blocked(f"init CLAUDE.md for {task.repo} came back empty")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(f"<!-- bench init CLAUDE.md: repo={task.repo} base={task.base_commit} via={via} -->\n{content}")
    return content


def forbidden_markers(global_claude_md: Path = REAL_CONFIG_DIR / "CLAUDE.md") -> list[str]:
    """Text that proves Kevin's global CLAUDE.md reached the model."""
    markers = [LANES_HEADING]
    if global_claude_md.exists():
        first = next((line.strip() for line in global_claude_md.read_text().splitlines() if line.strip()), "")
        if first:
            markers.append(first)
    return markers


def isolation_check(worktree: Path, run_id: str, model: str | None, runner, markers: list[str] | None = None) -> dict:
    result = run_claude_json(ClaudeCall(prompt=ISOLATION_PROMPT, model=model, cwd=worktree, **ISOLATED), runner=runner)
    text = result.get("result", "")
    leaked = [m for m in (forbidden_markers() if markers is None else markers) if m in text]
    if f"bench-sentinel {run_id}" not in text or leaked:
        raise Blocked(f"isolation check failed: {text[:300]}")
    return result


def first_call(task: TaskRow, arm: ArmConfig, worktree: Path) -> ClaudeCall:
    prompt = arm.prompt_prefix + task.raw_ask + "\n\n" + (PROMPTS / "suffix.txt").read_text()
    iso = ISOLATED if arm.isolated else {}
    return ClaudeCall(prompt=prompt, model=arm.model, cwd=worktree, effort=arm.effort,
                      permission_mode=arm.permission_mode, **iso)


def run_loop(call: ClaudeCall, worktree: Path, result_dir: Path, runner) -> tuple[str, int, list[dict]]:
    """Resume with the stand-in until DONE. Returns (outcome, stand-in rounds, results)."""
    standin = (PROMPTS / "standin.txt").read_text()
    results: list[dict] = []
    rounds = unchanged = 0
    before = fingerprint(worktree)
    while True:
        try:
            result = run_claude_json(call, runner=runner)
        except ClaudeError as e:
            result = {"is_error": True, "result": str(e)}
        results.append(result)
        (result_dir / f"turn-{len(results)}.json").write_text(json.dumps(result, indent=1) + "\n")
        text = str(result.get("result") or "")
        if result.get("is_error"):
            return "failed", rounds, results
        if DONE_RE.search(text):
            return "done", rounds, results
        if USAGE_LIMIT.search(text):
            return "failed", rounds, results
        after = fingerprint(worktree)
        if rounds > 0:
            unchanged = unchanged + 1 if after == before else 0
            if unchanged >= STALL_LIMIT:
                return "stalled", rounds, results
        before = after
        rounds += 1
        call = replace(call, prompt=standin, resume=result.get("session_id") or call.resume)


def test_gate(task: TaskRow, repo: RepoConfig, worktree: Path) -> str:
    if task.reference_tests:
        git(worktree, "checkout", task.reference_commit, "--", *task.reference_tests)
    for cmd in (repo.setup_cmd, repo.test_cmd):
        if cmd and subprocess.run(cmd, shell=True, cwd=worktree, capture_output=True).returncode != 0:
            return "fail"
    return "pass"


def measure(worktree: Path, arm: ArmConfig, rounds: int, session_ids: list[str],
            config_dir: Path | None = None) -> tuple[list[Phase], list]:
    """Segment the run's own sessions like history. The /init and isolation-check sessions share the
    transcript dir and are not the run. A run has no pre-work: it opens in execution."""
    config_dir = config_dir or (EMPTY_CONFIG_DIR if arm.isolated else REAL_CONFIG_DIR)
    tdir = transcript_dir_for(worktree, root=config_dir / "projects")
    sessions = []
    for sid in dict.fromkeys(session_ids):
        path = tdir / f"{sid}.jsonl"
        s = read_session(path, worktree.name) if path.exists() else None
        if s and s.events:
            sessions.append((s, Attribution(s.session_id, None, "none", s.events[0].uuid, s.events[-1].uuid)))
    overrides = [PhaseOverride(s.session_id, s.events[0].uuid, "execution") for s, _ in sessions]
    phases = segment(sessions, arm.name, overrides)
    first_prompt = min((e.ts for s, _ in sessions for e in s.events if e.kind == "human"), default=None)
    for p in phases:  # runs: human turns are the stand-in rounds, attention is zero
        p.human_turns = sum(1 for s, _ in sessions for e in s.events
                            if e.kind == "human" and p.contains(e.ts, s.session_id) and e.ts != first_prompt)
        p.attention_s = 0.0
    codex_hook(load_registry(), priced=load_prices().knows)(phases, sessions)
    return phases, sessions


def reference_size(task: TaskRow, worktree: Path) -> tuple[int, int, int, str | None, str]:
    """additions, deletions, files, title, head branch: from the reference PR when there is one."""
    repo = load_repos().get(task.repo)
    pr = next((p for p in load_prs(repo) if p.number == task.pr), None) if repo else None
    if pr is not None and task.kind == "whole":
        return pr.additions, pr.deletions, len(pr.changed_files), pr.title, pr.head_branch
    title, branch = (pr.title, pr.head_branch) if pr is not None else (None, "")
    adds = dels = files = 0
    for line in git(worktree, "diff", "--numstat", task.base_commit, task.reference_commit).splitlines():
        a, d, _ = line.split("\t", 2)
        adds += int(a) if a.isdigit() else 0
        dels += int(d) if d.isdigit() else 0
        files += 1
    return adds, dels, files, title, branch


def run_task(task: TaskRow, arm: ArmConfig, store: Store, dry_run: bool, runner=subprocess.run,
             state: dict | None = None) -> RunRow | None:
    """state carries 'isolation_checked' across tasks in one invocation."""
    state = state if state is not None else {}
    run_id = f"{task.task_id}-{arm.name}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    if dry_run:
        call = first_call(task, arm, WORKTREES / run_id)
        argv, env = build_argv(call)
        print(json.dumps({"argv": argv, "env": env, "prompt": call.prompt, "worktree": str(WORKTREES / run_id)},
                         indent=1))
        return None
    repo = load_repos()[task.repo]
    ensure_mirror(repo)
    worktree = checkout(task, run_id)
    result_dir = RUNS / run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    if arm.isolated:
        content = init_claude_md(task, worktree, arm.model, runner)
        hide_file(worktree, "CLAUDE.md")
        (worktree / "CLAUDE.md").write_text(f"# bench-sentinel {run_id}\n\n{content}")
        if not state.get("isolation_checked"):
            result = isolation_check(worktree, run_id, arm.model, runner)
            (result_dir / "isolation.json").write_text(json.dumps(result, indent=1) + "\n")
            state["isolation_checked"] = True
    started = datetime.now(timezone.utc)
    outcome, rounds, results = run_loop(first_call(task, arm, worktree), worktree, result_dir, runner)
    ended = datetime.now(timezone.utc)
    git(worktree, "add", "-A")
    diff = git(worktree, "diff", "--cached", task.base_commit)
    (result_dir / "diff.patch").write_text(diff + "\n" if diff else "")
    gate = test_gate(task, repo, worktree)
    phases, _ = measure(worktree, arm, rounds, [r["session_id"] for r in results if r.get("session_id")])
    costs = phase_costs(phases, load_prices())
    adds, dels, files, title, branch = reference_size(task, worktree)
    record_id = store.upsert_record(RecordRow(
        repo=task.repo, pr=task.pr, arm=arm.name, source="run", run_id=run_id, title=title or task.task_id,
        head_branch=branch or f"bench/{run_id}", base_commit=task.base_commit, reference_commit=task.reference_commit,
        merged_at=None, additions=adds, deletions=dels, files_changed=files, stratum=stratum_for(adds + dels),
        kind=task.kind, harness_version=harness_version_at(started) if arm.name == "Aprime" else None,
        models=models_in(phases), raw_ask=task.raw_ask, outcome=outcome, test_gate=gate,
        extractor_version=EXTRACTOR_VERSION,
    ))
    store.replace_phases(record_id, phases, costs)
    cost = sum(costs.values()) or sum(float(r.get("total_cost_usd") or 0) for r in results)
    row = RunRow(
        run_id=run_id, record_id=record_id, arm=arm.name, task_id=task.task_id, started=started, ended=ended,
        standin_rounds=rounds, cost_usd=cost, wall_s=(ended - started).total_seconds(),
        num_turns=sum(int(r.get("num_turns") or 0) for r in results), outcome=outcome,
        diff_path=str((result_dir / "diff.patch").relative_to(RUNS.parent)),
        result_dir=str(result_dir.relative_to(RUNS.parent)),
    )
    store.add_run(row)
    return row


def remove_worktree(run_id: str) -> None:
    shutil.rmtree(WORKTREES / run_id, ignore_errors=True)
