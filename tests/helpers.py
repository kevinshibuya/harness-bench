"""Builders for in-memory events, sessions and PRs."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from bench.config import RepoConfig
from bench.prs import PR, ChangedFile
from bench.transcripts import Event, Session, Usage

T0 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
REPO = RepoConfig("r", Path("/repo/r"), "main", "ai-staging", None, "true")


class Builder:
    """Appends events to one session, a minute apart unless told otherwise."""

    def __init__(self, session_id="s1", start=T0, branch="ai-staging", cwd="/repo/r"):
        self.sid, self.t, self.branch, self.cwd = session_id, start, branch, cwd
        self.events: list[Event] = []
        self.subagents: dict[str, list[Event]] = {}
        self.n = 0

    def _ev(self, kind, gap=60, **kw):
        self.t += timedelta(seconds=gap)
        self.n += 1
        e = Event(kind=kind, ts=self.t, session_id=self.sid, agent_id=None, uuid=f"{self.sid}-{self.n}",
                  parent_uuid=None, branch=self.branch, cwd=self.cwd, **kw)
        self.events.append(e)
        return e

    def human(self, text="go", gap=60):
        return self._ev("human", gap, text=text)

    def assistant(self, gap=60, model="claude-opus-5-5", usage=Usage(10, 0, 0, 5), text=None):
        self.n += 0
        return self._ev("assistant", gap, model=model, message_id=f"m{self.n + 1}", usage=usage, text=text)

    def tool(self, name, gap=5, **tool_input):
        return self._ev("tool_use", gap, tool_name=name, tool_use_id=f"t{self.n + 1}", tool_input=tool_input,
                        model="claude-opus-5-5")

    def skill(self, name, gap=5):
        return self.tool("Skill", gap, skill=name)

    def result(self, tool_use_id, gap=5, text=None, **tur):
        return self._ev("tool_result", gap, tool_use_id=tool_use_id, tool_result=tur, text=text)

    def pr_link(self, n, gap=5):
        return self._ev("pr_link", gap, text=f"https://github.com/k/r/pull/{n}")

    def agent(self, agent_id, subagent_type="reviewer", finish_gap=60, usage=Usage(100, 0, 0, 50), model="claude-fable-5-1"):
        """Dispatch a subagent; its result lands finish_gap seconds later (call .result yourself to delay)."""
        use = self.tool("Agent", subagent_type=subagent_type)
        self.subagents[agent_id] = [Event(
            kind="assistant", ts=self.t + timedelta(seconds=1), session_id=self.sid, agent_id=agent_id,
            uuid=f"{agent_id}-1", parent_uuid=None, branch=self.branch, cwd=self.cwd,
            model=model, message_id=f"{agent_id}-m1", usage=usage, text=f"{agent_id} final",
        )]
        return use

    def session(self) -> Session:
        stamps = [e.ts for e in self.events]
        return Session(self.sid, "r", Path(f"/x/{self.sid}.jsonl"), min(stamps), max(stamps),
                       {e.branch for e in self.events if e.branch}, list(self.events), dict(self.subagents))


def pr(number, head, first=T0 - timedelta(days=1), merged=T0 + timedelta(days=1), files=(("src/a.ts", "M"),),
       additions=100, deletions=10):
    return PR("r", number, f"PR {number}", head, "ai-staging", merged, f"m{number}", f"b{number}", f"h{number}",
              first, (f"c{number}",), additions, deletions, tuple(ChangedFile(p, s) for p, s in files))
