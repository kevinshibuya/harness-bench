"""SQLite store for records and everything hanging off them (spec §3, §11)."""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd

from bench.attribute import Attribution
from bench.config import RESULTS
from bench.phases import Phase, PhaseOverride
from bench.types import Arm, Source

SCHEMA_VERSION = "1"
STORE_PATH = RESULTS / "bench.sqlite"
STRICT_PREFIXES = ("claude-opus-5-5", "claude-fable-5-1", "claude-haiku-4-5")
View = Literal["loosened", "strict", "all"]


@dataclass
class RecordRow:
    repo: str
    pr: int
    arm: Arm
    source: Source
    run_id: str  # "" for history
    title: str
    head_branch: str
    base_commit: str
    reference_commit: str
    merged_at: datetime | None
    additions: int
    deletions: int
    files_changed: int
    stratum: str
    kind: Literal["whole", "slice"]
    harness_version: str | None
    models: list[str]
    raw_ask: str
    outcome: Literal["merged", "done", "stalled", "failed"]
    test_gate: Literal["pass", "fail", "n/a"]
    extractor_version: str


@dataclass
class FindingRow:
    record_id: int
    origin: Literal["judge", "in_process"]
    stage: str | None
    severity: str | None
    confirmed: bool | None
    verified: bool | None
    text: str
    leg: str | None


@dataclass
class JudgementRow:
    record_id: int
    judged_at: datetime
    legs: str
    n_findings: int
    status: Literal["ok", "failed"]


@dataclass
class RunRow:
    run_id: str
    record_id: int
    arm: Arm
    task_id: str
    started: datetime
    ended: datetime
    standin_rounds: int
    cost_usd: float
    wall_s: float
    num_turns: int
    outcome: str
    diff_path: str
    result_dir: str


@dataclass
class TaskRow:
    task_id: str
    repo: str
    pr: int
    slice_index: int | None
    base_commit: str
    reference_commit: str
    raw_ask: str
    reference_tests: list[str]
    kind: Literal["whole", "slice"]


@dataclass
class CoverageRow:
    repo: str
    month: str
    sessions_seen: int
    sessions_unattributed: int
    sessions_excluded: int = 0


def stratum_for(changed_lines: int) -> str:
    return "small" if changed_lines < 500 else "medium" if changed_lines < 1500 else "large"


def is_strict(models: list[str]) -> bool:
    claude = [m for m in models if m.startswith("claude-")]
    return all(m.startswith(STRICT_PREFIXES) for m in claude)


def _cell(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v)
    if isinstance(v, bool):
        return int(v)
    return v


