from collections import Counter
from datetime import timezone
from pathlib import Path

from bench.transcripts import iter_sessions, read_session

FIX = Path(__file__).parent / "fixtures" / "mini_session"


def load():
    return read_session(FIX / "s1.jsonl", repo="r")


def test_counts_per_kind_match_readme():
    s = load()
    assert Counter(e.kind for e in s.events) == {
        "human": 2, "assistant": 5, "tool_use": 3, "tool_result": 3, "pr_link": 1,
    }
    assert s.skipped_lines == 1


def test_usage_once_per_message_id():
    s = load()
    with_usage = [e for e in s.events if e.usage is not None]
    assert len(with_usage) == 4
    m1 = [e for e in s.events if e.message_id == "m1" and e.usage is not None]
    assert len(m1) == 1
    assert m1[0].usage.cache_read == 1000
    assert m1[0].uuid == "a1"


def test_usage_is_the_last_line_of_the_message():
    m1 = next(e for e in load().events if e.message_id == "m1" and e.usage is not None)
    assert m1.usage.output == 250


def test_command_and_meta_lines_are_not_human():
    texts = [e.text for e in load().events if e.kind == "human"]
    assert texts == ["Build feature x", "thanks"]


def test_bash_result_carries_stdout():
    s = load()
    bash = next(e for e in s.events if e.kind == "tool_use" and e.tool_name == "Bash")
    res = next(e for e in s.events if e.kind == "tool_result" and e.tool_use_id == bash.tool_use_id)
    assert res.text == "On branch feat/x"
    assert res.tool_result["stdout"] == "On branch feat/x"


def test_tool_use_fields():
    s = load()
    skill = next(e for e in s.events if e.tool_name == "Skill")
    assert skill.tool_input == {"skill": "grilling", "args": "x"}
    assert skill.tool_use_id == "t1"
    assert skill.usage is None


def test_session_metadata_and_subagents():
    s = load()
    assert s.session_id == "s1"
    assert s.branches == {"ai-staging", "feat/x"}
    assert s.started.tzinfo == timezone.utc
    assert s.started.isoformat().startswith("2026-09-20T10:00:00")
    assert set(s.subagents) == {"a1"}
    assert [e.kind for e in s.subagents["a1"]] == ["human", "assistant"]
    assert all(e.agent_id == "a1" for e in s.subagents["a1"])
    pr = next(e for e in s.events if e.kind == "pr_link")
    assert pr.text == "https://github.com/k/r/pull/7"
    assert all(e.cwd == "/repo/x" for e in s.events if e.kind != "pr_link")


def test_iter_sessions_reads_top_level_files_only():
    sessions = list(iter_sessions([FIX], repo="r"))
    assert [s.session_id for s in sessions] == ["s1"]
