"""Merged PRs into the integration branch, with commits from the mirror (spec §4)."""

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from bench.config import RESULTS, RepoConfig
from bench.mirrors import ensure_mirror, run_git
from bench.transcripts import parse_ts
from bench.types import Blocked

GH_FIELDS = "number,title,headRefName,baseRefName,mergedAt,mergeCommit,additions,deletions"


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: Literal["A", "M", "D", "R"]


@dataclass(frozen=True)
class PR:
    repo: str
    number: int
    title: str
    head_branch: str
    base_branch: str
    merged_at: datetime
    merge_commit: str
    base_commit: str  # merge_commit^1
    head_commit: str
    first_commit_at: datetime
    commits: tuple[str, ...]  # from refs/pull/N/head, oldest first
    additions: int
    deletions: int
    changed_files: tuple[ChangedFile, ...]

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


_STATUS = {"A": "A", "M": "M", "D": "D", "R": "R", "C": "A", "T": "M"}


def _changed_files(name_status: str) -> tuple[ChangedFile, ...]:
    out = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = _STATUS.get(parts[0][0], "M")
        out.append(ChangedFile(parts[-1], status))
    return tuple(out)


def build_prs(repo: RepoConfig, raw: list[dict], mirror: Path, git=run_git) -> list[PR]:
    prs = []
    for item in raw:
        head, base = item["headRefName"], item["baseRefName"]
        if base != repo.integration_branch or head in {repo.default_branch, repo.integration_branch}:
            continue
        n = item["number"]
        merge = item["mergeCommit"]["oid"]
        base_commit = git(mirror, "rev-parse", f"{merge}^1")
        try:
            head_commit = git(mirror, "rev-parse", "--verify", "-q", f"refs/pull/{n}/head")
        except (subprocess.CalledProcessError, Blocked):
            raise Blocked(f"refs/pull/{n}/head missing in mirror {repo.name}")
        mb = git(mirror, "merge-base", base_commit, head_commit)
        commits = tuple(git(mirror, "rev-list", "--reverse", f"{mb}..{head_commit}").split()) or (head_commit,)
        first_at = parse_ts(git(mirror, "log", "-1", "--format=%aI", commits[0]))
        prs.append(PR(
            repo=repo.name, number=n, title=item["title"], head_branch=head, base_branch=base,
            merged_at=parse_ts(item["mergedAt"]), merge_commit=merge, base_commit=base_commit,
            head_commit=head_commit, first_commit_at=first_at, commits=commits,
            additions=item["additions"], deletions=item["deletions"],
            changed_files=_changed_files(git(mirror, "diff", "--name-status", "-M", base_commit, merge)),
        ))
    return prs


def _prs_file(repo: RepoConfig) -> Path:
    return RESULTS / "prs" / f"{repo.name}.json"


def sync_prs(repo: RepoConfig) -> list[PR]:
    mirror = ensure_mirror(repo)
    out = subprocess.run(
        ["gh", "pr", "list", "--state", "merged", "--limit", "200", "--json", GH_FIELDS],
        cwd=repo.path, check=True, capture_output=True, text=True,
    )
    prs = build_prs(repo, json.loads(out.stdout), mirror)
    path = _prs_file(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in prs], default=str, indent=1) + "\n")
    return prs


def load_prs(repo: RepoConfig) -> list[PR]:
    path = _prs_file(repo)
    if not path.exists():
        return []
    prs = []
    for d in json.loads(path.read_text()):
        d["merged_at"] = parse_ts(d["merged_at"])
        d["first_commit_at"] = parse_ts(d["first_commit_at"])
        d["commits"] = tuple(d["commits"])
        d["changed_files"] = tuple(ChangedFile(**f) for f in d["changed_files"])
        prs.append(PR(**d))
    return prs
