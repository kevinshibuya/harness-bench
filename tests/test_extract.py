import shutil
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bench.archive import archive_transcripts
from bench.config import RepoConfig
from bench.extract import extract_repo, raw_ask_for
from bench.price import load_prices
from bench.store import Store
from helpers import pr

FIX = Path(__file__).parent / "fixtures" / "mini_session"
REPO = RepoConfig("r", Path("/repo/x"), "main", "ai-staging", None, "true")
T = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def run(tmp_path, store):
    src = tmp_path / "src" / "-repo-x"
    arch = tmp_path / "archive"
    archive_transcripts([src] if src.exists() else [], arch, "r")
    prs = [pr(7, "feat/x", first=T - timedelta(days=1), merged=T + timedelta(days=1))]
    return extract_repo(REPO, store, prs, arch, load_prices(), harness_version=lambda ts: "h1",
                        unattributed_dir=tmp_path / "unattributed")


def test_record_survives_source_deletion(tmp_path):
    shutil.copytree(FIX, tmp_path / "src" / "-repo-x")
    store = Store(tmp_path / "b.sqlite")
    first = run(tmp_path, store)
    assert first.records_upserted == 1 and first.sessions_seen == 1
    before = store.records()
    shutil.rmtree(tmp_path / "src")
    second = run(tmp_path, store)
    after = store.records()
    assert second.records_upserted == 1
    assert list(after["id"]) == list(before["id"])
    r = after.iloc[0]
    assert (r["repo"], r["pr"], r["arm"], r["source"], r["run_id"]) == ("r", 7, "A", "history", "")
    assert r["raw_ask"] == "Build feature x"
    assert r["harness_version"] == "h1"
    assert set(r["models"]) == {"claude-opus-5-5"}
    phases = store.phases()
    assert list(phases["name"]) == ["unassigned", "spec", "plan_review"]
    assert phases["cost_usd"].sum() > 0
    cov = store.coverage()
    assert list(cov["month"]) == ["2026-09"] and list(cov["sessions_unattributed"]) == [0]


def test_unattributed_sessions_are_listed(tmp_path):
    shutil.copytree(FIX, tmp_path / "src" / "-repo-x")
    store = Store(tmp_path / "b.sqlite")
    arch = tmp_path / "archive"
    archive_transcripts([tmp_path / "src" / "-repo-x"], arch, "r")
    s = extract_repo(REPO, store, [], arch, load_prices(), harness_version=lambda ts: "h1",
                     unattributed_dir=tmp_path / "unattributed")
    assert s.sessions_unattributed == 1 and s.records_upserted == 0
    listed = (tmp_path / "unattributed" / "r.json").read_text()
    assert "Build feature x" in listed


def test_excluded_session_is_dropped_before_attribution(tmp_path):
    shutil.copytree(FIX, tmp_path / "src" / "-repo-x")
    store = Store(tmp_path / "b.sqlite")
    arch = tmp_path / "archive"
    archive_transcripts([tmp_path / "src" / "-repo-x"], arch, "r")
    repo = replace(REPO, exclude_sessions={"s1": "worked on another project"})
    prs = [pr(7, "feat/x", first=T - timedelta(days=1), merged=T + timedelta(days=1))]
    s = extract_repo(repo, store, prs, arch, load_prices(), harness_version=lambda ts: "h1",
                     unattributed_dir=tmp_path / "unattributed")
    assert s.records_upserted == 0
    assert (s.sessions_seen, s.sessions_unattributed, s.sessions_excluded) == (0, 0, 1)
    cov = store.coverage()
    assert list(cov["sessions_excluded"]) == [1] and list(cov["sessions_seen"]) == [0]
    assert "Build feature x" not in (tmp_path / "unattributed" / "r.json").read_text()
