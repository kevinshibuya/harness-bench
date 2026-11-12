"""Findings the pipeline's own reviews raised and confirmed, mined from history (spec §8). Manual only."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bench.attribute import Attribution, events_in
from bench.claude import ISOLATED, ClaudeCall, parse_json_answer, run_claude_json
from bench.codex import CodexRun, codex_runs_in, load_registry
from bench.config import ARCHIVE, PROMPTS
from bench.phases import Phase, segment
from bench.store import FindingRow, Store
from bench.transcripts import Event, Session

REVIEW_STAGES = ("plan_review", "pr_review")
STATUS = {"confirmed": True, "dismissed": False, "left": None}
MAX_TEXT = 60_000


@dataclass
class Segment:
    stage: str
    main_text: str
    subagent_outputs: list[str] = field(default_factory=list)
    codex_outputs: list[str] = field(default_factory=list)


def _final_message(events: list[Event]) -> str | None:
    return next((e.text for e in reversed(events) if e.kind == "assistant" and e.text), None)


CODEX_ARCHIVE = ARCHIVE / "codex-runs"


def _codex_output(run: CodexRun, events: list[Event], codex_archive: Path = CODEX_ARCHIVE) -> str | None:
    for path in (run.log_path, codex_archive / run.log_path.name):
        if path.exists():
            return path.read_text(errors="replace")
    for use in events:
        if use.kind == "tool_use" and use.tool_name == "Bash" and run.label in str((use.tool_input or {}).get("command", "")).split():
            result = next((e for e in events if e.kind == "tool_result" and e.tool_use_id == use.tool_use_id), None)
            if result and result.text:
                return result.text
    return None


def review_segments(
    sessions: list[tuple[Session, Attribution]], phases: list[Phase], registry: list[CodexRun],
    codex_archive: Path = CODEX_ARCHIVE,
) -> list[Segment]:
    out = []
    for p in phases:
        if p.name not in REVIEW_STAGES:
            continue
        events = [
            e for s, a in sessions if s.session_id in p.session_ids
            for e in events_in(s, a) if p.contains(e.ts, s.session_id)
        ]
        lines = []
        for e in events:
            if e.kind == "human":
                lines.append(f"HUMAN: {e.text}")
            elif e.kind == "assistant" and e.text:
                lines.append(f"MAIN: {e.text}")
        subagents = {aid: evs for s, _ in sessions for aid, evs in s.subagents.items()}
        outputs = [m for aid in p.agent_ids if (m := _final_message(subagents.get(aid, [])))]
        all_events = [e for s, a in sessions for e in events_in(s, a)]
        codex = [o for run in codex_runs_in((p.start, p.end), events, registry) if (o := _codex_output(run, all_events, codex_archive))]
        out.append(Segment(p.name, "\n".join(lines), outputs, codex))
    return out


def caught_prompt(seg: Segment) -> str:
    parts = [(PROMPTS / "caught.md").read_text(), f"Stage: {seg.stage}", "", "Main session:", seg.main_text[:MAX_TEXT]]
    for i, o in enumerate(seg.subagent_outputs, 1):
        parts += ["", f"Review agent {i} final report:", o[:MAX_TEXT]]
    for i, o in enumerate(seg.codex_outputs, 1):
        parts += ["", f"Codex review {i} output:", o[-MAX_TEXT:]]
    return "\n".join(parts)


def extract_caught(
    store: Store, record_id: int, sessions: list[tuple[Session, Attribution]], runner=subprocess.run,
    registry: list[CodexRun] | None = None, codex_archive: Path = CODEX_ARCHIVE,
) -> list[FindingRow]:
    record = store.record(record_id)
    overrides = [o for sid in {s.session_id for s, _ in sessions} for o in store.phase_overrides(sid)]
    phases = segment(sessions, record.arm, overrides)
    store.delete_findings(record_id, "in_process")
    rows = []
    for seg in review_segments(sessions, phases, load_registry() if registry is None else registry, codex_archive):
        if not (seg.subagent_outputs or seg.codex_outputs or seg.main_text):
            continue
        call = ClaudeCall(prompt=caught_prompt(seg), model="opus", effort="high", **ISOLATED)
        found = parse_json_answer(run_claude_json(call, runner=runner).get("result", "") or "[]")
        for f in found if isinstance(found, list) else []:
            status = str(f.get("status", "left"))
            source = f.get("source") if f.get("source") in {"reviewer", "codex", "main"} else None
            row = FindingRow(record_id, "in_process", seg.stage, None, STATUS.get(status), None,
                             str(f.get("finding", "")), source)
            store.add_finding(row)
            rows.append(row)
    return rows
