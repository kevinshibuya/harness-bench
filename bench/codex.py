"""Codex runs from ~/.claude/codex-runs/registry.tsv, matched to transcript windows."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bench.attribute import Attribution, events_in
from bench.phases import Phase
from bench.transcripts import ZERO, Event, Session, Usage

log = logging.getLogger(__name__)
REGISTRY = Path.home() / ".claude" / "codex-runs" / "registry.tsv"
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class CodexRun:
    ts: datetime  # UTC
    model: str
    exit_code: int
    duration_s: float
    log_path: Path
    description: str
    label: str | None
    input_tokens: int | None
    output_tokens: int | None


def _label(description: str) -> str | None:
    tokens = description.split()
    if "codex-run.sh" in description:
        for i, tok in enumerate(tokens):
            if tok.endswith("codex-run.sh") and i + 1 < len(tokens):
                return tokens[i + 1]
    return tokens[0].rstrip(":") if tokens else None


def _int(raw: str) -> int | None:
    raw = raw.strip()
    return int(raw) if raw.isdigit() else None


def load_registry(path: Path = REGISTRY) -> list[CodexRun]:
    if not path.exists():
        return []
    runs = []
    for line in path.read_text(errors="replace").splitlines():
        cols = line.split("\t")
        if len(cols) < 7:
            continue
        try:
            ts = datetime.fromisoformat(cols[0]).replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc)
            exit_code = int(cols[3])
        except ValueError:
            continue
        runs.append(CodexRun(
            ts=ts, model=cols[2], exit_code=exit_code, duration_s=float(cols[4].rstrip("s") or 0),
            log_path=Path(cols[5]), description=cols[6], label=_label(cols[6]),
            input_tokens=_int(cols[7]) if len(cols) > 7 else None,
            output_tokens=_int(cols[8]) if len(cols) > 8 else None,
        ))
    return runs


def _bash_commands(events: list[Event]) -> list[str]:
    return [
        str((e.tool_input or {}).get("command", ""))
        for e in events if e.kind == "tool_use" and e.tool_name == "Bash"
    ]


LOG_IN_OUTPUT = re.compile(r"log=(\S+\.log)")


def codex_runs_in(window: tuple[datetime, datetime], events: list[Event], registry: list[CodexRun]) -> list[CodexRun]:
    """Runs whose log path the wrapper printed into a Bash result here; label and time only
    for calls whose result names no log (e.g. a backgrounded call)."""
    start, end = window
    results = {e.tool_use_id: e.text or "" for e in events if e.kind == "tool_result" and e.tool_use_id}
    logs: set[str] = set()
    unresolved: list[str] = []
    for e in events:
        if e.kind != "tool_use" or e.tool_name != "Bash":
            continue
        command = str((e.tool_input or {}).get("command", ""))
        if "codex-run.sh" not in command:
            continue
        found = LOG_IN_OUTPUT.findall(results.get(e.tool_use_id, ""))
        if found:
            logs.update(found)
        else:
            unresolved.append(command)
    by_log = [r for r in registry if str(r.log_path) in logs]
    by_label = [
        r for r in registry
        if r not in by_log and r.label and start <= r.ts <= end and any(r.label in c.split() for c in unresolved)
    ]
    return by_log + by_label


def usage_of(run: CodexRun) -> Usage:
    return Usage(input=run.input_tokens or 0, cache_write=0, cache_read=0, output=run.output_tokens or 0)


def codex_hook(registry: list[CodexRun], priced=lambda model: True):
    """For extract_repo: add each matched codex run's usage to the narrowest phase containing it.
    Runs whose model has no price (e.g. "default", when -m was omitted) are skipped with a warning."""

    def hook(phases: list[Phase], sessions: list[tuple[Session, Attribution]]) -> None:
        if not phases:
            return
        events = [e for s, a in sessions for e in events_in(s, a)]
        span = (min(p.start for p in phases), max(p.end for p in phases))
        for run in codex_runs_in(span, events, registry):
            if not priced(run.model):
                log.warning("codex run %s: model %r has no price; skipped", run.label, run.model)
                continue
            holders = [
                (end - start, p) for p in phases for _, start, end in p.windows if start <= run.ts <= end
            ]
            if not holders:
                continue
            phase = min(holders, key=lambda h: h[0])[1]
            phase.usage_by_model[run.model] = phase.usage_by_model.get(run.model, ZERO) + usage_of(run)

    return hook
