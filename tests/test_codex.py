from datetime import datetime, timedelta, timezone
from pathlib import Path

from bench.attribute import Attribution
from bench.codex import codex_hook, codex_runs_in, load_registry
from bench.phases import segment
from helpers import Builder

FIX = Path(__file__).parent / "fixtures" / "codex_registry.tsv"


def test_both_row_shapes_parse():
    seven, nine = load_registry(FIX)
    assert seven.label == "plan-review-harness-bench"
    assert seven.model == "gpt-6-astra"
    assert seven.ts == datetime(2026, 9, 24, 15, 1, 12, tzinfo=timezone.utc)  # 12:01:12 in São Paulo
    assert seven.duration_s == 645 and seven.input_tokens is None
    assert nine.label == "bench-probe"
    assert (nine.input_tokens, nine.output_tokens) == (13787, 5)


def test_run_matched_by_label_in_bash_input():
    run = load_registry(FIX)[0]
    b = Builder(start=run.ts - timedelta(minutes=5), branch="feat/x")
    b.skill("executing-plans", gap=0)
    b.tool("Bash", command="~/.claude/bin/codex-run.sh plan-review-harness-bench -m gpt-6-astra review")
    b.assistant(gap=900)
    s = b.session()
    window = (s.started, s.ended)
    assert codex_runs_in(window, s.events, [run]) == [run]
    b2 = Builder(start=run.ts - timedelta(minutes=5), branch="feat/x")
    b2.tool("Bash", command="ls")
    b2.assistant(gap=900)
    other = b2.session()
    assert codex_runs_in((other.started, other.ended), other.events, [run]) == []

    att = Attribution("s1", 1, "branch", s.events[0].uuid, s.events[-1].uuid)
    phases = segment([(s, att)], "A")
    codex_hook(load_registry(FIX))(phases, [(s, att)])
    assert "gpt-6-astra" in phases[0].usage_by_model


def _run(ts, label, log):
    from bench.codex import CodexRun
    return CodexRun(ts, "gpt-6-astra", 0, 10.0, Path(log), f"{label}: codex review", label, 5, 5)


def test_same_label_twice_matches_by_log_path():
    b = Builder(branch="feat/x")
    b.skill("executing-plans", gap=0)
    use = b.tool("Bash", command="~/.claude/bin/codex-run.sh pr-review -m gpt-6-astra review --base ai-staging")
    b.result(use.tool_use_id, text="codex-run: label=pr-review model=gpt-6-astra exit=0 duration=9s log=/logs/b.log")
    b.assistant(gap=300)
    s = b.session()
    mine, other = _run(b.t - timedelta(seconds=200), "pr-review", "/logs/b.log"), _run(b.t - timedelta(seconds=100), "pr-review", "/logs/a.log")
    assert codex_runs_in((s.started, s.ended), s.events, [mine, other]) == [mine]


def test_run_between_review_windows_lands_in_execution():
    import sys
    from test_phases import two_session_review_record, whole
    s1, s2, exec_mid = two_session_review_record()
    use = s2.events[1]  # the first assistant after pickup: charge a codex exec call there
    bash = use.__class__(**{**use.__dict__, "kind": "tool_use", "tool_name": "Bash", "uuid": "bash1",
                            "tool_use_id": "tb1", "tool_input": {"command": "~/.claude/bin/codex-run.sh impl exec x"},
                            "usage": None, "message_id": None})
    res = bash.__class__(**{**bash.__dict__, "kind": "tool_result", "uuid": "res1", "tool_input": None,
                            "text": "codex-run: label=impl model=gpt-5.6-terra exit=0 duration=5s log=/logs/impl.log"})
    s2.events[2:2] = [bash, res]
    run = _run(exec_mid.ts, "impl", "/logs/impl.log")
    run = run.__class__(**{**run.__dict__, "model": "gpt-5.6-terra"})
    pairs = [whole(s1), whole(s2)]
    phases = segment(pairs, "A")
    codex_hook([run])(phases, pairs)
    names = {p.name: p for p in phases}
    assert "gpt-5.6-terra" in names["execution"].usage_by_model
    assert "gpt-5.6-terra" not in names["pr_review"].usage_by_model
