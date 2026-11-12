from datetime import timedelta

from bench.attribute import Attribution
from bench.phases import PhaseOverride, arm_for, segment
from bench.transcripts import Usage
from helpers import T0, Builder, pr


def whole(session, n=1):
    return (session, Attribution(session.session_id, n, "branch", session.events[0].uuid, session.events[-1].uuid))


def pipeline_session():
    b = Builder(branch="feat/x")
    b.skill("grilling", gap=0)
    b.human("answers")
    b.assistant(model="claude-fable-5-1")
    b.skill("writing-plans")
    b.assistant(model="claude-fable-5-1")
    review = b.agent("rev1", subagent_type="reviewer")
    b.assistant()
    b.skill("executing-plans")
    b.result(review.tool_use_id, agentId="rev1")  # lands after execution began
    b.assistant()
    b.tool("Edit", file_path="/repo/r/src/a.ts")
    return b


def by_name(phases):
    return {p.name: p for p in phases}


def test_pipeline_yields_four_phases_and_subagent_charged_at_dispatch():
    s = pipeline_session().session()
    phases = segment([whole(s)], "A")
    assert [p.name for p in phases] == ["spec", "plan", "plan_review", "execution"]
    ph = by_name(phases)
    assert ph["plan_review"].agent_ids == ["rev1"]
    assert ph["plan_review"].usage_by_model["claude-fable-5-1"] == Usage(100, 0, 0, 50)
    assert "claude-fable-5-1" not in ph["execution"].usage_by_model
    assert ph["spec"].human_turns == 1


def test_arm_for_skill_and_added_plan_file():
    s = pipeline_session().session()
    assert arm_for([whole(s)], pr(1, "feat/x")) == "A"
    b = Builder(branch="feat/y")
    b.human("fix")
    b.tool("Edit", file_path="/repo/r/docs/plans/p.md")
    plain = b.session()
    assert arm_for([whole(plain)], pr(2, "feat/y", files=(("docs/plans/p.md", "M"),))) == "Aprime"
    assert arm_for([whole(plain)], pr(2, "feat/y", files=(("docs/plans/p.md", "A"),))) == "A"


def test_pickup_continues_execution_and_overnight_gap_is_not_wall():
    first = pipeline_session()
    first.assistant()
    s1 = first.session()
    b = Builder(session_id="s2", start=s1.ended + timedelta(hours=10), branch="feat/x")
    b.skill("pickup", gap=0)
    b.assistant(gap=100)
    s2 = b.session()
    phases = segment([whole(s1), whole(s2)], "A")
    ex = by_name(phases)["execution"]
    assert ex.session_ids == ["s1", "s2"]
    one_session = segment([whole(s1)], "A")
    assert ex.wall_s == by_name(one_session)["execution"].wall_s + 100
    assert ex.wall_s < 3600


def test_attention_is_capped_at_600():
    b = Builder(branch="feat/x")
    b.skill("executing-plans", gap=0)
    b.assistant()
    b.human("back after lunch", gap=20 * 60)
    b.assistant()
    b.human("quick one", gap=30)
    ph = by_name(segment([whole(b.session())], "A"))["execution"]
    assert ph.human_turns == 2
    assert ph.attention_s == 600 + 30


def test_aprime_edit_then_reviewer_is_execution_then_pr_review():
    b = Builder(branch="feat/y")
    b.human("inline fix")
    b.assistant()
    b.tool("Edit", file_path="/repo/r/docs/notes.md")  # docs edit does not open execution
    b.tool("Edit", file_path="/repo/r/src/a.ts")
    b.assistant()
    rev = b.agent("rev2")
    b.result(rev.tool_use_id, agentId="rev2")
    b.assistant()
    phases = segment([whole(b.session())], "Aprime")
    assert [p.name for p in phases] == ["unassigned", "execution", "pr_review"]
    assert by_name(phases)["pr_review"].agent_ids == ["rev2"]


def test_override_assigns_an_unassigned_prefix():
    b = Builder(branch="feat/y")
    first = b.human("let's talk design")
    b.assistant()
    b.skill("executing-plans")
    s = b.session()
    phases = segment([whole(s)], "A", [PhaseOverride("s1", first.uuid, "spec")])
    assert [p.name for p in phases] == ["spec", "execution"]


def test_nested_subagents_are_counted_once():
    b = Builder(branch="feat/x")
    b.skill("executing-plans", gap=0)
    outer = b.agent("outer", subagent_type="worker", usage=Usage(1, 0, 0, 1), model="claude-opus-5-5")
    b.result(outer.tool_use_id, agentId="outer")
    s = b.session()
    t = s.subagents["outer"][0].ts
    from bench.transcripts import Event
    s.subagents["outer"] += [
        Event("tool_use", t, "s1", "outer", "o-2", None, None, None, tool_name="Agent", tool_use_id="ot1", tool_input={}),
        Event("tool_result", t, "s1", "outer", "o-3", None, None, None, tool_use_id="ot1", tool_result={"agentId": "inner"}),
    ]
    s.subagents["inner"] = [Event("assistant", t, "s1", "inner", "i-1", None, None, None, model="claude-haiku-4-5",
                                  message_id="im", usage=Usage(7, 0, 0, 7))]
    ex = by_name(segment([whole(s)], "A"))["execution"]
    assert ex.agent_ids == ["outer", "inner"]
    assert ex.usage_by_model["claude-haiku-4-5"] == Usage(7, 0, 0, 7)


def two_session_review_record():
    """s1: execution then pr_review; s2: execution again, then pr_review again."""
    a = Builder(session_id="s1", branch="feat/x")
    a.skill("executing-plans", gap=0)
    a.assistant()
    rev = a.agent("r1")
    a.result(rev.tool_use_id, agentId="r1")
    a.assistant(text="review one")
    s1 = a.session()
    b = Builder(session_id="s2", start=s1.ended + timedelta(hours=2), branch="feat/x")
    b.skill("executing-plans", gap=0)
    b.assistant(text="more execution")
    exec_mid = b.assistant(text="execution work")
    rev2 = b.agent("r2")
    b.result(rev2.tool_use_id, agentId="r2")
    b.assistant(text="review two")
    return s1, b.session(), exec_mid


def test_recurring_phase_keeps_per_window_intervals():
    s1, s2, exec_mid = two_session_review_record()
    phases = by_name(segment([whole(s1), whole(s2)], "A"))
    review = phases["pr_review"]
    assert len(review.windows) == 2
    assert not any(start <= exec_mid.ts <= end for _, start, end in review.windows)
    assert any(start <= exec_mid.ts <= end for _, start, end in phases["execution"].windows)


def test_usage_shared_across_sessions_counts_once():
    a = Builder(session_id="s1", branch="feat/x")
    a.skill("executing-plans", gap=0)
    m = a.assistant(usage=Usage(10, 0, 0, 10))
    s1 = a.session()
    b = Builder(session_id="s2", start=s1.ended + timedelta(minutes=5), branch="feat/x")
    b.skill("pickup", gap=0)
    b.events.append(m.__class__(**{**m.__dict__, "session_id": "s2", "uuid": "copy", "ts": b.t}))
    s2 = b.session()
    ex = by_name(segment([whole(s1), whole(s2)], "A"))["execution"]
    assert ex.usage_by_model["claude-opus-5-5"] == Usage(10, 0, 0, 10)
