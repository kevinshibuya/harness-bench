"""out/report.md and the CSV exports, derived from the store (spec §10, §12)."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bench.config import RESULTS
from bench.extract import EXTRACTOR_VERSION
from bench.store import Store, is_strict

MIN_N = 3
MAJOR = {"major", "blocker"}
STRATA = ["small", "medium", "large"]

# Spec §12, verbatim.
VALIDITY_LIMITS = """\
- A′ history tasks were inline because they were small and clear. Comparisons are stratified by reference size and the confound is still there.
- The loosened pool mixes Opus 5 and Opus 5.5 execution. The strict view is the clean one and is small.
- Mined records are one sample each; runs are one sample each until Kevin repeats them. The report shows n everywhere.
- The judge is two models with fixed prompts. Its precision is known only on the hand-verified sample.
- The stand-in reply is not Kevin. It approves everything and never redirects.
- Slices leak the plan's task heading.
- The blinded diff drops `docs/**` and the harness files for every arm, so a task whose deliverable was documentation is judged on its code only.
- Codex `review` and plain `exec` legs are priced at zero: the codex wrapper records tokens only under `exec --json`. A and A′ costs are lower bounds; V runs no codex and is unaffected.
- Rule 1 attributes a session by its checkout's branch, not by what it worked on. An off-topic session is dropped only when someone spots it and lists it in `exclude_sessions`; one has been found, by hand, and nothing scans for the next."""

SECTIONS = [
    "## Pool sizes", "## Headline", "## Judge precision", "## Cost per phase, A history",
    "## Caught in process, history", "## Runs", "## Coverage", "## Validity limits",
]


def _table(headers: list[str], rows: list[list]) -> str:
    def cell(v):
        if isinstance(v, float):
            return f"{v:,.2f}"
        return "" if v is None else str(v)

    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines) if rows else "_none yet_"


def record_frame(store: Store) -> pd.DataFrame:
    """One row per record with totals, judgement state and escaped-major flag."""
    rec = store.records()
    if rec.empty:
        return rec
    ph = store.phases()
    totals = ph.groupby("record_id")[["cost_usd", "attention_s", "human_turns", "wall_s"]].sum()
    rec = rec.join(totals, on="id").fillna({"cost_usd": 0.0, "attention_s": 0.0, "human_turns": 0, "wall_s": 0.0})
    j = store.judgements()
    latest = j.sort_values("id").groupby("record_id").tail(1) if len(j) else j
    judged_ok = set(latest[latest["status"] == "ok"]["record_id"]) if len(j) else set()
    f = store.findings()
    judge_f = f[f["origin"] == "judge"] if len(f) else f
    majors = set(judge_f[judge_f["severity"].isin(MAJOR)]["record_id"]) if len(judge_f) else set()
    rec["judged"] = rec["id"].isin(judged_ok)
    rec["escaped_major"] = rec["judged"] & rec["id"].isin(majors)
    rec["strict"] = rec["models"].map(is_strict)
    return rec


def _headline(rec: pd.DataFrame) -> str:
    rows = []
    views = [("loosened", rec), ("strict", rec[rec["strict"]])]
    keys = ["arm", "source", "kind", "stratum"]
    for view, df in views:
        if df.empty:
            continue
        for (arm, source, kind, stratum), g in df.groupby(keys, sort=True):
            n = len(g)
            if n < MIN_N:
                rows.append([arm, source, kind, view, stratum, n, None, None, None, None, None])
                continue
            judged = g[g["judged"]]
            clean = judged[~judged["escaped_major"]]
            rate = f"{judged['escaped_major'].mean():.0%} of {len(judged)}" if len(judged) else "n/a"
            per_clean = judged["cost_usd"].sum() / len(clean) if len(clean) else None
            rows.append([arm, source, kind, view, stratum, n, g["cost_usd"].median(), g["attention_s"].median(),
                         g["human_turns"].median(), rate, per_clean])
    order = {s: i for i, s in enumerate(STRATA)}
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3], order.get(r[4], 9)))
    return _table(["arm", "source", "kind", "view", "stratum", "n", "median cost_usd", "median attention_s",
                   "median human_turns", "escaped-major rate", "cost per record with zero escaped majors"], rows)


