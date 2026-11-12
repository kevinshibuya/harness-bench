from datetime import datetime, timezone

from bench.phases import Phase
from bench.report import SECTIONS, VALIDITY_LIMITS, build_report, export_csv
from bench.store import FindingRow, JudgementRow, RecordRow, Store
from bench.transcripts import Usage

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def rec(pr, arm="A", source="history", run_id="", kind="whole", stratum="small", models=("claude-opus-5-5",)):
    return RecordRow(repo="r", pr=pr, arm=arm, source=source, run_id=run_id, title="t", head_branch="h",
                     base_commit="b", reference_commit="m", merged_at=NOW, additions=1, deletions=1, files_changed=1,
                     stratum=stratum, kind=kind, harness_version=None, models=list(models), raw_ask="x",
                     outcome="merged" if source == "history" else "done", test_gate="n/a", extractor_version="1")


def build(tmp_path):
    s = Store(tmp_path / "b.sqlite")
    ids = []
    for pr in (1, 2, 3):  # A history small, strict
        ids.append(s.upsert_record(rec(pr)))
    for pr in (4, 5):  # A history medium: n=2
        s.upsert_record(rec(pr, stratum="medium", models=("claude-opus-5",)))
    for pr in (6, 7, 8):
        s.upsert_record(rec(pr, arm="Aprime"))
    for i, arm in enumerate(["Aprime", "V", "V", "V"]):
        s.upsert_record(rec(6, arm=arm, source="run", run_id=f"run{i}", kind="slice"))
    for rid, cost in zip(ids, (1.0, 2.0, 3.0)):
        s.replace_phases(rid, [Phase("execution", NOW, NOW, 10, {"claude-opus-5-5": Usage(1, 0, 0, 1)}, 2, 60, [], [])],
                         {"execution": cost})
        s.add_judgement(JudgementRow(rid, NOW, "codex,opus", 0, "ok"))
    s.add_finding(FindingRow(ids[0], "judge", None, "major", None, True, "bug", "both"))
    s.add_finding(FindingRow(ids[0], "judge", None, "minor", None, False, "nit", "opus"))
    s.add_finding(FindingRow(ids[1], "in_process", "pr_review", None, True, None, "caught", "codex"))
    return s


def test_sections_and_validity(tmp_path):
    text = build_report(build(tmp_path))
    for heading in SECTIONS:
        assert heading in text
    assert VALIDITY_LIMITS in text
    assert "1 of 2 hand-verified judge findings are real: precision 50%." in text


def headline_rows(text):
    block = text.split("## Headline")[1].split("## Judge precision")[0]
    return [line for line in block.splitlines() if line.startswith("| ") and not line.startswith("| arm") and "---" not in line]


def test_history_and_run_never_share_a_row(tmp_path):
    rows = headline_rows(build_report(build(tmp_path)))
    aprime = [r for r in rows if r.startswith("| Aprime |")]
    assert any("| history |" in r for r in aprime) and any("| run |" in r for r in aprime)


def test_small_cells_show_no_median(tmp_path):
    rows = headline_rows(build_report(build(tmp_path)))
    medium = next(r for r in rows if "| A | history | whole | loosened | medium |" in r)
    cells = [c.strip() for c in medium.strip("|").split("|")]
    assert cells[5] == "2" and cells[6:] == ["", "", "", "", ""]
    small = next(r for r in rows if "| A | history | whole | loosened | small |" in r)
    cells = [c.strip() for c in small.strip("|").split("|")]
    assert cells[5:8] == ["3", "2.00", "60.00"]
    assert cells[9] == "33% of 3"
    assert cells[10] == "3.00"  # $6 over two clean records


def test_exports(tmp_path):
    paths = export_csv(build(tmp_path), tmp_path / "exports")
    assert sorted(p.name for p in paths) == sorted(
        ["records.csv", "phases.csv", "findings.csv", "judgements.csv", "runs.csv", "coverage.csv"])


def test_failed_latest_judgement_is_not_judged(tmp_path):
    from bench.report import record_frame
    s = Store(tmp_path / "b.sqlite")
    rid = s.upsert_record(rec(1))
    s.add_judgement(JudgementRow(rid, NOW, "codex,opus", 1, "ok"))
    s.add_judgement(JudgementRow(rid, NOW, "opus", 1, "failed"))
    s.add_finding(FindingRow(rid, "judge", None, "major", None, None, "bug", "opus"))
    row = record_frame(s).iloc[0]
    assert not row["judged"] and not row["escaped_major"]
