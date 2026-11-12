import os
import shutil
from pathlib import Path

from bench.archive import archive_transcripts

FIX = Path(__file__).parent / "fixtures" / "mini_session"


def test_copies_new_and_changed_never_deletes(tmp_path):
    src = tmp_path / "src" / "-repo-r"
    shutil.copytree(FIX, src)
    arch = tmp_path / "archive"
    dirs = archive_transcripts([src], arch, "r")
    assert dirs == [arch / "r" / "-repo-r"]
    copied = arch / "r" / "-repo-r" / "s1" / "subagents" / "agent-a1.jsonl"
    assert copied.exists()
    main = src / "s1.jsonl"
    main.write_text(main.read_text() + "\n")
    os.utime(main, (1e9, 2e9))
    archive_transcripts([src], arch, "r")
    assert (arch / "r" / "-repo-r" / "s1.jsonl").read_text() == main.read_text()
    shutil.rmtree(src)
    src.mkdir()
    archive_transcripts([src], arch, "r")
    assert copied.exists()


def test_codex_logs_are_copied(tmp_path):
    from bench.archive import archive_codex_logs
    src = tmp_path / "codex-runs"
    src.mkdir()
    (src / "20260901-1-x.log").write_text("review text")
    (src / "registry.tsv").write_text("row\n")
    (src / "notes.txt").write_text("ignored")
    dest = tmp_path / "archive" / "codex-runs"
    assert archive_codex_logs(src, dest) == 2
    (src / "20260901-1-x.log").unlink()
    assert archive_codex_logs(src, dest) == 0
    assert (dest / "20260901-1-x.log").read_text() == "review text"
    assert not (dest / "notes.txt").exists()
