"""Bench-owned bare mirrors of the target repos. Pushes are disabled."""

import subprocess
from pathlib import Path

from bench.config import MIRRORS, RepoConfig


def run_git(mirror: Path | None, *args: str) -> str:
    """The one place git is called; tests stub it."""
    cmd = ["git"] + (["-C", str(mirror)] if mirror else []) + list(args)
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def github_url(repo: RepoConfig) -> str:
    out = subprocess.run(
        ["gh", "repo", "view", "--json", "url", "-q", ".url"],
        cwd=repo.path, check=True, capture_output=True, text=True,
    )
    return out.stdout.strip() + ".git"


def mirror_path(repo_name: str) -> Path:
    return MIRRORS / f"{repo_name}.git"


def has_github_origin(repo: RepoConfig) -> bool:
    try:
        return "github.com" in run_git(repo.path, "remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return False


def ensure_mirror(repo: RepoConfig, git=run_git) -> Path:
    """mirrors/<repo>.git: GitHub as origin (so refs/pull/* exist), pushes disabled.

    A repo without a GitHub origin (the tiny smoke repo) mirrors its local path.
    """
    mirror = mirror_path(repo.name)
    on_github = has_github_origin(repo)
    if not mirror.exists():
        MIRRORS.mkdir(parents=True, exist_ok=True)
        git(None, "clone", "--quiet", "--mirror", str(repo.path), str(mirror))
        if on_github:
            git(mirror, "remote", "set-url", "origin", github_url(repo))
    git(mirror, "config", "remote.origin.pushurl", "DISABLED")
    refspecs = ["+refs/heads/*:refs/heads/*"]
    if on_github:
        refspecs.append("+refs/pull/*/head:refs/pull/*/head")
    git(mirror, "fetch", "--quiet", "origin", *refspecs)
    return mirror
