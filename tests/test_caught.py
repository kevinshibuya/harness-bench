import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bench.attribute import Attribution
from bench.caught import extract_caught, review_segments
from bench.codex import CodexRun
from bench.phases import segment
from bench.store import RecordRow, Store
from helpers import Builder


def session_with_codex():
    b = Builder(branch="feat/x")
    b.skill("writing-plans", gap=0)
    review = b.agent("rev1", subagent_type="reviewer")
    b.result(review.tool_use_id, agentId="rev1")
    bash = b.tool("Bash", command="~/.claude/bin/codex-run.sh plan-review-x -m gpt-6-astra review")
    b.result(bash.tool_use_id, text="codex-run: label=plan-review-x model=gpt-6-astra exit=0 duration=10s "
                                    "log=/nonexistent/gone.log")
    b.assistant(text="Both reviews agree; fixing the plan.")
    run_ts = b.t - timedelta(seconds=30)
    b.skill("executing-plans")
    s = b.session()
    run = CodexRun(run_ts, "gpt-6-astra", 0, 10, Path("/nonexistent/gone.log"), "plan-review-x: codex review",
                   "plan-review-x", None, None)
    return s, Attribution("s1", 1, "branch", s.events[0].uuid, s.events[-1].uuid), run


def test_segments_carry_subagent_and_codex_outputs(tmp_path):
    s, att, run = session_with_codex()
    (tmp_path / "gone.log").write_text("- [P1] The plan skips migrations")
    phases = segment([(s, att)], "A")
    segs = review_segments([(s, att)], phases, [run], codex_archive=tmp_path)
    assert [seg.stage for seg in segs] == ["plan_review"]
    assert segs[0].subagent_outputs == ["rev1 final"]
    assert segs[0].codex_outputs == ["- [P1] The plan skips migrations"]
    assert "fixing the plan" in segs[0].main_text


def test_fake_runner_json_becomes_findings(tmp_path):
    s, att, run = session_with_codex()
    store = Store(tmp_path / "b.sqlite")
    rid = store.upsert_record(RecordRow(
        repo="r", pr=1, arm="A", source="history", run_id="", title="t", head_branch="feat/x", base_commit="b",
        reference_commit="m", merged_at=datetime(2026, 9, 20, tzinfo=timezone.utc), additions=1, deletions=1,
        files_changed=1, stratum="small", kind="whole", harness_version=None, models=[], raw_ask="x",
        outcome="merged", test_gate="n/a", extractor_version="1"))
    answer = [{"finding": "skips migrations", "source": "codex", "status": "confirmed"},
              {"finding": "naming", "source": "reviewer", "status": "dismissed"}]

    def runner(argv, **kw):
        assert "rev1 final" in argv[-1] and "skips migrations" in argv[-1]
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": json.dumps(answer)}), stderr="")

    (tmp_path / "gone.log").write_text("- [P1] The plan skips migrations")
    rows = extract_caught(store, rid, [(s, att)], runner, registry=[run], codex_archive=tmp_path)
    assert [(r.stage, r.confirmed, r.leg) for r in rows] == [("plan_review", True, "codex"), ("plan_review", False, "reviewer")]
    assert list(store.findings()["origin"]) == ["in_process", "in_process"]
    extract_caught(store, rid, [(s, att)], runner, registry=[run], codex_archive=tmp_path)
    assert len(store.findings()) == 2


def test_expired_log_without_archive_falls_back_to_the_wrapper_line(tmp_path):
    s, att, run = session_with_codex()
    segs = review_segments([(s, att)], segment([(s, att)], "A"), [run], codex_archive=tmp_path)
    assert segs[0].codex_outputs == [next(e.text for e in s.events if e.kind == "tool_result" and e.text and "codex-run:" in e.text)]


def test_segment_text_excludes_the_gap_between_windows():
    from test_phases import two_session_review_record, whole
    s1, s2, _ = two_session_review_record()
    pairs = [whole(s1), whole(s2)]
    segs = review_segments(pairs, segment(pairs, "A"), [])
    text = segs[0].main_text
    assert "review one" in text and "review two" in text
    assert "execution work" not in text and "more execution" not in text
