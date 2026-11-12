from datetime import datetime, timezone

from bench.phases import Phase, PhaseOverride
from bench.store import FindingRow, JudgementRow, RecordRow, Store
from bench.transcripts import Usage

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def rec(pr=1, arm="A", source="history", run_id="", models=("claude-opus-5-5",)):
    return RecordRow(
        repo="r", pr=pr, arm=arm, source=source, run_id=run_id, title="t", head_branch="feat/x",
        base_commit="b", reference_commit="m", merged_at=NOW, additions=10, deletions=5, files_changed=2,
        stratum="small", kind="whole", harness_version="abc", models=list(models), raw_ask="do it",
        outcome="merged", test_gate="n/a", extractor_version="1",
    )


def test_history_upsert_is_idempotent(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    a = s.upsert_record(rec())
    b = s.upsert_record(rec())
    assert a == b
    assert len(s.records()) == 1


def test_arm_flip_replaces_history_row(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    rid = s.upsert_record(rec(arm="Aprime"))
    s.add_judgement(JudgementRow(rid, NOW, "codex,opus", 1, "ok"))
    s.add_finding(FindingRow(rid, "judge", None, "major", None, True, "bug", "both"))
    assert s.upsert_record(rec(arm="A")) == rid
    df = s.records()
    assert list(df["arm"]) == ["A"]
    assert list(s.judgements()["record_id"]) == [rid]
    assert list(s.findings()["verified"]) == [1]


def test_runs_are_keyed_by_run_id(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    s.upsert_record(rec(arm="V", source="run", run_id="r1"))
    s.upsert_record(rec(arm="V", source="run", run_id="r2"))
    s.upsert_record(rec(arm="A"))
    assert len(s.records()) == 3


def test_strict_view(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    s.upsert_record(rec(pr=1, models=("claude-opus-5", "claude-fable-5-1")))
    s.upsert_record(rec(pr=2, models=("claude-opus-5-5", "gpt-6-astra", "claude-haiku-4-5-20251001")))
    s.upsert_record(rec(pr=3, models=("claude-fable-5-1", "<synthetic>")))
    assert sorted(s.records("strict")["pr"]) == [2, 3]
    assert sorted(s.records("loosened")["pr"]) == [1, 2, 3]


def test_judgement_with_zero_findings_and_phases(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    rid = s.upsert_record(rec())
    s.add_judgement(JudgementRow(rid, NOW, "opus,codex", 0, "ok"))
    assert list(s.judgements()["n_findings"]) == [0]
    fid = s.add_finding(FindingRow(rid, "judge", None, "major", None, None, "bug", "both"))
    assert fid > 0
    ph = Phase("execution", NOW, NOW, 60.0, {"claude-opus-5-5": Usage(1, 2, 3, 4)}, 2, 30.0, ["a1"], ["s1"])
    s.replace_phases(rid, [ph], {"execution": 1.5})
    s.replace_phases(rid, [ph], {"execution": 1.5})
    df = s.phases()
    assert len(df) == 1 and df.iloc[0]["cost_usd"] == 1.5


def test_meta_and_overrides(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    s.set_meta("k", "v")
    assert s.get_meta("k") == "v"
    assert s.get_meta("schema_version") == "1"
    s.add_phase_override(PhaseOverride("s1", "u1", "spec"))
    assert s.phase_overrides("s1") == [PhaseOverride("s1", "u1", "spec")]


def test_run_record_round_trips_with_nulls(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    r = rec(arm="V", source="run", run_id="r1")
    r.merged_at, r.harness_version = None, None
    rid = s.upsert_record(r)
    back = s.record(rid)
    assert back.merged_at is None and back.harness_version is None and back.pr == 1
