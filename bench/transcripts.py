"""Read Claude Code transcripts into typed events (spec §4)."""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

EventKind = Literal["assistant", "human", "tool_use", "tool_result", "pr_link"]


@dataclass(frozen=True)
class Usage:
    input: int
    cache_write: int
    cache_read: int
    output: int

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input + other.input,
            self.cache_write + other.cache_write,
            self.cache_read + other.cache_read,
            self.output + other.output,
        )

    @property
    def total(self) -> int:
        return self.input + self.cache_write + self.cache_read + self.output


ZERO = Usage(0, 0, 0, 0)


@dataclass(frozen=True)
class Event:
    kind: EventKind
    ts: datetime
    session_id: str
    agent_id: str | None
    uuid: str
    parent_uuid: str | None
    branch: str | None
    cwd: str | None
    model: str | None = None
    effort: str | None = None
    message_id: str | None = None
    request_id: str | None = None
    usage: Usage | None = None  # set on exactly one event per message_id
    text: str | None = None  # human text; assistant text; tool_result text; pr_link url
    tool_name: str | None = None
    tool_use_id: str | None = None
    tool_input: dict | None = None
    tool_result: dict | None = None


@dataclass
class Session:
    session_id: str
    repo: str
    path: Path
    started: datetime
    ended: datetime
    branches: set[str]
    events: list[Event]
    subagents: dict[str, list[Event]]
    skipped_lines: int = 0
    agent_files: dict[str, Path] = field(default_factory=dict)


def parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def is_human(line: dict) -> bool:
    content = (line.get("message") or {}).get("content")
    return (
        line.get("type") == "user"
        and isinstance(content, str)
        and not line.get("isMeta")
        and not content.startswith("<")
    )


def _usage(raw: dict | None) -> Usage | None:
    if not raw:
        return None
    return Usage(
        input=raw.get("input_tokens") or 0,
        cache_write=raw.get("cache_creation_input_tokens") or 0,
        cache_read=raw.get("cache_read_input_tokens") or 0,
        output=raw.get("output_tokens") or 0,
    )


def _block_text(content) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts) if parts else None
    return None


def _events_from_line(line: dict, seen_messages: set[str], final_usage: dict[str, dict]) -> list[Event]:
    typ = line.get("type")
    if "timestamp" not in line:
        return []
    common = dict(
        ts=parse_ts(line["timestamp"]),
        session_id=line.get("sessionId", ""),
        agent_id=line.get("agentId"),
        uuid=line.get("uuid") or f"{typ}:{line['timestamp']}",
        parent_uuid=line.get("parentUuid"),
        branch=line.get("gitBranch") or None,
        cwd=line.get("cwd"),
    )
    if typ == "assistant":
        msg = line.get("message") or {}
        mid = msg.get("id")
        usage = None
        if mid not in seen_messages:
            # Streamed messages repeat usage per line and only the last line is final.
            usage = _usage(final_usage.get(mid) or msg.get("usage"))
            if mid is not None:
                seen_messages.add(mid)
        content = msg.get("content") or []
        text = _block_text(content) if isinstance(content, list) else None
        meta = dict(model=msg.get("model"), effort=line.get("effort"), message_id=mid, request_id=line.get("requestId"))
        out = [Event(kind="assistant", usage=usage, text=text, **meta, **common)]
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                out.append(Event(
                    kind="tool_use", tool_name=block.get("name"), tool_use_id=block.get("id"),
                    tool_input=block.get("input") or {}, **meta, **common,
                ))
        return out
    if typ == "user":
        if is_human(line):
            return [Event(kind="human", text=line["message"]["content"], **common)]
        content = (line.get("message") or {}).get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tur = line.get("toolUseResult")
                    return [Event(
                        kind="tool_result", tool_use_id=block.get("tool_use_id"),
                        tool_result=tur if isinstance(tur, dict) else {},
                        text=_block_text(block.get("content")), **common,
                    )]
        return []
    if typ == "pr-link":
        common["uuid"] = f"pr-link:{line['timestamp']}"
        return [Event(kind="pr_link", text=line.get("prUrl"), **common)]
    return []


def _read_lines(path: Path) -> tuple[list[dict], int]:
    lines, skipped = [], 0
    with path.open() as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(obj, dict):
                lines.append(obj)
            else:
                skipped += 1
    if skipped:
        log.warning("%s: skipped %d unparsable lines", path, skipped)
    return lines, skipped


def _read_events(path: Path) -> tuple[list[Event], int, set[str], str | None]:
    lines, skipped = _read_lines(path)
    seen: set[str] = set()
    final_usage: dict[str, dict] = {}
    for line in lines:
        msg = line.get("message") if line.get("type") == "assistant" else None
        if isinstance(msg, dict) and msg.get("id") and msg.get("usage"):
            final_usage[msg["id"]] = msg["usage"]
    events: list[Event] = []
    branches: set[str] = set()
    agent_id = None
    for line in lines:
        if line.get("gitBranch"):
            branches.add(line["gitBranch"])
        agent_id = agent_id or line.get("agentId")
        events.extend(_events_from_line(line, seen, final_usage))
    return events, skipped, branches, agent_id


def read_session(path: Path, repo: str) -> Session:
    events, skipped, branches, _ = _read_events(path)
    subagents: dict[str, list[Event]] = {}
    agent_files: dict[str, Path] = {}
    sub_dir = path.with_suffix("") / "subagents"
    if sub_dir.is_dir():
        for agent_path in sorted(sub_dir.rglob("agent-*.jsonl")):
            sub_events, sub_skipped, _, agent_id = _read_events(agent_path)
            key = agent_id or agent_path.stem.removeprefix("agent-")
            subagents[key] = sub_events
            agent_files[key] = agent_path
            skipped += sub_skipped
    stamps = [e.ts for e in events] or [datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)]
    session_id = next((e.session_id for e in events if e.session_id), path.stem)
    return Session(
        session_id=session_id, repo=repo, path=path, started=min(stamps), ended=max(stamps),
        branches=branches, events=events, subagents=subagents, skipped_lines=skipped, agent_files=agent_files,
    )


def iter_sessions(dirs: list[Path], repo: str) -> Iterator[Session]:
    for d in dirs:
        for path in sorted(d.glob("*.jsonl")):
            yield read_session(path, repo)