class Store:
    def __init__(self, path: Path = STORE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript((Path(__file__).parent / "schema.sql").read_text())
        if "sessions_excluded" not in {r[1] for r in self.db.execute("PRAGMA table_info(coverage)")}:
            self.db.execute("ALTER TABLE coverage ADD COLUMN sessions_excluded INTEGER NOT NULL DEFAULT 0")
        if self.get_meta("schema_version") is None:
            self.set_meta("schema_version", SCHEMA_VERSION)
        self.db.commit()

    def _insert(self, table: str, row: dict, verb: str = "INSERT") -> int:
        cols = ", ".join(f'"{c}"' for c in row)
        marks = ", ".join("?" for _ in row)
        cur = self.db.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", [_cell(v) for v in row.values()])
        self.db.commit()
        return cur.lastrowid

    # records -----------------------------------------------------------------
    def upsert_record(self, r: RecordRow) -> int:
        row = asdict(r)
        if r.source == "history":
            # an arm flip updates in place: deleting would cascade judgements and verified findings
            self.db.execute(
                "UPDATE records SET arm = ? WHERE repo = ? AND pr = ? AND source = 'history' AND arm != ?",
                (r.arm, r.repo, r.pr, r.arm),
            )
        key = (r.repo, r.pr, r.arm, r.source, r.run_id)
        found = self.db.execute(
            "SELECT id FROM records WHERE repo = ? AND pr = ? AND arm = ? AND source = ? AND run_id = ?", key
        ).fetchone()
        if found:
            sets = ", ".join(f'"{c}" = ?' for c in row)
            self.db.execute(f"UPDATE records SET {sets} WHERE id = ?", [_cell(v) for v in row.values()] + [found[0]])
            self.db.commit()
            return found[0]
        return self._insert("records", row)

    def replace_phases(self, record_id: int, phases: list[Phase], costs: dict[str, float]) -> None:
        self.db.execute("DELETE FROM phases WHERE record_id = ?", (record_id,))
        for p in phases:
            usage = {m: asdict(u) for m, u in p.usage_by_model.items()}
            self.db.execute(
                'INSERT INTO phases (record_id, name, start, "end", wall_s, human_turns, attention_s, '
                "agent_ids, session_ids, usage, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [record_id, p.name, _cell(p.start), _cell(p.end), p.wall_s, p.human_turns, p.attention_s,
                 _cell(p.agent_ids), _cell(p.session_ids), json.dumps(usage), costs.get(p.name, 0.0)],
            )
        self.db.commit()

    def replace_sessions(self, record_id: int, attributions: list[Attribution]) -> None:
        self.db.execute("DELETE FROM sessions WHERE record_id = ?", (record_id,))
        for a in attributions:
            self.db.execute(
                "INSERT INTO sessions (record_id, session_id, rule, start_uuid, end_uuid) VALUES (?, ?, ?, ?, ?)",
                (record_id, a.session_id, a.rule, a.start_uuid, a.end_uuid),
            )
        self.db.commit()

    def add_finding(self, f: FindingRow) -> int:
        return self._insert("findings", asdict(f))

    def delete_findings(self, record_id: int, origin: str) -> None:
        self.db.execute("DELETE FROM findings WHERE record_id = ? AND origin = ?", (record_id, origin))
        self.db.commit()

    def set_verified(self, finding_id: int, verified: bool) -> None:
        self.db.execute("UPDATE findings SET verified = ? WHERE id = ?", (int(verified), finding_id))
        self.db.commit()

    def add_judgement(self, j: JudgementRow) -> int:
        return self._insert("judgements", asdict(j))

    def add_run(self, r: RunRow) -> int:
        return self._insert("runs", asdict(r), verb="INSERT OR REPLACE")

    def upsert_task(self, t: TaskRow) -> None:
        self._insert("tasks", asdict(t), verb="INSERT OR REPLACE")

    def upsert_coverage(self, c: CoverageRow) -> None:
        self._insert("coverage", asdict(c), verb="INSERT OR REPLACE")

    def add_phase_override(self, o: PhaseOverride) -> None:
        self._insert("phase_overrides", asdict(o), verb="INSERT OR REPLACE")

    def phase_overrides(self, session_id: str) -> list[PhaseOverride]:
        rows = self.db.execute(
            "SELECT session_id, start_uuid, phase FROM phase_overrides WHERE session_id = ?", (session_id,)
        ).fetchall()
        return [PhaseOverride(*r) for r in rows]

    def add_model_pass(self, a: Attribution, why: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO model_pass_attributions (session_id, pr, start_uuid, end_uuid, why) "
            "VALUES (?, ?, ?, ?, ?)", (a.session_id, a.pr_number, a.start_uuid, a.end_uuid, why),
        )
        self.db.commit()

    def model_pass_attributions(self) -> dict[str, list[Attribution]]:
        rows = self.db.execute("SELECT session_id, pr, start_uuid, end_uuid FROM model_pass_attributions").fetchall()
        return {
            sid: [Attribution(sid, pr, "model_pass" if pr is not None else "none", start, end)]
            for sid, pr, start, end in rows
        }

    # reads -------------------------------------------------------------------
    def _df(self, sql: str, params=()) -> pd.DataFrame:
        return pd.read_sql_query(sql, self.db, params=params)

    def records(self, view: View = "all") -> pd.DataFrame:
        df = self._df("SELECT * FROM records ORDER BY id")
        df["models"] = df["models"].map(lambda s: json.loads(s) if s else [])
        if view == "strict":
            df = df[df["models"].map(is_strict)]
        return df.reset_index(drop=True)

    def record(self, record_id: int) -> RecordRow:
        df = self.records()
        row = df[df["id"] == record_id]
        if row.empty:
            raise KeyError(f"no record {record_id}")
        d = {k: (None if not isinstance(v, list) and pd.isna(v) else v) for k, v in row.iloc[0].to_dict().items()}
        d.pop("id")
        d["merged_at"] = datetime.fromisoformat(d["merged_at"]) if d["merged_at"] else None
        for key in ("pr", "additions", "deletions", "files_changed"):
            d[key] = int(d[key])
        return RecordRow(**d)

    def phases(self) -> pd.DataFrame:
        return self._df("SELECT * FROM phases")

    def sessions(self) -> pd.DataFrame:
        return self._df("SELECT * FROM sessions")

    def findings(self) -> pd.DataFrame:
        return self._df("SELECT * FROM findings ORDER BY id")

    def judgements(self) -> pd.DataFrame:
        return self._df("SELECT * FROM judgements ORDER BY id")

    def runs(self) -> pd.DataFrame:
        return self._df("SELECT * FROM runs")

    def tasks(self) -> pd.DataFrame:
        df = self._df("SELECT * FROM tasks ORDER BY task_id")
        df["reference_tests"] = df["reference_tests"].map(lambda s: json.loads(s) if s else [])
        return df

    def task(self, task_id: str) -> TaskRow:
        df = self.tasks()
        row = df[df["task_id"] == task_id]
        if row.empty:
            raise KeyError(f"no task {task_id}")
        d = row.iloc[0].to_dict()
        d["slice_index"] = None if pd.isna(d["slice_index"]) else int(d["slice_index"])
        d["pr"] = int(d["pr"])
        return TaskRow(**d)

    def coverage(self) -> pd.DataFrame:
        return self._df("SELECT * FROM coverage ORDER BY repo, month")

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
