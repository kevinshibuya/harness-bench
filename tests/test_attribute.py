from datetime import timedelta

from bench.attribute import attribute, events_in, repo_relative
from helpers import REPO, T0, Builder, pr


def kinds_and_prs(session, atts):
    return [(a.pr_number, a.rule, [e.text for e in events_in(session, a) if e.kind == "human"]) for a in atts]


def test_grill_on_staging_then_branch_is_one_attribution():
    b = Builder()
    b.human("let's grill feature x")
    b.skill("grilling")
    b.assistant()
    b.human("approved, create the branch")
    b.assistant()
    b.branch = "feat/x"
    b.assistant()
    b.human("done?")
    b.assistant()
    s = b.session()
    atts = attribute(s, [pr(1, "feat/x"), pr(2, "feat/y")], REPO)
    assert len(atts) == 1
    a = atts[0]
    assert (a.pr_number, a.rule) == (1, "branch")
    assert a.start_uuid == s.events[0].uuid and a.end_uuid == s.events[-1].uuid


def test_x_y_x_yields_three_ranges_with_switch_turns_after_the_cut():
    b = Builder(branch="feat/x")
    b.human("work on x")
    b.assistant()
    b.human("now switch to y")
    b.assistant()  # runs git checkout, still reported on feat/x
    b.branch = "feat/y"
    b.assistant()
    b.human("y is done, back to x")
    b.assistant()
    b.branch = "feat/x"
    b.assistant()
    b.human("finish x")
    s = b.session()
    atts = attribute(s, [pr(1, "feat/x"), pr(2, "feat/y")], REPO)
    assert kinds_and_prs(s, atts) == [
        (1, "branch", ["work on x"]),
        (2, "branch", ["now switch to y"]),
        (1, "branch", ["y is done, back to x", "finish x"]),
    ]
    covered = [e.uuid for a in atts for e in events_in(s, a)]
    assert covered == [e.uuid for e in s.events]


def test_pr_link_attributes_its_segment():
    b = Builder(branch="ai-staging")
    b.human("small fix")
    b.assistant()
    b.pr_link(2)
    s = b.session()
    atts = attribute(s, [pr(1, "feat/x"), pr(2, "feat/y")], REPO)
    assert [(a.pr_number, a.rule) for a in atts] == [(2, "pr_link")]


def test_window_and_edited_paths():
    b = Builder(branch="ai-staging")
    b.human("fix it")
    b.tool("Edit", file_path="/repo/r/.claude/worktrees/wt1/src/a.ts")
    s = b.session()
    far = pr(3, "feat/z", first=T0 - timedelta(days=9), merged=T0 - timedelta(days=8))
    atts = attribute(s, [pr(1, "feat/x", files=(("src/a.ts", "M"),)), far], REPO)
    assert [(a.pr_number, a.rule) for a in atts] == [(1, "window")]


def test_window_needs_a_unique_pr():
    b = Builder(branch="ai-staging")
    b.tool("Write", file_path="src/a.ts")
    s = b.session()
    atts = attribute(s, [pr(1, "feat/x"), pr(2, "feat/y")], REPO)
    assert [(a.pr_number, a.rule) for a in atts] == [(None, "none")]


def test_no_signal_is_none():
    b = Builder(branch="main")
    b.human("what's up")
    b.assistant()
    s = b.session()
    atts = attribute(s, [pr(1, "feat/x")], REPO)
    assert [(a.pr_number, a.rule) for a in atts] == [(None, "none")]


def test_repo_relative():
    assert repo_relative("/repo/r/.claude/worktrees/board-rag/src/a.ts", None, REPO) == "src/a.ts"
    assert repo_relative("/repo/r/src/b.ts", None, REPO) == "src/b.ts"
    assert repo_relative("src/c.ts", "/repo/r/.claude/worktrees/w", REPO) == "src/c.ts"
    assert repo_relative("/elsewhere/x.ts", None, REPO) is None
    assert repo_relative("/repo/rr/x.ts", None, REPO) is None


def test_branch_switch_without_a_human_turn_keeps_the_segment():
    b = Builder(branch="ai-staging")
    b.human("start")
    b.assistant()
    b.branch = "feat/x"
    b.assistant()
    b.tool("Bash", command="git commit")
    b.assistant()
    b.branch = "ai-staging"
    b.assistant()
    s = b.session()
    atts = attribute(s, [pr(1, "feat/x", files=(("other.ts", "M"),))], REPO)
    assert [(a.pr_number, a.rule) for a in atts] == [(1, "branch"), (None, "none")]
    assert events_in(s, atts[0])[0].text == "start"
