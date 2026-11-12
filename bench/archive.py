"""Copy transcripts into archive/ before Claude Code's 30-day cleanup deletes them (spec §3)."""

import shutil
from pathlib import Path


def archive_transcripts(dirs: list[Path], archive_root: Path, repo: str) -> list[Path]:
    """Copy new or changed files into archive/<repo>/<dir name>/…; never delete. Returns the archived dirs."""
    out = []
    for d in dirs:
        target_dir = archive_root / repo / d.name
        for src in d.rglob("*"):
            if not src.is_file():
                continue
            dst = target_dir / src.relative_to(d)
            st = src.stat()
            if dst.exists():
                dt = dst.stat()
                if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                    continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        if target_dir.is_dir():
            out.append(target_dir)
    return out


def archived_dirs(archive_root: Path, repo: str) -> list[Path]:
    base = archive_root / repo
    return sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []


CODEX_RUNS = Path.home() / ".claude" / "codex-runs"


def archive_codex_logs(src: Path = CODEX_RUNS, dest: Path | None = None) -> int:
    """codex-run.sh deletes logs after 30 days; keep every *.log and the registry. Returns files copied."""
    from bench.config import ARCHIVE

    dest = dest or ARCHIVE / "codex-runs"
    copied = 0
    if not src.is_dir():
        return 0
    for f in [*src.glob("*.log"), src / "registry.tsv"]:
        if not f.is_file():
            continue
        target = dest / f.name
        st = f.stat()
        if target.exists() and target.stat().st_size == st.st_size and int(target.stat().st_mtime) == int(st.st_mtime):
            continue
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        copied += 1
    return copied
