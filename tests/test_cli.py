import pytest

from bench.cli import main

SUBCOMMANDS = ["extract", "report", "run", "judge", "tasks", "status"]


def test_help_lists_every_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for name in SUBCOMMANDS:
        assert name in out



def test_extract_archives_even_when_gh_fails(tmp_path, monkeypatch, capsys):
    import subprocess
    import bench.archive, bench.config, bench.extract, bench.prs, bench.store
    calls = []

    def boom(repo):
        raise subprocess.CalledProcessError(1, ["gh"])

    monkeypatch.setattr(bench.prs, "sync_prs", boom)
    monkeypatch.setattr(bench.prs, "load_prs", lambda repo: [])
    monkeypatch.setattr(bench.archive, "archive_transcripts", lambda dirs, root, name: calls.append(name) or [])
    monkeypatch.setattr(bench.archive, "archive_codex_logs", lambda *a, **k: 0)
    monkeypatch.setattr(bench.extract, "extract_repo", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no PRs")))
    monkeypatch.setattr(bench.store, "Store", lambda *a, **k: object())
    example = bench.config.load_repos(bench.config.REPOS_EXAMPLE)
    monkeypatch.setattr(bench.config, "load_repos", lambda path=None: example)
    assert main(["extract", "--repo", "sample-app"]) == 0
    assert calls == ["sample-app"]
    assert "gh failed" in capsys.readouterr().out


def test_status_counts_stale_records(tmp_path, monkeypatch, capsys):
    import bench.store
    from datetime import datetime, timezone
    from bench.store import RecordRow, Store
    db = tmp_path / "b.sqlite"
    s = Store(db)
    s.upsert_record(RecordRow("r", 1, "A", "history", "", "t", "h", "b", "m", datetime(2026, 9, 20, tzinfo=timezone.utc),
                              1, 1, 1, "small", "whole", None, [], "x", "merged", "n/a", "1"))
    monkeypatch.setattr(bench.store, "Store", lambda *a, **k: Store(db))
    assert main(["status"]) == 0
    assert "stale history records (extractor version below 2): 1" in capsys.readouterr().out
