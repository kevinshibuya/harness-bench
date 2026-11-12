"""Blinded two-leg judge plus consolidation, the same for every arm (spec §8). Manual only."""

import re
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from bench.claude import ISOLATED, ClaudeCall, ClaudeError, parse_json_answer, run_claude_json
from bench.config import PROMPTS, ROOT
from bench.mirrors import mirror_path, run_git
from bench.store import FindingRow, JudgementRow, RecordRow, Store
from bench.types import Blocked

BLINDED_EXCLUDE = ("docs/", "CLAUDE.md", "CONTEXT.md", "HANDOFF", ".claude/")
CODEX_RUN = Path.home() / ".claude" / "bin" / "codex-run.sh"
SEVERITIES = {"blocker", "major", "minor", "not-a-defect"}
LEGS = {"opus", "codex", "both"}
CODEX_TIMEOUT_S = 3600
CODEX_FINDING = re.compile(r"^- \[P(\d)\] (.+?)(?: — (\S+))?\s*$")
P_SEVERITY = {"0": "blocker", "1": "blocker", "2": "major", "3": "minor"}


def _excluded(path: str) -> bool:
    return any(path.startswith(x) or x in path for x in BLINDED_EXCLUDE)


def split_diff(diff_text: str) -> list[tuple[str, str]]:
    """(path, chunk) per file of a unified diff."""
    chunks = re.split(r"(?m)^(?=diff --git )", diff_text)
    out = []
    for chunk in chunks:
        m = re.match(r"diff --git a/(\S+) b/(\S+)", chunk)
        if m:
            out.append((m.group(2), chunk))
    return out


def blinded_input(record: RecordRow, diff_text: str) -> str:
    kept = [chunk.rstrip("\n") for path, chunk in split_diff(diff_text) if not _excluded(path)]
    return f"Task:\n\n{record.raw_ask.strip()}\n\nChange:\n\n" + ("\n".join(kept) if kept else "(empty diff)") + "\n"


def diff_for(record: RecordRow, git=run_git) -> str:
    if record.source == "run":
        return (ROOT / "runs" / record.run_id / "diff.patch").read_text()
    return git(mirror_path(record.repo), "diff", record.base_commit, record.reference_commit)


def codex_argv(record_id: int, input_path: Path) -> list[str]:
    """`exec` in the input's scratch dir: `review` diffs its working directory, which would be the wrong repo."""
    prompt = f"{(PROMPTS / 'judge.md').read_text().strip()} The input is at {input_path}. Do not edit files."
    return [str(CODEX_RUN), f"judge-{record_id}", "-m", "gpt-6-astra", "-c", "model_reasoning_effort=xhigh",
            "-C", str(input_path.parent), "exec", "-s", "read-only", "--skip-git-repo-check", prompt]


def parse_codex_review(text: str) -> list[dict]:
    """JSON when codex returned it, else its `- [Pn] title — where` list format."""
    try:
        found = parse_json_answer(text[text.rfind("\n[") :] if "\n[" in text else text)
        # the log echoes the prompt, whose example answer must not pass for the real one
        if isinstance(found, list) and not any("<one line>" in str(f) for f in found):
            return found
    except ValueError:
        pass
    out: list[dict] = []
    for line in text.splitlines():
        m = CODEX_FINDING.match(line)
        if m:
            out.append({"title": m.group(2), "where": m.group(3) or "", "why": "",
                        "severity_guess": P_SEVERITY.get(m.group(1), "minor")})
        elif out and line.startswith("  ") and line.strip():
            out[-1]["why"] = (out[-1]["why"] + " " + line.strip()).strip()
    # codex prints its review twice (streamed, then final); keep one copy of each finding
    unique = {(f["title"], f["where"]): f for f in out}
    return list(unique.values())


