import json
import subprocess
from datetime import datetime, timezone

from bench.judge import blinded_input, codex_argv, judge_record, parse_codex_review
from bench.store import RecordRow, Store

DIFF = """diff --git a/docs/plans/x.md b/docs/plans/x.md
new file mode 100644
--- /dev/null
+++ b/docs/plans/x.md
@@ -0,0 +1 @@
+# Plan: arm A pipeline
diff --git a/CLAUDE.md b/CLAUDE.md
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -1 +1 @@
-old
+## Lanes
diff --git a/src/a.ts b/src/a.ts
--- a/src/a.ts
+++ b/src/a.ts
@@ -1 +1 @@
-export const a = 1;
+export const a = 2;
"""


def record(**kw):
    base = dict(repo="r", pr=5, arm="A", source="history", run_id="", title="feat: secret title",
                head_branch="feat/secret-branch", base_commit="b", reference_commit="m",
                merged_at=datetime(2026, 9, 20, tzinfo=timezone.utc), additions=1, deletions=1, files_changed=3,
                stratum="small", kind="whole", harness_version=None, models=[], raw_ask="Make a two.",
                outcome="merged", test_gate="n/a", extractor_version="1")
    base.update(kw)
    return RecordRow(**base)


def test_blinding_keeps_only_source_hunks():
    text = blinded_input(record(), DIFF)
    assert "src/a.ts" in text and "export const a = 2;" in text
    assert "docs/plans" not in text and "CLAUDE.md" not in text and "Lanes" not in text
    assert "secret" not in text and "Make a two." in text


def test_codex_argv(tmp_path):
    argv = codex_argv(7, tmp_path / "in.txt")
    assert argv[1] == "judge-7"
    assert argv[argv.index("-m") + 1] == "gpt-6-astra"
    assert "model_reasoning_effort=xhigh" in argv
    assert argv[argv.index("-C") + 1] == str(tmp_path)
    assert argv[argv.index("-s") + 1] == "read-only"
    assert "exec" in argv and "--skip-git-repo-check" in argv and str(tmp_path / "in.txt") in argv[-1]
    assert "review" not in argv and not any(".claude" in a and a != argv[0] for a in argv)


def test_codex_list_format_parses_and_dedupes():
    log = "- [P1] Off by one — src/a.ts:3\n  Loops one past the end.\n\n- [P1] Off by one — src/a.ts:3\n  Loops one past the end.\n"
    assert parse_codex_review(log) == [
        {"title": "Off by one", "where": "src/a.ts:3", "why": "Loops one past the end.", "severity_guess": "blocker"}
    ]


def test_prompt_echo_is_not_an_answer():
    log = 'user\n[{"title": "<one line>", "where": "<file:line or file>"}]\n\n- [P3] Typo — a.ts\n  x\n'
    assert [f["title"] for f in parse_codex_review(log)] == ["Typo"]


def fakes(tmp_path, opus, codex, merged):
    replies = iter([opus, merged])

    def claude(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": json.dumps(next(replies))}), stderr="")

    def codex_run(argv, **kw):
        log = tmp_path / "codex.log"
        log.write_text(json.dumps(codex))
        return subprocess.CompletedProcess(argv, 0, stdout=f"codex-run: label={argv[1]} log={log}\n", stderr="")

    return claude, codex_run


def with_task(store, ask="Make a two."):
    from bench.store import TaskRow
    store.upsert_task(TaskRow("r-5", "r", 5, None, "b", "m", ask, [], "whole"))


def test_overlapping_legs_consolidate(tmp_path):
    store = Store(tmp_path / "b.sqlite")
    rid = store.upsert_record(record())
    with_task(store)
    opus = [{"title": "a is wrong", "where": "src/a.ts:1"}, {"title": "no test", "where": "src/a.ts"}]
    codex = [{"title": "a should be 3", "where": "src/a.ts:1"}]
    merged = [{"title": "a is wrong", "where": "src/a.ts:1", "why": "x", "severity": "major", "leg": "both"},
              {"title": "no test", "where": "src/a.ts", "why": "y", "severity": "not-a-defect", "leg": "opus"}]
    claude, codex_run = fakes(tmp_path, opus, codex, merged)
    j = judge_record(store, rid, claude, codex_run, git=lambda *a: DIFF)
    assert (j.status, j.n_findings, j.legs) == ("ok", 2, "codex,opus")
    f = store.findings()
    assert list(f["severity"]) == ["major", "not-a-defect"] and list(f["leg"]) == ["both", "opus"]


def test_zero_findings_still_stores_a_judgement(tmp_path):
    store = Store(tmp_path / "b.sqlite")
    rid = store.upsert_record(record())
    with_task(store)
    claude, codex_run = fakes(tmp_path, [], [], [])
    j = judge_record(store, rid, claude, codex_run, git=lambda *a: DIFF)
    assert (j.status, j.n_findings) == ("ok", 0)
    assert list(store.judgements()["n_findings"]) == [0]


def test_rejudge_replaces_findings(tmp_path):
    store = Store(tmp_path / "b.sqlite")
    rid = store.upsert_record(record())
    with_task(store)
    one = [{"title": "bug", "where": "src/a.ts", "why": "x", "severity": "major", "leg": "both"}]
    two = one + [{"title": "nit", "where": "src/a.ts", "why": "y", "severity": "minor", "leg": "opus"}]
    judge_record(store, rid, *fakes(tmp_path, [{"title": "x"}], [], two), git=lambda *a: DIFF)
    judge_record(store, rid, *fakes(tmp_path, [{"title": "x"}], [], one), git=lambda *a: DIFF)
    assert len(store.findings()) == 1 and len(store.judgements()) == 2


def test_history_is_judged_on_the_task_ask_never_the_mined_turn(tmp_path):
    import pytest
    from bench.types import Blocked
    store = Store(tmp_path / "b.sqlite")
    rid = store.upsert_record(record(raw_ask="pickup HANDOFF.md"))
    with pytest.raises(Blocked, match="bench tasks add --repo r --pr 5 --ask"):
        judge_record(store, rid, *fakes(tmp_path, [], [], []), git=lambda *a: DIFF)
    with_task(store, ask="Add X")
    seen = []
    claude, codex_run = fakes(tmp_path, [], [], [])

    def spy(argv, **kw):
        seen.append(argv[-1])
        return claude(argv, **kw)

    judge_record(store, rid, spy, codex_run, git=lambda *a: DIFF)
    assert "Add X" in seen[0] and "HANDOFF" not in seen[0]
