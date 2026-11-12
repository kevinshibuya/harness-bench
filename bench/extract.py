"""History extraction: archive → sessions → attribution → phases → prices → store (spec §3 to §5)."""

import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from bench.archive import archived_dirs
from bench.attribute import Attribution, attribute, events_in
from bench.config import RESULTS, RepoConfig
from bench.phases import Phase, arm_for, segment
from bench.price import Prices, cost_usd
from bench.prs import PR
from bench.store import CoverageRow, RecordRow, Store, stratum_for
from bench.transcripts import Session, iter_sessions

EXTRACTOR_VERSION = "2"
CUTOFF = datetime(2026, 9, 1, tzinfo=timezone.utc)
HARNESS_REPO = Path.home() / ".claude"
UNATTRIBUTED_DIR = RESULTS / "unattributed"


@dataclass
class ExtractSummary:
    records_upserted: int
    sessions_seen: int
    sessions_unattributed: int
    sessions_skipped_old: int
    sessions_excluded: int = 0
    groups: dict[int, list[tuple[Session, Attribution]]] = field(default_factory=dict, repr=False)
    arms: dict[int, str] = field(default_factory=dict, repr=False)


@lru_cache(maxsize=None)
def harness_version_at(ts: datetime) -> str:
    out = subprocess.run(
        ["git", "-C", str(HARNESS_REPO), "log", "-1", f"--before={ts.isoformat()}", "--format=%h"],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


def raw_ask_for(sessions: list[tuple[Session, Attribution]]) -> str:
    ranges = sorted((events_in(s, a) for s, a in sessions), key=lambda evs: evs[0].ts)
    for events in ranges:
        for e in events:
            if e.kind == "human":
                return e.text or ""
    return ""


def phase_costs(phases: list[Phase], prices: Prices) -> dict[str, float]:
    return {p.name: sum(cost_usd(m, u, prices) for m, u in p.usage_by_model.items()) for p in phases}


def models_in(phases: list[Phase]) -> list[str]:
    return sorted({m for p in phases for m in p.usage_by_model if m and m != "<synthetic>"})


def _first_human(session: Session) -> str:
    return next((e.text or "" for e in session.events if e.kind == "human"), "")[:200]


def extract_repo(
    repo: RepoConfig, store: Store, prs: list[PR], archive_root: Path, prices: Prices,
    harness_version=harness_version_at, unattributed_dir: Path = UNATTRIBUTED_DIR,
    extra_attributions: dict[str, list[Attribution]] | None = None, codex=None,
) -> ExtractSummary:
    """Read only from the archive. extra_attributions: model-pass results keyed by session id.
    codex: optional callable(phases, sessions) that adds codex usage to phases in place."""
    prs = [p for p in prs if p.merged_at >= CUTOFF]
    by_number = {p.number: p for p in prs}
    extra = extra_attributions or {}
    grouped: dict[int, list[tuple[Session, Attribution]]] = defaultdict(list)
    seen = unattributed = skipped = excluded = 0
    coverage: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    unlisted = []
    for session in iter_sessions(archived_dirs(archive_root, repo.name), repo.name):
        if not session.events or session.ended < CUTOFF:
            skipped += 1
            continue
        month = session.started.strftime("%Y-%m")
        if session.session_id in repo.exclude_sessions:
            excluded += 1
            coverage[month][2] += 1
            continue
        seen += 1
        coverage[month][0] += 1
        atts = attribute(session, prs, repo)
        if all(a.rule == "none" for a in atts) and session.session_id in extra:
            atts = extra[session.session_id]
        if all(a.pr_number is None for a in atts):
            unattributed += 1
            coverage[month][1] += 1
            unlisted.append({
                "session_id": session.session_id, "start": session.started.isoformat(),
                "end": session.ended.isoformat(), "branches": sorted(session.branches),
                "first_human_turn": _first_human(session),
            })
        for a in atts:
            if a.pr_number in by_number:
                grouped[a.pr_number].append((session, a))

    upserted = 0
    arms: dict[int, str] = {}
    for number, sessions in sorted(grouped.items()):
        pr = by_number[number]
        arm = arms[number] = arm_for(sessions, pr)
        overrides = [o for sid in {s.session_id for s, _ in sessions} for o in store.phase_overrides(sid)]
        phases = segment(sessions, arm, overrides)
        if codex is not None:
            codex(phases, sessions)
        first = min(events_in(s, a)[0].ts for s, a in sessions)
        record_id = store.upsert_record(RecordRow(
            repo=repo.name, pr=number, arm=arm, source="history", run_id="", title=pr.title,
            head_branch=pr.head_branch, base_commit=pr.base_commit, reference_commit=pr.merge_commit,
            merged_at=pr.merged_at, additions=pr.additions, deletions=pr.deletions,
            files_changed=len(pr.changed_files), stratum=stratum_for(pr.changed_lines), kind="whole",
            harness_version=harness_version(first), models=models_in(phases), raw_ask=raw_ask_for(sessions),
            outcome="merged", test_gate="n/a", extractor_version=EXTRACTOR_VERSION,
        ))
        store.replace_phases(record_id, phases, phase_costs(phases, prices))
        store.replace_sessions(record_id, [a for _, a in sessions])
        upserted += 1

    for month, (n_seen, n_un, n_ex) in coverage.items():
        store.upsert_coverage(CoverageRow(repo.name, month, n_seen, n_un, n_ex))
    unattributed_dir.mkdir(parents=True, exist_ok=True)
    (unattributed_dir / f"{repo.name}.json").write_text(json.dumps(unlisted, indent=1, ensure_ascii=False) + "\n")
    return ExtractSummary(upserted, seen, unattributed, skipped, excluded, dict(grouped), arms)
