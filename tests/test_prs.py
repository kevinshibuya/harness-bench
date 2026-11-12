import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bench.config import RepoConfig
from bench.prs import build_prs
from bench.types import Blocked

FIX = Path(__file__).parent / "fixtures" / "prs_sample.json"
REPO = RepoConfig("sample-app", Path("/tmp/sa"), "main", "ai-staging", None, "true")


def fake_git(missing_pull: int | None = None):
    def git(mirror, *args):
        match args:
            case ("rev-parse", ref) if ref.endswith("^1"):
                return "base-of-" + ref.removesuffix("^1")
            case ("rev-parse", "--verify", "-q", ref):
                n = int(ref.split("/")[2])
                if n == missing_pull:
                    raise Blocked("missing")
                return f"head{n}"
            case ("merge-base", a, b):
                return "mb-" + b
            case ("rev-list", "--reverse", rng):
                head = rng.split("..")[1]
                return f"{head}-c1\n{head}-c2"
            case ("log", "-1", "--format=%aI", sha):
                return "2026-09-05T12:00:00-03:00"
            case ("diff", "--name-status", "-M", base, merge):
                return "M\tapp/a.ts\nA\tdocs/plans/p.md\nR087\told.ts\tnew.ts\nD\tgone.ts"
        raise AssertionError(args)
    return git


def test_promotion_pr_is_filtered_and_base_is_first_parent():
    prs = build_prs(REPO, json.loads(FIX.read_text()), Path("/m"), git=fake_git())
    assert [p.number for p in prs] == [23, 21]
    p = prs[0]
    assert p.base_commit == "base-of-m23"
    assert p.merge_commit == "m23"
    assert p.head_commit == "head23"
    assert p.commits == ("head23-c1", "head23-c2")
    assert p.first_commit_at == datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    assert p.merged_at == datetime(2026, 9, 11, 6, 20, 14, tzinfo=timezone.utc)
    assert [(f.path, f.status) for f in p.changed_files] == [
        ("app/a.ts", "M"), ("docs/plans/p.md", "A"), ("new.ts", "R"), ("gone.ts", "D"),
    ]


def test_missing_pull_ref_is_blocked():
    with pytest.raises(Blocked, match="refs/pull/21/head missing in mirror sample-app"):
        build_prs(REPO, json.loads(FIX.read_text()), Path("/m"), git=fake_git(missing_pull=21))
