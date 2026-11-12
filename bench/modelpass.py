"""Rule 4 attribution and phase labels for unassigned prefixes, by an isolated Opus call (spec §5).

Manual only: behind `bench extract --model-pass`, never scheduled.
"""

import subprocess
from pathlib import Path

from bench.archive import archived_dirs
from bench.attribute import Attribution, attribute
from bench.claude import ISOLATED, ClaudeCall, parse_json_answer, run_claude_json
from bench.config import PROMPTS, RepoConfig
from bench.extract import CUTOFF
from bench.phases import PhaseOverride, unassigned_windows
from bench.prs import PR
from bench.store import Store
from bench.transcripts import Event, Session, iter_sessions
from bench.types import Arm

PHASES = {"unassigned", "spec", "plan", "plan_review", "execution", "pr_review"}
MIN_HUMAN_TURNS = 5
MIN_TOKEN_SHARE = 0.10


def _ask(prompt: str, runner) -> dict:
    call = ClaudeCall(prompt=prompt, model="opus", effort="high", **ISOLATED)
    result = run_claude_json(call, runner=runner)
    answer = parse_json_answer(result.get("result", ""))
    return answer if isinstance(answer, dict) else {}


def _transcript_lines(events: list[Event], tools: bool) -> list[str]:
    lines = []
    for e in events:
        stamp = e.ts.strftime("%Y-%m-%d %H:%M")
        if e.kind == "human":
            lines.append(f"[{stamp}] HUMAN: {(e.text or '')[:1500]}")
        elif e.kind == "tool_use" and e.tool_name == "Skill":
            inp = e.tool_input or {}
            lines.append(f"[{stamp}] SKILL: {inp.get('skill')} {str(inp.get('args') or '')[:300]}")
        elif tools and e.kind == "tool_use":
            lines.append(f"[{stamp}] TOOL: {e.tool_name}")
    return lines


def attribution_prompt(session: Session, prs: list[PR]) -> str:
    pr_lines = [
        f"#{p.number} {p.title} (branch {p.head_branch}, {p.first_commit_at:%Y-%m-%d} to {p.merged_at:%Y-%m-%d})"
        for p in prs
    ]
    return "\n".join([
        (PROMPTS / "attribute.md").read_text(), "", "Pull requests:", *pr_lines, "",
        f"Session {session.session_id}, {session.started:%Y-%m-%d %H:%M} to {session.ended:%Y-%m-%d %H:%M}:",
        *_transcript_lines(session.events, tools=False),
    ])


def resolve_unattributed(
    repo: RepoConfig, store: Store, prs: list[PR], archive_root: Path, runner=subprocess.run
) -> list[Attribution]:
    known = {p.number for p in prs}
    done = store.model_pass_attributions()
    out = []
    for session in iter_sessions(archived_dirs(archive_root, repo.name), repo.name):
        if (not session.events or session.ended < CUTOFF or session.session_id in done
                or session.session_id in repo.exclude_sessions):
            continue
        if any(a.pr_number is not None for a in attribute(session, prs, repo)):
            continue
        answer = _ask(attribution_prompt(session, prs), runner)
        pr = answer.get("pr")
        pr = pr if isinstance(pr, int) and pr in known else None
        att = Attribution(session.session_id, pr, "model_pass" if pr else "none",
                          session.events[0].uuid, session.events[-1].uuid)
        store.add_model_pass(att, str(answer.get("why", "")))
        if pr:
            out.append(att)
    return out


def phase_prompt(events: list[Event]) -> str:
    return "\n".join([(PROMPTS / "phase_prefix.md").read_text(), "", *_transcript_lines(events, tools=True)])


def resolve_phase_prefixes(
    record_id: int, sessions: list[tuple[Session, Attribution]], store: Store, runner=subprocess.run,
    arm: Arm = "A",
) -> list[PhaseOverride]:
    overrides = [o for sid in {s.session_id for s, _ in sessions} for o in store.phase_overrides(sid)]
    windows = unassigned_windows(sessions, arm, overrides)
    record_tokens = sum(
        e.usage.total for s, _ in sessions for e in s.events if e.kind == "assistant" and e.usage
    ) or 1
    out = []
    for session, chunk in windows:
        humans = sum(1 for e in chunk if e.kind == "human")
        tokens = sum(e.usage.total for e in chunk if e.kind == "assistant" and e.usage)
        if humans <= MIN_HUMAN_TURNS and tokens <= MIN_TOKEN_SHARE * record_tokens:
            continue
        answer = _ask(phase_prompt(chunk), runner)
        phase = answer.get("phase")
        if phase in PHASES and phase != "unassigned":
            o = PhaseOverride(session.session_id, chunk[0].uuid, phase)
            store.add_phase_override(o)
            out.append(o)
    return out