def _claude_list(prompt: str, runner) -> list[dict]:
    call = ClaudeCall(prompt=prompt, model="opus", effort="high", cwd=Path(tempfile.mkdtemp(prefix="bench-judge-")),
                      **ISOLATED)
    result = run_claude_json(call, runner=runner)
    if result.get("is_error"):
        raise ClaudeError(str(result.get("result", ""))[:300])
    found = parse_json_answer(result.get("result", "") or "[]")
    if not isinstance(found, list):
        raise ClaudeError("judge leg did not return a list")
    return found


def _codex_list(record_id: int, blinded: str, runner) -> list[dict]:
    input_path = Path(tempfile.mkdtemp(prefix="bench-judge-")) / f"record-{record_id}.txt"
    input_path.write_text(blinded)
    proc = runner(codex_argv(record_id, input_path), capture_output=True, text=True, timeout=CODEX_TIMEOUT_S)
    m = re.search(r"log=(\S+)", proc.stdout or "")
    if proc.returncode != 0 or not m:
        raise ClaudeError(f"codex leg exited {proc.returncode}: {(proc.stderr or proc.stdout or '')[-300:]}")
    return parse_codex_review(Path(m.group(1)).read_text(errors="replace"))


def judge_record(store: Store, record_id: int, claude_runner=subprocess.run, codex_runner=subprocess.run,
                 git=run_git) -> JudgementRow:
    import json

    record = store.record(record_id)
    if record.source == "history":
        record = replace(record, raw_ask=history_ask(store, record))
    blinded = blinded_input(record, diff_for(record, git=git))
    judge_prompt = (PROMPTS / "judge.md").read_text()
    legs: dict[str, list[dict]] = {}
    failed = False
    try:
        legs["opus"] = _claude_list(f"{judge_prompt}\n\n{blinded}", claude_runner)
    except (ClaudeError, ValueError, OSError):
        failed = True
    try:
        legs["codex"] = _codex_list(record_id, blinded, codex_runner)
    except (ClaudeError, ValueError, OSError, subprocess.TimeoutExpired):
        failed = True
    findings: list[dict] = []
    if any(legs.values()):
        consolidate = (PROMPTS / "consolidate.md").read_text()
        lists = "\n\n".join(f'"{name}":\n{json.dumps(items, ensure_ascii=False)}' for name, items in legs.items())
        try:
            findings = _claude_list(f"{consolidate}\n\n{lists}", claude_runner)
        except (ClaudeError, ValueError, OSError):
            failed = True
    store.delete_findings(record_id, "judge")  # judgement rows stay as the audit trail
    for f in findings:
        severity = f.get("severity") if f.get("severity") in SEVERITIES else None
        leg = f.get("leg") if f.get("leg") in LEGS else None
        text = f"{f.get('title', '')} ({f.get('where', '')}): {f.get('why', '')}".strip()
        store.add_finding(FindingRow(record_id, "judge", None, severity, None, None, text, leg))
    row = JudgementRow(record_id, datetime.now(timezone.utc), ",".join(sorted(legs)), len(findings),
                       "failed" if failed else "ok")
    store.add_judgement(row)
    return row


def history_ask(store: Store, record: RecordRow) -> str:
    """A history record is judged on Kevin's written ask for its whole task, never the mined turn (§9)."""
    tasks = store.tasks()
    match = tasks[(tasks["repo"] == record.repo) & (tasks["pr"] == record.pr) & (tasks["kind"] == "whole")] \
        if len(tasks) else tasks
    if match.empty:
        raise Blocked(f"record of {record.repo}#{record.pr} has no Kevin-written ask: "
                      f"bench tasks add --repo {record.repo} --pr {record.pr} --ask \"...\"")
    return str(match.iloc[0]["raw_ask"])


def judge_cli(store: Store, record_ids: list[int]) -> int:
    code = 0
    for rid in record_ids:
        try:
            j = judge_record(store, rid)
        except Blocked as b:
            print(f"record {rid}: blocked: {b}")
            code = 2
            continue
        print(f"record {rid}: {j.status}, {j.n_findings} findings (legs {j.legs})")
    return code
