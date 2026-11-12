from pathlib import Path

from bench.config import REPOS_EXAMPLE, load_repos, transcript_dir_for, transcript_dirs_for


def test_escaping_matches_claude_code(tmp_path):
    d = transcript_dir_for(Path("/bench-test/code/my_app"), root=tmp_path)
    assert d.name == "-bench-test-code-my-app"


def test_worktree_dirs_are_picked_up(tmp_path):
    repo = load_repos(REPOS_EXAMPLE)["tiny"]
    main = transcript_dir_for(repo.path, root=tmp_path)
    for name in (main.name, f"{main.name}--claude-worktrees-a", f"{main.name}--claude-worktrees-b"):
        (tmp_path / name).mkdir()
    (tmp_path / "unrelated").mkdir()
    dirs = transcript_dirs_for(repo, root=tmp_path)
    assert dirs[0] == main and len(dirs) == 3


def test_example_repos_config_loads():
    repos = load_repos(REPOS_EXAMPLE)
    assert set(repos) >= {"sample-app", "tiny"}
    app = repos["sample-app"]
    assert app.path.is_absolute()
    assert app.integration_branch == "ai-staging"
    assert app.test_cmd


def test_exclude_sessions_load_with_reasons(tmp_path):
    f = tmp_path / "repos.toml"
    f.write_text('[r]\npath = "/x"\ndefault_branch = "main"\nintegration_branch = "ai-staging"\ntest_cmd = "true"\n\n'
                 '[[r.exclude_sessions]]\nid = "s1"\nreason = "off-topic"\n')
    assert load_repos(f)["r"].exclude_sessions == {"s1": "off-topic"}