def _precision(store: Store) -> str:
    f = store.findings()
    judge_f = f[(f["origin"] == "judge") & f["verified"].notna()] if len(f) else f
    if judge_f.empty:
        return "No judge finding has been hand-verified yet."
    true = int((judge_f["verified"] == 1).sum())
    return f"{true} of {len(judge_f)} hand-verified judge findings are real: precision {true / len(judge_f):.0%}."


def _phase_costs(rec: pd.DataFrame, store: Store) -> str:
    ids = set(rec[(rec["arm"] == "A") & (rec["source"] == "history")]["id"])
    ph = store.phases()
    ph = ph[ph["record_id"].isin(ids)]
    if ph.empty:
        return _table([], [])
    total = ph["cost_usd"].sum() or 1.0
    order = ["unassigned", "spec", "plan", "plan_review", "execution", "pr_review"]
    rows = []
    for name in order:
        g = ph[ph["name"] == name]
        if g.empty:
            continue
        n = len(g)
        med = (lambda c: None if n < MIN_N else g[c].median())
        rows.append([name, n, med("cost_usd"), med("wall_s"), med("attention_s"), f"{g['cost_usd'].sum() / total:.0%}"])
    return _table(["phase", "n records", "median cost_usd", "median wall_s", "median attention_s", "share of cost"], rows)


def _caught(rec: pd.DataFrame, store: Store) -> str:
    f = store.findings()
    if f.empty:
        return _table([], [])
    hist = rec[rec["source"] == "history"][["id", "arm"]]
    f = f[f["origin"] == "in_process"].merge(hist, left_on="record_id", right_on="id")
    rows = []
    for (arm, stage), g in f.groupby(["arm", "stage"]):
        rows.append([arm, stage, g["record_id"].nunique(), len(g), int((g["confirmed"] == 1).sum())])
    return _table(["arm", "stage", "records", "raised", "confirmed"], rows)


def _runs(store: Store) -> str:
    runs = store.runs()
    if runs.empty:
        return _table([], [])
    rec = store.records()[["id", "test_gate"]]
    runs = runs.merge(rec, left_on="record_id", right_on="id", how="left")
    rows = [[r.run_id, r.task_id, r.arm, r.outcome, r.test_gate, r.standin_rounds, r.cost_usd, r.wall_s]
            for r in runs.sort_values("started").itertuples()]
    return _table(["run", "task", "arm", "outcome", "test gate", "stand-in rounds", "cost_usd", "wall_s"], rows)


def _coverage(store: Store) -> str:
    cov = store.coverage()
    rows = [[c.repo, c.month, c.sessions_seen, c.sessions_unattributed, c.sessions_excluded,
             f"{1 - c.sessions_unattributed / c.sessions_seen:.0%}" if c.sessions_seen else "n/a"]
            for c in cov.itertuples()]
    return _table(["repo", "month", "sessions seen", "unattributed", "excluded", "attributed"], rows)


def build_report(store: Store) -> str:
    rec = record_frame(store)
    strict_n = int(rec["strict"].sum()) if len(rec) else 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        "# harness-bench report", "",
        f"Generated {now}, extractor version {EXTRACTOR_VERSION}. Costs are list-price reference numbers, "
        f"not what Kevin pays. Cells with n below {MIN_N} show n only.", "",
        SECTIONS[0], "", _table(["view", "records"], [["loosened", len(rec)], ["strict", strict_n]]), "",
        SECTIONS[1], "",
        "Grouped by arm, source, kind, view and stratum. History and run records never share a row.", "",
        _headline(rec) if len(rec) else _table([], []), "",
        SECTIONS[2], "", _precision(store), "",
        SECTIONS[3], "", _phase_costs(rec, store) if len(rec) else _table([], []), "",
        SECTIONS[4], "", _caught(rec, store) if len(rec) else _table([], []), "",
        SECTIONS[5], "", _runs(store), "",
        SECTIONS[6], "", _coverage(store), "",
        SECTIONS[7], "", VALIDITY_LIMITS, "",
    ]
    return "\n".join(parts)


def export_csv(store: Store, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "records": store.records(), "phases": store.phases(), "findings": store.findings(),
        "judgements": store.judgements(), "runs": store.runs(), "coverage": store.coverage(),
    }
    paths = []
    for name, df in frames.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        paths.append(path)
    return paths


def write_report(store: Store, results: Path = RESULTS) -> Path:
    path = results / "report.md"
    path.write_text(build_report(store))
    export_csv(store, results / "exports")
    return path
