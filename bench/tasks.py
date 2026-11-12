"""Replayable tasks from merged PRs: whole PRs and plan-task slices (spec §9)."""

import json
import re
from pathlib import Path

from bench.config import TASKS, RepoConfig
from bench.mirrors import mirror_path, run_git
from bench.prs import PR
from bench.store import RecordRow, TaskRow
from bench.types import Blocked

TEST_FILE = re.compile(r"(\.test\.|\.spec\.|(^|/)tests/|(^|/)__tests__/)")
HEADING = re.compile(r"(?m)^### Task (\d+):\s*(.+?)\s*$")
TYPE_PREFIX = re.compile(r"^\w+(\([^)]*\))?!?:\s*")


class NeedsGoal(Exception):
    """A slice needs Kevin's goal sentence; carries what he needs to write it."""

    def __init__(self, heading: str, work: str):
        super().__init__(heading)
        self.heading, self.work = heading, work


def parse_plan(text: str) -> list[tuple[int, str, str]]:
    """(K, heading text, Work paragraph) per `### Task K:` heading."""
    out = []
    matches = list(HEADING.finditer(text))
    for i, m in enumerate(matches):
        body = text[m.end() : matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        work = re.search(r"\*\*Work:\*\*\s*(.+?)(?:\n\s*\n|\Z)", body, re.S)
        out.append((int(m.group(1)), m.group(2), work.group(1).strip() if work else body.strip()))
    return out


def reference_tests(paths: list[str]) -> list[str]:
    return [p for p in paths if TEST_FILE.search(p)]


def slice_commits(pr: PR, k: int, mirror: Path, git=run_git) -> list[str]:
    """The PR's commits whose subject, after any `type(scope):` prefix, starts with `task K`."""
    pattern = re.compile(rf"(?i)^task\s*{k}\b")
    hits = [c for c in pr.commits if pattern.match(TYPE_PREFIX.sub("", git(mirror, "log", "-1", "--format=%s", c)))]
    if not hits:
        raise Blocked(f"no commit for task {k} of {pr.repo}#{pr.number}")
    return hits


def plan_file(pr: PR) -> str:
    plans = [f.path for f in pr.changed_files if f.status == "A" and f.path.startswith("docs/") and "plan" in f.path]
    if not plans:
        raise Blocked(f"no plan file added under docs/ by {pr.repo}#{pr.number}")
    return plans[0]


def _toml(task: TaskRow) -> str:
    lines = []
    for key, value in task.__dict__.items():
        if value is None:
            continue
        lines.append(f"{key} = {value if isinstance(value, int) else json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def task_path(task_id: str, out_dir: Path = TASKS) -> Path:
    return out_dir / f"{task_id}.toml"


def build_task(
    repo: RepoConfig, pr: PR, record: RecordRow, slice_index: int | None, goal: str | None,
    out_dir: Path = TASKS, git=run_git,
) -> TaskRow:
    """goal: for a slice, Kevin's goal sentence; for a whole PR, Kevin's full ask (spec §9, amended)."""
    if slice_index is None:
        if not goal:
            raise NeedsGoal(f"#{pr.number} {pr.title}", f"First human turn on record: {record.raw_ask[:500]}")
        task = TaskRow(
            task_id=f"{repo.name}-{pr.number}", repo=repo.name, pr=pr.number, slice_index=None,
            base_commit=pr.base_commit, reference_commit=pr.merge_commit, raw_ask=goal.strip(),
            reference_tests=reference_tests([f.path for f in pr.changed_files if f.status != "D"]), kind="whole",
        )
    else:
        mirror = mirror_path(repo.name)
        commits = slice_commits(pr, slice_index, mirror, git=git)
        tasks = {k: (h, w) for k, h, w in parse_plan(git(mirror, "show", f"{pr.merge_commit}:{plan_file(pr)}"))}
        if slice_index not in tasks:
            raise Blocked(f"no '### Task {slice_index}:' heading in {plan_file(pr)}")
        heading, work = tasks[slice_index]
        if not goal:
            raise NeedsGoal(f"Task {slice_index}: {heading}", work)
        base = git(mirror, "rev-parse", f"{commits[0]}^")
        reference = commits[-1]
        changed = [
            line.split("\t")[-1] for line in git(mirror, "diff", "--name-status", "-M", base, reference).splitlines()
            if line and not line.startswith("D")
        ]
        task = TaskRow(
            task_id=f"{repo.name}-{pr.number}-s{slice_index}", repo=repo.name, pr=pr.number,
            slice_index=slice_index, base_commit=base, reference_commit=reference,
            raw_ask=f"{heading}\n\n{goal.strip()}", reference_tests=reference_tests(changed), kind="slice",
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    task_path(task.task_id, out_dir).write_text(_toml(task))
    return task


def load_task(task_id: str, store=None, out_dir: Path = TASKS) -> TaskRow:
    """From the store when present, else from tasks/<id>.toml."""
    if store is not None:
        try:
            return store.task(task_id)
        except KeyError:
            pass
    import tomllib

    path = task_path(task_id, out_dir)
    if not path.exists():
        raise KeyError(f"no task {task_id} in the store or at {path}")
    d = tomllib.loads(path.read_text())
    d.setdefault("slice_index", None)
    return TaskRow(**d)
