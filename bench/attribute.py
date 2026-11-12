"""Attribute session ranges to PRs (spec §5, rules 1 to 3; rule 4 is the model pass)."""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from bench.config import RepoConfig
from bench.prs import PR
from bench.transcripts import Event, Session

Rule = Literal["branch", "pr_link", "window", "model_pass", "none"]
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
PR_URL = re.compile(r"/pull/(\d+)")


@dataclass(frozen=True)
class Attribution:
    session_id: str
    pr_number: int | None
    rule: Rule
    start_uuid: str  # inclusive, contiguous range of main-session events
    end_uuid: str


@dataclass
class _Segment:
    start: int  # index into session.events, inclusive
    end: int  # exclusive
    branch: str | None
    pr: int | None = None
    rule: Rule = "none"


def events_in(session: Session, att: Attribution) -> list[Event]:
    """The attribution's events. One transcript line can yield several events sharing a uuid."""
    uuids = [e.uuid for e in session.events]
    start = uuids.index(att.start_uuid)
    end = len(uuids) - 1 - uuids[::-1].index(att.end_uuid)
    return session.events[start : end + 1]


def repo_relative(path: str, cwd: str | None, repo: RepoConfig) -> str | None:
    """Strip the repo root or a `.claude/worktrees/<name>` root; None when outside the repo."""
    if not os.path.isabs(path):
        if not cwd:
            return None
        path = os.path.join(cwd, path)
    path = os.path.normpath(path)
    root = os.path.normpath(str(repo.path))
    if not path.startswith(root + os.sep):
        return None
    rel = path[len(root) + 1 :]
    parts = rel.split(os.sep)
    if parts[:2] == [".claude", "worktrees"]:
        if len(parts) < 4:
            return None
        rel = os.sep.join(parts[3:])
    return rel


def _segments(events: list[Event]) -> list[_Segment]:
    """Cut at every branch change; the last human turn before a change opens the next segment."""
    segs: list[_Segment] = []
    branch = None
    last_human = None  # index of the last human event in the current segment
    start = 0
    for i, e in enumerate(events):
        b = e.branch or branch
        if branch is not None and b != branch:
            cut = last_human if last_human is not None and last_human > start else i
            segs.append(_Segment(start, cut, branch))
            start = cut
            last_human = None if cut == i else last_human
        if e.kind == "human":
            last_human = i
        branch = b
    segs.append(_Segment(start, len(events), branch))
    return [s for s in segs if s.end > s.start]


def _span(events: list[Event], seg: _Segment) -> tuple[datetime, datetime]:
    return events[seg.start].ts, events[seg.end - 1].ts


def _branch_match(seg: _Segment, span: tuple[datetime, datetime], prs: list[PR]) -> int | None:
    candidates = [p for p in prs if p.head_branch == seg.branch]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].number
    start, end = span
    inside = [p for p in candidates if p.first_commit_at <= end and start <= p.merged_at]
    pool = inside or [p for p in candidates if p.merged_at >= start] or candidates
    return min(pool, key=lambda p: p.merged_at).number


def _edited_paths(events: list[Event], repo: RepoConfig) -> set[str]:
    out = set()
    for e in events:
        key = EDIT_TOOLS.get(e.tool_name or "")
        if e.kind == "tool_use" and key and (e.tool_input or {}).get(key):
            rel = repo_relative(e.tool_input[key], e.cwd, repo)
            if rel:
                out.add(rel)
    return out


def attribute(session: Session, prs: list[PR], repo: RepoConfig) -> list[Attribution]:
    events = session.events
    if not events:
        return []
    segs = _segments(events)
    known = {p.number for p in prs}

    # Rule 1: branch, with leading unmatched segments joining the first match
    # and unmatched segments between matches joining the preceding one.
    for seg in segs:
        n = _branch_match(seg, _span(events, seg), prs)
        if n is not None:
            seg.pr, seg.rule = n, "branch"
    matched = [i for i, s in enumerate(segs) if s.rule == "branch"]
    if matched:
        first, last = matched[0], matched[-1]
        for i in range(first):
            segs[i].pr, segs[i].rule = segs[first].pr, "branch"
        for i in range(first + 1, last):
            if segs[i].rule == "none":
                segs[i].pr, segs[i].rule = segs[i - 1].pr, "branch"

    # Rule 2: a pr-link names the PR of the segment it sits in.
    for seg in segs:
        if seg.rule != "none":
            continue
        for e in events[seg.start : seg.end]:
            m = e.kind == "pr_link" and PR_URL.search(e.text or "")
            if m and int(m.group(1)) in known:
                seg.pr, seg.rule = int(m.group(1)), "pr_link"
                break

    # Rule 3: the segment overlaps exactly one PR window whose changed files it edited.
    for seg in segs:
        if seg.rule != "none":
            continue
        edited = _edited_paths(events[seg.start : seg.end], repo)
        if not edited:
            continue
        start, end = _span(events, seg)
        hits = [
            p for p in prs
            if p.first_commit_at <= end and start <= p.merged_at
            and edited & {f.path for f in p.changed_files}
        ]
        if len(hits) == 1:
            seg.pr, seg.rule = hits[0].number, "window"

    # Merge neighbours with the same outcome into contiguous ranges.
    ranges: list[_Segment] = []
    for seg in segs:
        if ranges and (ranges[-1].pr, ranges[-1].rule) == (seg.pr, seg.rule):
            ranges[-1].end = seg.end
        else:
            ranges.append(_Segment(seg.start, seg.end, seg.branch, seg.pr, seg.rule))
    return [
        Attribution(session.session_id, r.pr, r.rule, events[r.start].uuid, events[r.end - 1].uuid)
        for r in ranges
    ]
