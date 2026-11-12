"""Arm and phase segmentation for one PR's attributed session ranges (spec §5)."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from bench.attribute import Attribution, events_in
from bench.prs import PR
from bench.transcripts import ZERO, Event, Session, Usage
from bench.types import Arm

PhaseName = Literal["unassigned", "spec", "plan", "plan_review", "execution", "pr_review"]
ATTENTION_CAP_S = 600.0

A_SKILLS = {"grilling", "grill-with-docs", "grill-me", "writing-plans", "executing-plans"}
GRILL_SKILLS = {"grilling", "grill-with-docs", "grill-me"}
REVIEW = "review"  # resolves to plan_review or pr_review depending on whether execution began
PICKUP = "pickup"

# Marker table, spec §5. Keys: ("skill", name) or ("agent", subagent_type).
MARKERS: dict[tuple[str, str], str] = {
    **{("skill", s): "spec" for s in GRILL_SKILLS},
    ("skill", "writing-plans"): "plan",
    ("skill", "executing-plans"): "execution",
    ("skill", "codex-review"): REVIEW,
    ("agent", "reviewer"): REVIEW,
    ("skill", "pickup"): PICKUP,
}
NO_PIPELINE = {"Aprime", "V"}  # arms whose execution opens at the first source edit
SOURCE_EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path"}


@dataclass
class Phase:
    name: PhaseName
    start: datetime
    end: datetime
    wall_s: float
    usage_by_model: dict[str, Usage]
    human_turns: int
    attention_s: float
    agent_ids: list[str] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    # (session_id, start, end) per window; start..end of a recurring phase overlaps other phases
    windows: list[tuple[str, datetime, datetime]] = field(default_factory=list)

    def contains(self, ts: datetime, session_id: str | None = None) -> bool:
        return any(start <= ts <= end and session_id in (None, sid) for sid, start, end in self.windows)


@dataclass(frozen=True)
class PhaseOverride:
    session_id: str
    start_uuid: str
    phase: PhaseName


def _skill(e: Event) -> str | None:
    if e.kind == "tool_use" and e.tool_name == "Skill":
        return str((e.tool_input or {}).get("skill", "")).split(":")[-1]
    return None


def arm_for(sessions: list[tuple[Session, Attribution]], pr: PR) -> Arm:
    for session, att in sessions:
        if any(_skill(e) in A_SKILLS for e in events_in(session, att)):
            return "A"
    for f in pr.changed_files:
        if f.status == "A" and f.path.startswith("docs/") and ("spec" in f.path or "plan" in f.path):
            return "A"
    return "Aprime"


def _marker(e: Event, arm: Arm, execution_began: bool) -> str | None:
    if e.kind != "tool_use":
        return None
    skill = _skill(e)
    if skill is not None:
        m = MARKERS.get(("skill", skill))
    elif e.tool_name == "Agent":
        m = MARKERS.get(("agent", str((e.tool_input or {}).get("subagent_type", ""))))
    elif arm in NO_PIPELINE and not execution_began and e.tool_name in SOURCE_EDIT_TOOLS:
        path = str((e.tool_input or {}).get(SOURCE_EDIT_TOOLS[e.tool_name], ""))
        return "execution" if path and "/docs/" not in f"/{path}" else None
    else:
        return None
    if m == REVIEW:
        return "pr_review" if (arm in NO_PIPELINE or execution_began) else "plan_review"
    return m


def _message_start(events: list[Event], i: int) -> int:
    """Back up to the first event of the API message that holds event i."""
    mid = events[i].message_id
    j = i
    while mid is not None and j > 0 and events[j - 1].message_id == mid:
        j -= 1
    return j


@dataclass
class _Window:
    name: PhaseName
    session: Session
    start: int
    end: int  # exclusive
    until: datetime  # the window's end time


def _windows(
    ranges: list[tuple[Session, list[Event]]], arm: Arm, overrides: Iterable[PhaseOverride]
) -> list[tuple[_Window, list[Event]]]:
    by_key = {(o.session_id, o.start_uuid): o.phase for o in overrides}
    out: list[tuple[_Window, list[Event]]] = []
    open_phase: PhaseName | None = None
    execution_began = False
    for session, events in ranges:
        cuts: list[tuple[int, PhaseName]] = [(0, "unassigned")]
        for i, e in enumerate(events):
            name: str | None = by_key.get((session.session_id, e.uuid))
            if name is None:
                name = _marker(e, arm, execution_began)
            if name == PICKUP:
                name = open_phase or cuts[-1][1]
            if name is None or name == cuts[-1][1]:
                continue
            start = _message_start(events, i) if i > 0 else 0
            start = max(start, cuts[-1][0])
            if start == cuts[-1][0]:
                cuts[-1] = (start, name)  # the open window is empty: rename instead of splitting
            else:
                cuts.append((start, name))
            if name == "execution":
                execution_began = True
        for k, (start, name) in enumerate(cuts):
            end = cuts[k + 1][0] if k + 1 < len(cuts) else len(events)
            until = events[end].ts if end < len(events) else events[-1].ts
            out.append((_Window(name, session, start, end, until), events))
        open_phase = cuts[-1][1]
    return out


def unassigned_windows(
    sessions: list[tuple[Session, Attribution]], arm: Arm, overrides: Iterable[PhaseOverride] = ()
) -> list[tuple[Session, list[Event]]]:
    """Each non-empty unassigned window, per session, for the model pass."""
    out = []
    for window, events in _windows(_ranges(sessions), arm, overrides):
        chunk = events[window.start : window.end]
        if window.name == "unassigned" and chunk:
            out.append((window.session, chunk))
    return out


def _ranges(sessions: list[tuple[Session, Attribution]]) -> list[tuple[Session, list[Event]]]:
    ranges = sorted(
        ((s, events_in(s, a)) for s, a in sessions),
        key=lambda r: r[1][0].ts if r[1] else r[0].started,
    )
    return [(s, evs) for s, evs in ranges if evs]


def _subagent_usage(session: Session, agent_id: str, seen: set[str], ids: list[str], usage: dict[str, Usage]) -> None:
    if agent_id in seen or agent_id not in session.subagents:
        return
    seen.add(agent_id)
    ids.append(agent_id)
    events = session.subagents[agent_id]
    for e in events:
        if e.usage is not None and e.model:
            usage[e.model] = usage.get(e.model, ZERO) + e.usage
    for child in _dispatched_agents(events, events):
        _subagent_usage(session, child, seen, ids, usage)


def _dispatched_agents(window: list[Event], all_events: list[Event]) -> list[str]:
    """Agent ids dispatched by Agent tool_uses inside the window, joined through their tool_result."""
    results = {
        e.tool_use_id: (e.tool_result or {}).get("agentId")
        for e in all_events if e.kind == "tool_result" and e.tool_use_id
    }
    out = []
    for e in window:
        if e.kind == "tool_use" and e.tool_name == "Agent":
            agent = results.get(e.tool_use_id)
            if agent:
                out.append(agent)
    return out


def segment(
    sessions: list[tuple[Session, Attribution]], arm: Arm, overrides: Iterable[PhaseOverride] = ()
) -> list[Phase]:
    ranges = _ranges(sessions)
    phases: dict[str, Phase] = {}
    seen_agents: set[str] = set()
    seen_messages: set[str] = set()  # a resumed session can repeat an earlier message
    for window, events in _windows(ranges, arm, overrides):
        chunk = events[window.start : window.end]
        if not chunk:
            continue
        session = window.session
        usage: dict[str, Usage] = {}
        for e in chunk:
            if e.kind == "assistant" and e.usage is not None and e.model:
                if e.message_id is not None:
                    if e.message_id in seen_messages:
                        continue
                    seen_messages.add(e.message_id)
                usage[e.model] = usage.get(e.model, ZERO) + e.usage
        agent_ids: list[str] = []
        for agent in _dispatched_agents(chunk, session.events):
            _subagent_usage(session, agent, seen_agents, agent_ids, usage)
        humans, attention = 0, 0.0
        for i, e in enumerate(chunk):
            if e.kind != "human":
                continue
            humans += 1
            idx = window.start + i
            prev = next((p for p in reversed(events[:idx]) if p.kind == "assistant"), None)
            if prev is None:
                prev = next((p for p in reversed(session.events) if p.kind == "assistant" and p.ts < e.ts), None)
            if prev is not None:
                attention += min((e.ts - prev.ts).total_seconds(), ATTENTION_CAP_S)
        wall = (window.until - chunk[0].ts).total_seconds()
        p = phases.get(window.name)
        if p is None:
            phases[window.name] = Phase(
                window.name, chunk[0].ts, window.until, wall, usage, humans, attention,
                agent_ids, [session.session_id], [(session.session_id, chunk[0].ts, window.until)],
            )
        else:
            p.windows.append((session.session_id, chunk[0].ts, window.until))
            p.start, p.end = min(p.start, chunk[0].ts), max(p.end, window.until)
            p.wall_s += wall
            for model, u in usage.items():
                p.usage_by_model[model] = p.usage_by_model.get(model, ZERO) + u
            p.human_turns += humans
            p.attention_s += attention
            p.agent_ids += agent_ids
            if session.session_id not in p.session_ids:
                p.session_ids.append(session.session_id)
    return sorted(phases.values(), key=lambda p: p.start)
