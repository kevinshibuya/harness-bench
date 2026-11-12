import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
# Everything the bench generates (store, report, exports, logs). Gitignored: it is never committed.
RESULTS = ROOT / "out"
MIRRORS = ROOT / "mirrors"
# Run checkouts live outside $HOME: Claude Code reads <ancestor>/.claude/CLAUDE.md for every
# ancestor of the cwd, and $HOME/.claude/CLAUDE.md is Kevin's global file, so a checkout
# under $HOME leaks it into the isolated V arm whatever CLAUDE_CONFIG_DIR says.
WORKTREES = Path("/Users/Shared/harness-bench/worktrees")
ARCHIVE = ROOT / "archive"
TASKS = ROOT / "tasks"
RUNS = ROOT / "runs"
PROMPTS = ROOT / "prompts"
TRANSCRIPT_ROOT = Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class RepoConfig:
    name: str
    path: Path
    default_branch: str
    integration_branch: str
    setup_cmd: str | None
    test_cmd: str
    exclude_sessions: dict[str, str] = field(default_factory=dict)  # session id -> reason (spec §5)


def _resolve(raw: str) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (ROOT / p)


# Target repos. The local file is gitignored and holds the real ones; the example is the fallback.
REPOS_LOCAL = CONFIG / "repos.local.toml"
REPOS_EXAMPLE = CONFIG / "repos.example.toml"


def repos_file() -> Path:
    return REPOS_LOCAL if REPOS_LOCAL.exists() else REPOS_EXAMPLE


def load_repos(path: Path | None = None) -> dict[str, RepoConfig]:
    data = tomllib.loads((path or repos_file()).read_text())
    return {
        name: RepoConfig(
            name=name,
            path=_resolve(entry["path"]),
            default_branch=entry["default_branch"],
            integration_branch=entry["integration_branch"],
            setup_cmd=entry.get("setup_cmd"),
            test_cmd=entry["test_cmd"],
            exclude_sessions={e["id"]: e["reason"] for e in entry.get("exclude_sessions", [])},
        )
        for name, entry in data.items()
    }


def transcript_dir_for(path: Path, root: Path = TRANSCRIPT_ROOT) -> Path:
    """The directory Claude Code writes a cwd's transcripts to."""
    return root / re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))


def transcript_dirs_for(repo: RepoConfig, root: Path = TRANSCRIPT_ROOT) -> list[Path]:
    """The repo's main transcript dir plus every worktree dir beside it."""
    main = transcript_dir_for(repo.path, root)
    dirs = [main] if main.is_dir() else []
    if root.is_dir():
        dirs += sorted(p for p in root.glob(f"{main.name}--claude-worktrees-*") if p.is_dir())
    return dirs
