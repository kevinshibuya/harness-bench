import json
import shutil
import subprocess
from pathlib import Path

from bench.archive import archive_transcripts
from bench.attribute import Attribution
from bench.config import RepoConfig
from bench.modelpass import resolve_phase_prefixes, resolve_unattributed
from bench.store import Store
from helpers import Builder, pr

FIX = Path(__file__).parent / "fixtures" / "mini_session"
REPO = RepoConfig("r", Path("/repo/x"), "main", "ai-staging", None, "true")


def fake(answer: dict, seen: list):
    def runner(argv, **kw):
        seen.append((argv, kw["env"].get("CLAUDE_CONFIG_DIR")))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"result": json.dumps(answer)}), stderr="")
    return runner


def test_unattributed_session_resolves_to_pr(tmp_path):
    shutil.copytree(FIX, tmp_path / "src" / "-repo-x")
    arch = tmp_path / "archive"
    archive_transcripts([tmp_path / "src" / "-repo-x"], arch, "r")
    store = Store(tmp_path / "b.sqlite")
    seen = []
    atts = resolve_unattributed(REPO, store, [pr(12, "feat/other")], arch, runner=fake({"pr": 12, "why": "x"}, seen))
    assert [(a.pr_number, a.rule) for a in atts] == [(12, "model_pass")]
    argv, config_dir = seen[0]
    assert "--disable-slash-commands" in argv and config_dir
    assert "Build feature x" in argv[-1] and "#12" in argv[-1]
    assert store.model_pass_attributions()["s1"][0].pr_number == 12
    again = resolve_unattributed(REPO, store, [pr(12, "feat/other")], arch, runner=fake({"pr": 12}, seen))
    assert again == [] and len(seen) == 1


def test_long_unassigned_prefix_gets_an_override(tmp_path):
    b = Builder(branch="feat/x")
    for i in range(6):
        b.human(f"design question {i}")
        b.assistant()
    b.skill("executing-plans")
    s = b.session()
    att = Attribution("s1", 1, "branch", s.events[0].uuid, s.events[-1].uuid)
    store = Store(tmp_path / "b.sqlite")
    seen = []
    out = resolve_phase_prefixes(1, [(s, att)], store, runner=fake({"phase": "spec", "why": "design"}, seen))
    assert [(o.session_id, o.start_uuid, o.phase) for o in out] == [("s1", s.events[0].uuid, "spec")]
    assert store.phase_overrides("s1") == out
