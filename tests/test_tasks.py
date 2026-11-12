from datetime import datetime, timezone
from pathlib import Path

import pytest

from bench.store import RecordRow
from bench.tasks import NeedsGoal, build_task, parse_plan, slice_commits
from bench.types import Blocked
from helpers import REPO, pr

FIX = Path(__file__).parent / "fixtures" / "plan_three_tasks.md"


def record():
    return RecordRow(
        repo="r", pr=5, arm="A", source="history", run_id="", title="t", head_branch="feat/x", base_commit="b5",
        reference_commit="m5", merged_at=datetime(2026, 9, 20, tzinfo=timezone.utc), additions=1, deletions=1,
        files_changed=3, stratum="small", kind="whole", harness_version=None, models=[], raw_ask="Build x please",
        outcome="merged", test_gate="n/a", extractor_version="1",
    )


def test_whole_task_fields(tmp_path):
    p = pr(5, "feat/x", files=(("src/a.ts", "M"), ("src/a.test.ts", "A"), ("tests/b.py", "M"), ("__tests__/c.js", "D"),
                               ("app/x.spec.ts", "A")))
    with pytest.raises(NeedsGoal, match="#5"):
        build_task(REPO, p, record(), None, None, out_dir=tmp_path)
    t = build_task(REPO, p, record(), None, None or "Make x work.", out_dir=tmp_path)
    assert (t.task_id, t.kind, t.base_commit, t.reference_commit, t.raw_ask) == ("r-5", "whole", "b5", "m5", "Make x work.")
    assert t.reference_tests == ["src/a.test.ts", "tests/b.py", "app/x.spec.ts"]
    assert (tmp_path / "r-5.toml").exists()


def test_plan_headings():
    tasks = parse_plan(FIX.read_text())
    assert [(k, h) for k, h, _ in tasks] == [(1, "Scaffold the thing"), (2, "The route"), (3, "The viewer")]
    assert tasks[2][2] == "Render the original in a viewer\nacross two lines."


def fake_git(subjects):
    def git(mirror, *args):
        if args[:2] == ("log", "-1"):
            return subjects[args[-1]]
        raise AssertionError(args)
    return git


def test_slice_commits_by_subject():
    subjects = {
        "c1": "feat(x): task 3: the viewer",
        "c2": "fix: task 3 follow-up",
        "c3": "Task 4: next",
        "c4": "plan: Kevin's ruling on task 3",
        "c5": "task 30: not three",
    }
    p = pr(5, "feat/x")
    p = p.__class__(**{**p.__dict__, "commits": tuple(subjects)})
    assert slice_commits(p, 3, Path("/m"), git=fake_git(subjects)) == ["c1", "c2"]
    with pytest.raises(Blocked, match="no commit for task 9 of r#5"):
        slice_commits(p, 9, Path("/m"), git=fake_git(subjects))
