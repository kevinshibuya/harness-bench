# harness-bench implementation plan

**Goal:** A Python CLI that mines Claude Code transcripts into per-PR records, runs the vanilla and inline arms on demand, judges every arm's output the same blinded way, and regenerates a comparison report on a schedule that spends no Claude usage.
**Architecture:** One package `bench` with a pipeline of pure modules (transcripts → attribute → phases → price → store) feeding a SQLite store, plus three side modules that spend Claude usage only when invoked by hand (run, judge, modelpass). Run transcripts go through the same pipeline as history. A launchd agent archives transcripts, extracts and reports daily. Target repos are read through bare mirror clones with pushes disabled, so no run can touch a real branch.
**Spec:** `docs/specs/2026-09-24-harness-bench-design.md`
**Execution model:** opus

> Edited 2026-11-12 before publishing: the private target repos are called "the first repo" and "the second repo", and home-directory paths were removed. This plan predates the move of all output from the committed `results/` to the gitignored `out/` (spec §11, amendment); `config/repos.toml` is now `config/repos.example.toml` plus a gitignored `config/repos.local.toml`.

Revision 2, after the one review wave (reviewer on Opus, reviewer on Fable, codex astra). Findings consolidated inline on Fable.

## Global constraints

- Python 3.12, `uv`, `pandas`, `argparse`, `sqlite3`, `pytest`. No other runtime dependency without a stated reason in the commit.
- Nothing that spends Claude or codex usage runs on a schedule. Only `bench extract` and `bench report` are scheduled.
- No task in this plan runs an arm on a shortlist task. The permitted live calls: one luna probe in Task 10, the isolation check and the tiny-repo smoke in Task 12.
- Arms are one enum everywhere, `Arm = Literal["A", "Aprime", "V"]`, in code, CLI, store and report.
- Records older than 2026-09-01 are never created. Loosened view = every record; strict view = records whose Claude models are all in `{claude-opus-5-5, claude-fable-5-1, claude-haiku-4-5*}`; gpt models never affect a view; strict is a subset of loosened.
- Human turn: user line, `message.content` is a string, `isMeta` not true, content does not start with `<`.
- Usage is counted once per `message.id`, never per assistant line.
- Attention cap per turn: 600 s. Size strata: small < 500 changed lines, medium < 1500, large otherwise.
- Store key: `(repo, pr, arm, source, run_id)` with `run_id = ""` for history rows, never NULL. A history record is replaced by `(repo, pr, source="history")`, so an arm flip leaves no stale row.
- Stand-in reply, verbatim: `Go with your recommendation. Treat this as my go, and as GREEN, wherever one is needed.`
- Prompt suffix, verbatim, all arms: `Work in this checkout only. Do not push and do not open a PR. If you have a question, ask it and stop. When the change is complete, commit it and end your reply with the single word DONE.`
- Stall guard: three consecutive resumes with an unchanged content fingerprint end the run as `stalled`. A claude error, `is_error`, or a usage-limit message ends it as `failed`. No other cap.
- `--bare` is never passed. Isolation is `CLAUDE_CONFIG_DIR` plus `--setting-sources project --disable-slash-commands --strict-mcp-config`.
- Prices come from the vendors' pricing pages, with the fetch date in the file. Never from memory.
- Target repos: two private repos, the first and the second. Transcript root: `~/.claude/projects/`. Both repos' integration branch is `ai-staging`.
- Every command that reads `~/.claude` or the target repos is read-only there, except Task 10, which edits `~/.claude/bin/codex-run.sh`.
- Commit after every task with a one-line message and the attribution trailer.

---

### Task 1: Scaffold

**Files:**
- `pyproject.toml` — create: project `harness-bench`, package `bench`, script `bench = "bench.cli:main"`, deps `pandas`, dev deps `pytest`
- `bench/__init__.py`, `bench/cli.py`, `bench/config.py`, `bench/types.py` — create
- `config/repos.toml` — create
- `.gitignore` — create: `mirrors/`, `worktrees/`, `archive/`, `.venv/`, `__pycache__/`, `results/extract.log`, `config/empty-claude/*`, `!config/empty-claude/.keep`
- `config/empty-claude/.keep` — create
- `tests/test_cli.py`, `tests/test_config.py` — create
- `README.md` — create: one paragraph, the CLI list from spec §11, and a "spends Claude usage" column per command

**Interfaces:**
- Produces:
  ```python
  # bench/types.py
  Arm = Literal["A", "Aprime", "V"]
  Source = Literal["history", "run"]
  # bench/config.py
  @dataclass(frozen=True)
  class RepoConfig:
      name: str; path: Path; default_branch: str; integration_branch: str
      setup_cmd: str | None; test_cmd: str
  def load_repos(path: Path = CONFIG/"repos.toml") -> dict[str, RepoConfig]
  def transcript_dir_for(path: Path, root: Path = TRANSCRIPT_ROOT) -> Path
  def transcript_dirs_for(repo: RepoConfig, root: Path = TRANSCRIPT_ROOT) -> list[Path]
  # bench/cli.py
  def main(argv: list[str] | None = None) -> int
  ```

**Work:** `transcript_dir_for` applies Claude Code's escaping: `re.sub(r"[^A-Za-z0-9]", "-", str(path.resolve()))`, so `my_app` becomes `my-app`. `transcript_dirs_for` returns the main dir plus every existing sibling matching `<main>--claude-worktrees-*`, so new worktree dirs are picked up without config. `repos.toml` carries the two repos with `setup_cmd` (`cd app && npm ci` for the first repo; look at the second repo's README or package files for its equivalent and record it) and `test_cmd` (first repo: `cd app && npm test`; second repo: read its package files). Subcommands `extract`, `report`, `run`, `judge`, `tasks`, `status` exist as stubs that print `not implemented` and return 2. Internal structure is your choice.

**Acceptance check:** `uv run pytest tests/test_cli.py tests/test_config.py` — red: module `bench` missing. Green: `main(["--help"])` raises `SystemExit(0)` and the captured help lists all six subcommands; `transcript_dir_for` on the first repo's path names an existing directory; `transcript_dirs_for` for the second repo returns at least two existing directories.

**Boundaries:** No logic beyond wiring. Do not touch `~/.claude`.

- [x] `uv init`, `pyproject.toml`, deps
- [x] `types.py`, `config.py`, `cli.py` stubs
- [x] `repos.toml` with real setup and test commands, `.gitignore`, `README.md`
- [x] tests, run, commit

---

### Task 2: Transcript reader

**Files:**
- `bench/transcripts.py` — create
- `tests/fixtures/mini_session/README.md`, `tests/fixtures/mini_session/<sid>.jsonl`, `tests/fixtures/mini_session/<sid>/subagents/agent-a1.jsonl` — create: hand-written lines covering every case below, with the README listing expected counts
- `tests/test_transcripts.py` — create

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Usage: input: int; cache_write: int; cache_read: int; output: int
  @dataclass(frozen=True)
  class Event:
      kind: Literal["assistant", "human", "tool_use", "tool_result", "pr_link"]
      ts: datetime; session_id: str; agent_id: str | None; uuid: str; parent_uuid: str | None
      branch: str | None; cwd: str | None
      model: str | None; effort: str | None; message_id: str | None; request_id: str | None
      usage: Usage | None            # set on exactly one event per message_id
      text: str | None               # human text; tool_result stdout/result text; pr_link url
      tool_name: str | None; tool_use_id: str | None; tool_input: dict | None; tool_result: dict | None
  @dataclass
  class Session:
      session_id: str; repo: str; path: Path; started: datetime; ended: datetime
      branches: set[str]; events: list[Event]; subagents: dict[str, list[Event]]
  def read_session(path: Path, repo: str) -> Session
  def iter_sessions(dirs: list[Path], repo: str) -> Iterator[Session]
  ```

**Work:** One JSON object per line, unparsable lines skipped with a counted warning. Mapping per spec §4: an `assistant` line yields one `assistant` event (model, effort, message_id from `message.id`, request_id) plus one `tool_use` event per `tool_use` block carrying `tool_use_id` (the block's `id`), `tool_name`, `tool_input`. Claude Code writes one API response as several assistant lines sharing `message.id`, each repeating the full usage: attach `usage` to the first event seen for a `message_id` and `None` to the rest. A `user` line is `human` when the human-turn rule holds; `tool_result` when its content array holds a `tool_result` block, with `tool_use_id` from that block, `tool_result` = the line's `toolUseResult` dict or `{}`, and `text` = the block's text content when it is a string (this is where Bash stdout lives); otherwise ignored. `pr-link` lines become `pr_link` with `text` = the URL. Every other line type is ignored. Subagent files under `<sid>/subagents/` are read with the same function and keyed by the `agentId` on their lines; nested `subagents/` under an agent are read too and keyed the same way. `branches` collects every non-empty `gitBranch`; `cwd` is copied onto every event. Timestamps parse to aware UTC.

**Acceptance check:** `uv run pytest tests/test_transcripts.py` — red: import error. Green: event counts per kind match the README; two assistant lines sharing a `message.id` yield exactly one non-None usage; a `<command-name>` line and an `isMeta` line are not human; a Bash `tool_result` carries its stdout in `text`; the subagent map has key `a1`.

**Boundaries:** No attribution, no pricing. Tests never read real transcripts.

- [x] fixture with README of expected counts, including the duplicate-usage pair
- [x] `read_session`, `iter_sessions`
- [x] tests, commit

---

### Task 3: PR metadata and mirrors

**Files:**
- `bench/prs.py`, `bench/mirrors.py` — create
- `tests/fixtures/prs_sample.json` — create: trimmed `gh` output with three PRs, one of them a promotion PR
- `tests/test_prs.py` — create

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ChangedFile: path: str; status: Literal["A", "M", "D", "R"]
  @dataclass(frozen=True)
  class PR:
      repo: str; number: int; title: str; head_branch: str; base_branch: str
      merged_at: datetime; merge_commit: str; base_commit: str        # base_commit = merge_commit^1
      head_commit: str; first_commit_at: datetime; commits: tuple[str, ...]  # from refs/pull/N/head, oldest first
      additions: int; deletions: int; changed_files: tuple[ChangedFile, ...]
  def sync_prs(repo: RepoConfig) -> list[PR]      # gh + mirror; writes results/prs/<repo>.json
  def load_prs(repo: RepoConfig) -> list[PR]
  def ensure_mirror(repo: RepoConfig) -> Path     # mirrors/<repo>.git, fetched, pushes disabled
  ```

**Work:** `sync_prs` runs `gh pr list --state merged --limit 200 --json number,title,headRefName,baseRefName,mergedAt,mergeCommit,additions,deletions` in `repo.path` and keeps only PRs with `base_branch == repo.integration_branch` and `head_branch` not in `{default_branch, integration_branch}`. From the mirror: `base_commit = merge_commit^1`; `head_commit = refs/pull/<N>/head` (stop with `blocked: refs/pull/<N>/head missing in mirror <repo>` if absent); `commits = git rev-list --reverse $(git merge-base base_commit head_commit)..head_commit`; `first_commit_at` = author date of `commits[0]`; `changed_files` from `git diff --name-status base_commit merge_commit` (rename status `R` keeps the new path). `ensure_mirror` runs `git clone --mirror <repo.path> mirrors/<repo>.git` on first use with the clone's `remote.origin.url` then rewritten to the real GitHub URL (`gh repo view --json url`), fetches `+refs/pull/*/head:refs/pull/*/head` on every call, and sets `remote.origin.pushurl` to `DISABLED` so any push fails. Git calls live behind one small function so tests can stub them.

**Acceptance check:** `uv run pytest tests/test_prs.py` — red: import error. Green: the promotion PR is filtered out; the two kept PRs have `base_commit` equal to the stubbed first parent. Live smoke: `uv run python -c "from bench.config import load_repos; from bench.prs import sync_prs; print([p.number for p in sync_prs(load_repos()['<first repo>'])])"` prints a list containing 23 and not containing 22, and `git -C mirrors/<first repo>.git config remote.origin.pushurl` prints `DISABLED`.

**Boundaries:** Read-only in the target repos. No attribution.

- [x] `mirrors.py` with pull refs and disabled push
- [x] `prs.py` with the filter and the git lookups stubbed in tests
- [x] tests, live smoke, commit

---

### Task 4: Attribution

**Files:**
- `bench/attribute.py` — create
- `tests/test_attribute.py` — create, using the Task 2 fixture plus in-memory `PR`s

**Interfaces:**
- Consumes: `Session`, `PR`, `RepoConfig`
- Produces:
  ```python
  @dataclass(frozen=True)
  class Attribution:
      session_id: str; pr_number: int | None
      rule: Literal["branch", "pr_link", "window", "model_pass", "none"]
      start_uuid: str; end_uuid: str      # inclusive, contiguous range of main-session events
  def attribute(session: Session, prs: list[PR], repo: RepoConfig) -> list[Attribution]
  def repo_relative(path: str, cwd: str | None, repo: RepoConfig) -> str | None
  ```

**Work:** Rules in order from spec §5, producing contiguous, disjoint ranges that together cover the session. Rule 1: walk events in order and cut the session into segments at every change of `branch`; a segment whose branch equals a PR head branch attributes to that PR (ties between PRs sharing a head branch name break by the segment's time falling inside the PR's `[first_commit_at, merged_at]` window); a leading run of segments with no match attaches to the first matching segment that follows (the grill on `ai-staging` before the branch exists belongs to that branch's PR); a non-matching segment between two matches attaches to the preceding one. The human turn immediately before a branch change belongs to the segment after it. Rule 2: a `pr_link` event attributes the segment it sits in. Rule 3: a segment still unattributed whose span lies inside exactly one PR's window and whose edited paths intersect that PR's `changed_files` attributes there; edited paths are `Edit`/`Write` tool inputs passed through `repo_relative`, which strips `repo.path` or any `<repo.path>/.claude/worktrees/<name>` prefix and returns `None` for paths outside the repo. Anything left is one `none` attribution; a session on the integration or default branch with no match stays `none`.

**Acceptance check:** `uv run pytest tests/test_attribute.py` — red: import error. Green: a session on `ai-staging` then `feat/x` yields one attribution to the `feat/x` PR covering the whole session; `feat/x` → `feat/y` → `feat/x` yields three disjoint ranges, two to x and one to y, with the pre-switch human turns on the correct side; an absolute path under a worktree normalises to the repo-relative path; a session with no signal yields `none`.

**Boundaries:** No model calls. No phases.

- [x] segmentation and rules 1 to 3, `repo_relative`
- [x] tests, commit

---

### Task 5: Arm and phases

**Files:**
- `bench/phases.py` — create
- `tests/test_phases.py` — create

**Interfaces:**
- Consumes: `Session`, `Attribution`, `PR`, `Usage`, `Arm`
- Produces:
  ```python
  PhaseName = Literal["unassigned", "spec", "plan", "plan_review", "execution", "pr_review"]
  @dataclass
  class Phase:
      name: PhaseName; start: datetime; end: datetime; wall_s: float
      usage_by_model: dict[str, Usage]; human_turns: int; attention_s: float
      agent_ids: list[str]; session_ids: list[str]
  @dataclass(frozen=True)
  class PhaseOverride: session_id: str; start_uuid: str; phase: PhaseName
  def arm_for(sessions: list[tuple[Session, Attribution]], pr: PR) -> Arm      # "A" or "Aprime"
  def segment(sessions: list[tuple[Session, Attribution]], arm: Arm, overrides: list[PhaseOverride] = ()) -> list[Phase]
  ```

**Work:** `arm_for` returns `A` when any `Skill` tool_use in the attributed ranges has `input.skill` in `{grilling, grill-with-docs, grill-me, writing-plans, executing-plans}`, or when `pr.changed_files` has a file with status `A` under `docs/` whose path contains `spec` or `plan`; else `Aprime`. `segment` works **session by session**: each attributed range starts in `unassigned`; the marker table from spec §5 opens phases; `pickup` (or an override) resumes the last phase left open by the previous session of the same PR; a phase closes at the next marker or at the range end. In `Aprime` the execution marker is the first `Edit`/`Write` to a path not under `docs/`, and any `reviewer` dispatch or `codex-review` skill call opens `pr_review`. Phases with the same name across sessions merge into one `Phase` whose `wall_s` is the sum of per-session window durations, never the gap between sessions. Usage: the deduplicated usage of assistant events inside the window, plus the whole usage of every subagent whose **dispatching `Agent` tool_use** falls inside the window (join `tool_result.tool_use_id` to the tool_use, take `toolUseResult.agentId`), walking nested subagents recursively; each agent file is counted once. `human_turns` and `attention_s` per the constraints, the previous assistant timestamp taken within the same session only. Marker table is data, not branches.

**Acceptance check:** `uv run pytest tests/test_phases.py` — red: import error. Green: a fixture with `grilling`, `writing-plans`, a reviewer dispatch whose result lands after `executing-plans`, then execution yields four phases with the subagent charged to `plan_review`; a second session starting with `pickup` continues `execution` and its overnight gap is not in `wall_s`; a 20-minute gap counts 600 of attention; an `Aprime` fixture with a reviewer dispatch after the first edit yields `execution` then `pr_review`; a PR that only modifies an existing plan file is `Aprime`.

**Boundaries:** No pricing, no store.

- [x] `arm_for`
- [x] `segment`: per-session windows, marker table, dispatch-time subagent join, overrides
- [x] tests, commit

---

### Task 6: Prices

**Files:**
- `config/prices.toml` — create
- `bench/price.py` — create
- `tests/test_price.py` — create

**Interfaces:**
- Produces: `load_prices(path=...) -> Prices`; `cost_usd(model: str, usage: Usage, prices: Prices) -> float`; `Prices.for_model(model) -> ModelPrice(input, cache_write, cache_read, output)` in USD per million tokens

**Work:** Fetch the current pricing pages for Anthropic (Opus 5.5, Fable 5.1, Sonnet 5, Haiku 4.5, Opus 5) and OpenAI (gpt-6-astra, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna) with WebFetch, and write the numbers with `fetched = "2026-MM-DD"` and `source = "<url>"` per vendor. Resolution is **longest-prefix match** on the model id, so `claude-opus-5-5` never resolves to the `claude-opus-5` entry and `claude-haiku-4-5-20251001` resolves to Haiku. Unknown model → raise with the id in the message. `<synthetic>` and empty models cost zero.

**Acceptance check:** `uv run pytest tests/test_price.py` — red: import error. Green: a known usage on a known model returns the hand-computed figure; `claude-opus-5-5` resolves to the 5.5 entry; an unknown model raises.

**Boundaries:** No memory-sourced numbers. If a page is unreachable, stop and report `blocked: pricing page <url>`.

- [x] fetch pages, write toml with dates and sources
- [x] `price.py`, tests, commit

---

### Task 7: Store

**Files:**
- `bench/store.py`, `bench/schema.sql` — create
- `tests/test_store.py` — create

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class RecordRow:
      repo: str; pr: int; arm: Arm; source: Source; run_id: str          # "" for history
      title: str; head_branch: str; base_commit: str; reference_commit: str; merged_at: datetime | None
      additions: int; deletions: int; files_changed: int; stratum: str; kind: Literal["whole", "slice"]
      harness_version: str | None; models: list[str]; raw_ask: str
      outcome: Literal["merged", "done", "stalled", "failed"]; test_gate: Literal["pass", "fail", "n/a"]
      extractor_version: str
  @dataclass
  class FindingRow: record_id: int; origin: Literal["judge", "in_process"]; stage: str | None; severity: str | None; confirmed: bool | None; verified: bool | None; text: str; leg: str | None
  @dataclass
  class JudgementRow: record_id: int; judged_at: datetime; legs: str; n_findings: int; status: Literal["ok", "failed"]
  @dataclass
  class RunRow: run_id: str; record_id: int; arm: Arm; task_id: str; started: datetime; ended: datetime; standin_rounds: int; cost_usd: float; wall_s: float; num_turns: int; outcome: str; diff_path: str; result_dir: str
  @dataclass
  class TaskRow: task_id: str; repo: str; pr: int; slice_index: int | None; base_commit: str; reference_commit: str; raw_ask: str; reference_tests: list[str]; kind: Literal["whole", "slice"]
  @dataclass
  class CoverageRow: repo: str; month: str; sessions_seen: int; sessions_unattributed: int
  class Store:
      def __init__(self, path: Path): ...
      def upsert_record(self, r: RecordRow) -> int      # history: replace by (repo, pr, source); run: replace by full key; id preserved
      def replace_phases(self, record_id: int, phases: list[Phase], costs: dict[str, float]) -> None
      def replace_sessions(self, record_id: int, attributions: list[Attribution]) -> None
      def add_finding(self, f: FindingRow) -> int
      def add_judgement(self, j: JudgementRow) -> int
      def add_run(self, r: RunRow) -> int
      def upsert_task(self, t: TaskRow) -> None
      def upsert_coverage(self, c: CoverageRow) -> None
      def add_phase_override(self, o: PhaseOverride) -> None / phase_overrides(session_id) -> list[PhaseOverride]
      def records(self, view: Literal["loosened", "strict", "all"] = "all") -> pd.DataFrame
      def phases(self) / findings(self) / judgements(self) / runs(self) / tasks(self) / coverage(self) -> pd.DataFrame
      def set_meta(self, key: str, value: str) / get_meta(self, key: str) -> str | None
  ```

**Work:** Tables `records`, `phases`, `sessions`, `findings`, `judgements`, `runs`, `tasks`, `coverage`, `phase_overrides`, `meta`. Unique index on `(repo, pr, arm, source, run_id)` with `run_id` NOT NULL DEFAULT ''. `upsert_record` for `source="history"` deletes any existing history row for `(repo, pr)` whose arm differs, then replaces, keeping the row id when the key matches. `records("strict")` filters on the global-constraints rule using only `claude-*` entries of `models`; `"loosened"` returns every record. Schema version in `meta`. Internal structure is your choice.

**Acceptance check:** `uv run pytest tests/test_store.py` — red: import error. Green: upserting the same history record twice yields one row with the same id; a history record re-upserted as `Aprime` leaves no `A` row; strict excludes a record with `claude-opus-5` and includes one with `gpt-6-astra` beside new Claude models; `add_judgement` with zero findings is visible in `judgements()`.

**Boundaries:** No extraction logic.

- [x] schema, `Store`, tests, commit

---

### Task 8: `bench extract` and `bench status`

**Files:**
- `bench/extract.py`, `bench/archive.py` — create
- `bench/cli.py` — modify: wire `extract` and `status`
- `tests/test_extract.py`, `tests/test_archive.py` — create, on the fixture end to end with a temp store and temp archive

**Interfaces:**
- Consumes: Tasks 2 to 7
- Produces:
  ```python
  def archive_transcripts(dirs: list[Path], archive_root: Path, repo: str) -> list[Path]   # copies new or changed files, returns archived dirs
  @dataclass
  class ExtractSummary: records_upserted: int; sessions_seen: int; sessions_unattributed: int; sessions_skipped_old: int
  def extract_repo(repo: RepoConfig, store: Store, prs: list[PR], archive_root: Path, prices: Prices) -> ExtractSummary
  def harness_version_at(ts: datetime) -> str      # git -C ~/.claude log -1 --before=<iso> --format=%h
  def raw_ask_for(sessions: list[tuple[Session, Attribution]]) -> str   # first human turn of the earliest attributed range
  ```

**Work:** The CLI does, per repo: `ensure_mirror`, `sync_prs`, `archive_transcripts` (copy any transcript file whose size or mtime differs into `archive/<repo>/<same relative layout>`, never delete), then `extract_repo` reading **only from the archive**. Skip sessions with `ended < 2026-09-01`. Attribute, group by PR, and for each PR with at least one attribution: `arm_for`, `segment` with stored overrides, price each phase, build `RecordRow(source="history", run_id="", kind="whole", reference_commit=merge_commit, raw_ask=raw_ask_for(...), outcome="merged", test_gate="n/a", models=every model seen)`, upsert, replace phases and sessions. Coverage: upsert one `CoverageRow` per `(repo, month of session start)` with seen and unattributed counts, recomputed from the archive every run. Unattributed sessions are also written to `results/unattributed/<repo>.json` (session id, start, end, branches, first human turn truncated to 200 chars) for Task 9. `bench status` prints pool sizes per view, coverage per repo per month as a rate, tasks without runs, and records without a judgement. `extractor_version` is a constant in `extract.py`, bumped when segmentation rules change.

**Acceptance check:** `uv run pytest tests/test_extract.py tests/test_archive.py` — red: import error. Green on fixture: a second extract after deleting the source transcript still yields the same record from the archive. Live: `uv run bench extract` on both repos completes; `uv run bench status` shows a loosened pool of at least 15 and a strict pool of at least 1; `archive/` holds every transcript file. Paste the status output in the commit body.

**Boundaries:** No model calls. Read-only on transcripts and repos.

- [x] `archive.py`, `extract.py`, wiring, tests
- [x] live run, status, commit with output

---

### Task 9: Claude invocation helper and the model pass

**Files:**
- `bench/claude.py`, `bench/modelpass.py` — create
- `prompts/attribute.md`, `prompts/phase_prefix.md` — create
- `bench/cli.py` — modify: `extract --model-pass` flag
- `tests/test_claude.py`, `tests/test_modelpass.py` — create: command assembly, prompt rendering and response parsing only

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ClaudeCall:
      prompt: str; model: str | None; cwd: Path | None; config_dir: Path | None   # None = Kevin's real ~/.claude
      resume: str | None; effort: str | None; permission_mode: str | None
      setting_sources: str | None; disable_skills: bool; strict_mcp: bool; extra_args: tuple[str, ...]
  def build_argv(call: ClaudeCall) -> tuple[list[str], dict[str, str]]     # argv and env overrides
  def run_claude_json(call: ClaudeCall, runner=subprocess.run) -> dict       # parsed --output-format json
  ISOLATED = dict(config_dir=CONFIG/"empty-claude", setting_sources="project", disable_skills=True, strict_mcp=True)
  def resolve_unattributed(repo, store, prs, archive_root, runner) -> list[Attribution]
  def resolve_phase_prefixes(record_id, sessions, store, runner) -> list[PhaseOverride]
  ```

**Work:** `build_argv` maps fields to real flags: `-p`, `--output-format json`, `--model`, `-r/--resume`, `--effort`, `--permission-mode`, `--setting-sources`, `--disable-slash-commands`, `--strict-mcp-config`; `config_dir` becomes `CLAUDE_CONFIG_DIR` in env; never `--bare`. First live use of the empty config dir will need a login under it: run `CLAUDE_CONFIG_DIR=config/empty-claude claude -p "reply ok" --output-format json` once; if it fails auth, stop and report `blocked: run "CLAUDE_CONFIG_DIR=<abs path> claude login" once` for Kevin, since login is interactive. The attribution prompt gives the PR list (number, title, head branch, window) and one session's human turns and skill args, and asks for `{"pr": <int|null>, "why": "<one line>"}`; resolved attributions get `rule="model_pass"`. The phase-prefix prompt runs for every `unassigned` window longer than 5 human turns or more than 10% of the record's tokens, shows its human turns and tool names, and asks for `{"phase": "<PhaseName>", "why": ...}`; the answer is stored as a `PhaseOverride` and applied by `segment` on the next extract. Both run under `ISOLATED` on Opus at high, only behind `--model-pass`, which the scheduled job never passes.

**Acceptance check:** `uv run pytest tests/test_claude.py tests/test_modelpass.py` — red: import error. Green: `build_argv` for an isolated call contains `--disable-slash-commands`, `--setting-sources project`, `--strict-mcp-config`, sets `CLAUDE_CONFIG_DIR`, and never contains `--bare`; a fake runner returning `{"pr": 12}` produces an attribution to 12; a fake `{"phase": "spec"}` produces an override. No live call in this task.

**Boundaries:** Do not run the model pass live. Do not schedule it.

- [x] `claude.py`, prompts, `modelpass.py`, flag, tests, commit

---

### Task 10: Codex token capture

**Files:**
- `~/.claude/bin/codex-run.sh` — modify: append two columns, `input_tokens` and `output_tokens`, to each registry row; empty when unknown
- `bench/codex.py` — create
- `tests/fixtures/codex_registry.tsv` — create: one verbatim existing row and one nine-column row
- `tests/test_codex.py` — create

**Interfaces:**
- Produces: `CodexRun(ts: datetime (UTC), model: str, exit_code: int, duration_s: float, log_path: Path, description: str, label: str | None, input_tokens: int | None, output_tokens: int | None)`; `load_registry(path=...) -> list[CodexRun]`; `codex_runs_in(window: tuple[datetime, datetime], events: list[Event], registry: list[CodexRun]) -> list[CodexRun]`

**Work:** The registry today has seven tab-separated columns: local timestamp without offset (treat as `America/Sao_Paulo`, convert to UTC), the literal `script`, model, exit code, duration with an `s` suffix, log path, description; the label is the first token after `codex-run.sh` in the description. Parse both shapes. Matching: a run belongs to a window when its timestamp is inside it and its label appears in a `Bash` tool_use input of the events. First establish how codex reports usage: run `~/.claude/bin/codex-run.sh bench-probe -m gpt-5.6-luna -c model_reasoning_effort=low exec "reply with the word ok"` with whatever JSON output flag the codex CLI offers, read the log; if usage is present, parse it in the script and write the two columns; if not, write empty columns and note in the README that codex legs are priced at zero until codex exposes usage. Wire matched runs into `usage_by_model` under the codex model id in `extract.py` (a small change there is inside this task's boundary).

**Acceptance check:** `uv run pytest tests/test_codex.py` — red: import error. Green: both fixture rows parse with the right label and a UTC timestamp; a run whose label appears in a fixture Bash input is matched. The script change is checked by the probe and `tail -1 ~/.claude/codex-runs/registry.tsv` showing nine columns; paste it in the commit body. Commit `~/.claude` separately with a one-line message and push.

**Boundaries:** `codex-run.sh` keeps every existing behaviour and flag; only the registry line changes.

- [x] probe, script edit, verify one row
- [x] `codex.py`, wiring, tests, commit both repos

---

### Task 11: Tasks from PRs

**Files:**
- `bench/tasks.py` — create
- `bench/cli.py` — modify: `tasks add --repo R --pr N [--slice K --goal "..."]`, `tasks list`, `tasks show T`
- `tests/fixtures/plan_three_tasks.md` — create
- `tests/test_tasks.py` — create

**Interfaces:**
- Consumes: `Store`, `PR`, mirror
- Produces: `build_task(repo, pr, record: RecordRow, slice_index: int | None, goal: str | None) -> TaskRow`, writing `tasks/<repo>-<pr>[-s<K>].toml`; `slice_commits(pr: PR, k: int, mirror: Path) -> list[str]`

**Work:** Whole PR: `raw_ask` = the record's stored `raw_ask`; `base_commit` and `reference_commit` from the PR; `reference_tests` = changed files matching `*.test.*`, `*.spec.*`, or under `tests/` or `__tests__/`; `kind="whole"`. Slice: the plan file is the PR's added file under `docs/` whose path contains `plan`; `### Task K:` headings are parsed; `slice_commits` returns the PR's commits whose subject matches `(?i)\btask\s*K\b` in the subject (after any `type(scope):` prefix), and if none match: `blocked: no commit for task K of <repo>#<pr>`. Base is the parent of the first match, reference the last match. `raw_ask` is the heading text plus the `--goal` sentence, which is required for a slice; without it the command prints the heading and the task's Work paragraph and exits 2 so Kevin can write the goal. `tasks add` prints the resulting raw ask.

**Acceptance check:** `uv run pytest tests/test_tasks.py` — red: import error. Green: whole-task fields from a fixture record; the fixture plan yields three headings; `slice_commits` on stubbed subjects `feat(x): task 3: …`, `fix: task 3 follow-up`, `Task 4: …` returns the first two for K=3. Live: `uv run bench tasks add` for the three whole PRs on the spec's shortlist; then for the three plans named there, run `tasks add --slice K` without `--goal` for two candidate tasks each and paste the printed headings in the commit body so Kevin can supply goals.

**Boundaries:** Do not run any arm. Do not invent goal sentences.

- [x] `tasks.py`, CLI, tests
- [x] add the three whole tasks, print slice candidates, commit

---

### Task 12: Runner

**Files:**
- `bench/run.py` — create
- `config/arms/V.toml`, `config/arms/Aprime.toml` — create
- `prompts/standin.txt`, `prompts/suffix.txt` — create, verbatim from the constraints
- `bench/cli.py` — modify: `run --arm V|Aprime --task T... [--judge] [--dry-run]`
- `scripts/make-tiny-repo.sh` — create: builds a two-file git repo at `mirrors/tiny-src` with a base commit (one failing pytest) and a reference commit (the fix), with fixed `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` and identity so the SHAs are stable
- `tasks/tiny.toml` — create, with those SHAs; `config/repos.toml` — modify: add `tiny` with `path = "mirrors/tiny-src"`, `test_cmd = "python3 -m pytest -q"`, no setup
- `tests/test_run.py` — create: command assembly, checkout layout, stall guard and outcome mapping with a fake claude

**Interfaces:**
- Consumes: `TaskRow`, `Store`, `ensure_mirror`, `ClaudeCall`, `run_claude_json`, `extract`'s segment path
- Produces:
  ```python
  @dataclass(frozen=True)
  class ArmConfig: name: Arm; model: str | None; isolated: bool; prompt_prefix: str; effort: str; permission_mode: str
  def checkout(task: TaskRow, run_id: str) -> Path        # git clone --shared mirrors/<repo>.git worktrees/<run_id>, checkout base, branch bench/<run_id>
  def fingerprint(worktree: Path) -> str                  # HEAD sha + `git add -A && git write-tree`
  def run_task(task: TaskRow, arm: ArmConfig, store: Store, dry_run: bool) -> RunRow
  ```

**Work:** Per spec §6 and §7. `checkout` uses `git clone --shared`, so the run's `origin` is the mirror, whose pushes are disabled; the mirror's own remote is untouched. For `V`: write the cached init CLAUDE.md into the worktree and add `CLAUDE.md` to `.git/info/exclude` so it never enters the diff; prepend a sentinel line `# bench-sentinel <run_id>` for the isolation check. The init CLAUDE.md is generated lazily the first time a repo is run: `ClaudeCall(prompt="/init", ...ISOLATED but disable_skills=False, cwd=worktree)` in that task's checkout, cached at `tasks/<repo>/init-claude.md` with a header naming the base commit; if print mode rejects the slash command, use the prompt `Write the CLAUDE.md file /init would write for this repository, and nothing else.` and record which path was taken in the header. Isolation check, once per session before the first `V` run: `ClaudeCall(prompt="Print the first line of every CLAUDE.md you were given, then the word END.", ...ISOLATED, cwd=worktree)` must contain the sentinel and must not contain `## Lanes`; otherwise stop and report `blocked: isolation check failed: <output head>`. `Aprime`: `isolated=False` (Kevin's real config dir, hooks and skills on), model `None`, prefix `Lane: Inline.\n\n`. Loop: first call `ClaudeCall(prompt=prefix + raw_ask + "\n\n" + suffix, model, cwd=worktree, effort, permission_mode="bypassPermissions", plus ISOLATED fields when isolated)`; save every result JSON to `runs/<run_id>/turn-<n>.json`; if `is_error` or the result text matches `usage limit` → outcome `failed`; if `result` does not end with `DONE` → resume with the same arm configuration, `resume=session_id`, and the stand-in text; stall guard on `fingerprint`. After the loop: `git add -A && git diff --cached <base>` to `runs/<run_id>/diff.patch`; copy `task.reference_tests` from the reference commit over the worktree; run `setup_cmd` if set, then `test_cmd`; record `test_gate`. Then measure the run like history: the run's transcript is at `transcript_dir_for(worktree, root=<config_dir>/projects)` (empty config dir for V, `~/.claude/projects` for Aprime); read it with Task 2, `segment` with the arm, match codex runs (Task 10), price, and `upsert_record(RecordRow(source="run", run_id, arm, kind=task.kind, stratum from the reference PR, models, raw_ask, outcome, test_gate, harness_version for Aprime else None))`, replace phases, `add_run`. For runs, `human_turns` = stand-in rounds and `attention_s` = 0. `--dry-run` prints argv, env, prompt and worktree path for the first call and exits without invoking claude. `--judge` chains Task 13.

**Acceptance check:** `uv run pytest tests/test_run.py` — red: import error. Green: argv for `V` has the isolation flags and `--permission-mode bypassPermissions`, for `Aprime` has no `CLAUDE_CONFIG_DIR` override; a fake claude that never says DONE trips the stall guard at three; editing the same file between resumes changes the fingerprint; `is_error` maps to `failed`. Live, in order: `scripts/make-tiny-repo.sh`, the isolation check, then `uv run bench run --arm V --task tiny`, which must end `done` with `test_gate=pass`, a `runs/<id>/diff.patch`, and a run record with an `execution` phase whose usage is non-zero. Paste the run summary in the commit body. `Aprime` is checked with `--dry-run` only.

**Boundaries:** No shortlist task is run. Nothing under `~/.claude` changes. Judge is Task 13.

- [x] arm configs, prompts, `checkout`, `fingerprint`, loop with fake-able claude
- [x] init CLAUDE.md generation, isolation check
- [x] test gate, run-transcript extraction, record upsert, dry-run
- [x] tiny repo script and task, tests, live smoke, commit with the summary

---

### Task 13: Judge

**Files:**
- `bench/judge.py` — create
- `prompts/judge.md`, `prompts/consolidate.md` — create
- `bench/cli.py` — modify: `judge --record ID... [--mark FINDING_ID --verified true|false]`, and `run --judge` chaining
- `tests/test_judge.py` — create: blinding, argv and parsing with fake runners

**Interfaces:**
- Consumes: `Store`, `ClaudeCall`, `run_claude_json`, mirror, `~/.claude/bin/codex-run.sh`
- Produces: `blinded_input(record: RecordRow, diff_text: str) -> str`; `judge_record(store, record_id, claude_runner, codex_runner) -> JudgementRow`; `BLINDED_EXCLUDE = ("docs/", "CLAUDE.md", "CONTEXT.md", "HANDOFF", ".claude/")`

**Work:** The diff for a history record is `git diff <base_commit> <reference_commit>` from the mirror; for a run record it is the saved patch. `blinded_input` drops every file whose path starts with or contains an entry of `BLINDED_EXCLUDE`, keeps no commit messages, branch names, run ids or arm names, and prepends the raw ask. Reviewer leg: `ClaudeCall(prompt=judge.md + input, model="opus", effort="high", ...ISOLATED, cwd=<scratch dir>)` asking for a JSON list of `{"title", "where", "why", "severity_guess"}`. Codex leg, exact argv: `~/.claude/bin/codex-run.sh judge-<record_id> -m gpt-6-astra -c model_reasoning_effort=xhigh -C ~/.claude review "<judge.md text> The input is at <abs path of a temp file holding the blinded input>. Do not edit files."`, parsed from the log the wrapper names. Consolidation: `ClaudeCall` on Opus with `consolidate.md`, both lists in, JSON out with `blocker | major | minor | not-a-defect` and `leg` in `{opus, codex, both}`. Every finding is stored with `origin="judge"`; a `JudgementRow` is stored whether or not there are findings, `status="failed"` when a leg errored. `--mark` sets `verified`. The prompts say nothing about arms or pipelines.

**Acceptance check:** `uv run pytest tests/test_judge.py` — red: import error. Green: blinded input for a fixture diff that adds `docs/plans/x.md`, `CLAUDE.md` and `src/a.ts` contains only the `src/a.ts` hunk and no branch or commit text; the codex argv contains `gpt-6-astra` and `model_reasoning_effort=xhigh`; fake legs with overlapping findings consolidate to the expected count; a fake run with zero findings still stores a judgement. No live judging in this task.

**Boundaries:** Do not judge real records. Legs are plain CLI calls, never the Agent tool.

- [x] prompts, `judge.py`, CLI, tests, commit

---

### Task 14: Caught-in-process extraction

**Files:**
- `bench/caught.py` — create
- `prompts/caught.md` — create
- `bench/cli.py` — modify: `extract --caught --record ID...`
- `tests/test_caught.py` — create

**Interfaces:**
- Consumes: `Session`, `Phase`, `Store`, `ClaudeCall`, `CodexRun`
- Produces: `review_segments(sessions, phases, registry) -> list[Segment(stage, main_text, subagent_outputs: list[str], codex_outputs: list[str])]`; `extract_caught(store, record_id, sessions, runner) -> list[FindingRow]`

**Work:** A segment is a `plan_review` or `pr_review` window: the main session's assistant text and human turns inside it, the final assistant message of each subagent dispatched inside it, and for each codex run matched inside it the review text from its log file if it still exists, else the Bash `tool_result` text of the invoking call. The prompt asks for the findings raised and, per finding, whether the main session confirmed, dismissed, or left it, as JSON. Stored with `origin="in_process"`, `stage`, `confirmed`. Runs under `ISOLATED` on Opus, manual flag only.

**Acceptance check:** `uv run pytest tests/test_caught.py` — red: import error. Green: segments for the Task 5 fixture contain the reviewer subagent's last message and a codex output present only in a Bash tool_result; a fake runner's JSON becomes finding rows with the right stage.

**Boundaries:** No live calls. No scheduling.

- [x] `caught.py`, prompt, CLI, tests, commit

---

### Task 15: Report

**Files:**
- `bench/report.py` — create
- `bench/cli.py` — modify: wire `report`
- `tests/test_report.py` — create, on a hand-built store with records in every arm, source, kind, view and stratum

**Interfaces:**
- Consumes: `Store`
- Produces: `build_report(store) -> str`; `export_csv(store, out_dir: Path) -> list[Path]` writing `records.csv`, `phases.csv`, `findings.csv`, `judgements.csv`, `runs.csv`, `coverage.csv`

**Work:** `results/report.md` with: generated-at and extractor version; pool sizes per view; the headline table grouped by **arm × source × kind × view × stratum** with n, median cost_usd, median attention_s, median human_turns, escaped-major rate (records with any judge finding of severity major or blocker, over records with an `ok` judgement), and cost per record with zero escaped majors; judge precision (verified true over verified not-null); a per-phase cost table for A history; an in-process table for A and A′ history; a runs table; coverage per repo per month; the validity limits from spec §12 verbatim. Cells with n below 3 print the n and no median. History and run rows never share a cell. Use pandas; markdown tables by hand or `DataFrame.to_markdown` if `tabulate` is added with a stated reason.

**Acceptance check:** `uv run pytest tests/test_report.py` — red: import error. Green: the report contains every section heading and the validity text; an `Aprime` history record and an `Aprime` run record land in different rows; a stratum with n=2 shows no median. Live: `uv run bench report` writes `results/report.md`; commit it.

**Boundaries:** No new measures beyond spec §10.

- [x] `report.py`, exports, tests, live report, commit

---

### Task 16: Schedule and final walkthrough

**Files:**
- `bin/install-launchd.sh`, `bin/nightly.sh` — create
- `README.md` — modify: every command, which spend Claude usage, the login step for the empty config dir, the codex pricing note if any
- `bench/cli.py` — modify only if `status` needs a final field

**Interfaces:** none new.

**Work:** `nightly.sh` runs, from the repo root, `uv run bench extract && uv run bench report`, then `git add results && git commit -q -m "results: nightly extract $(date +%F)"` only when `results/` changed; it never pushes. The plist runs `nightly.sh` at 03:30 daily with `EnvironmentVariables` `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` and `uv` by absolute path, logging to `results/extract.log`. `install-launchd.sh` is idempotent: unload if loaded, write, load. Nothing else is scheduled.

**Acceptance check:** `bin/install-launchd.sh && launchctl list | grep harness-bench` shows the job; `launchctl start com.kevin.harness-bench` then `tail -5 results/extract.log` shows extract and report completing and one results commit or "no change"; `git status` clean apart from that commit. Paste both outputs in the commit body.

**Boundaries:** Do not add run, judge or the model pass to the schedule.

- [x] scripts, README, install, one manual fire, commit

---

## Self-review against the spec

- §2 mined vs run, manual-only usage: Tasks 8, 9, 12, 13, 14, 16.
- §3 record fields, pool views, archive: Tasks 7, 8.
- §4 sources, PR filter, first-parent base, pull refs: Tasks 2, 3, 10.
- §5 attribution, arm, per-session phases, dispatch-time subagents, attention, coverage per month, model pass for prefixes: Tasks 4, 5, 8, 9.
- §6 and §7 protocols, isolation without `--bare`, run transcripts measured like history: Tasks 9, 12.
- §8 judge with path exclusion, judgements with zero findings, precision, caught including codex: Tasks 13, 14, 15.
- §9 tasks, subject-mapped slices, Kevin-written goals: Task 11.
- §10 measures, longest-prefix prices: Tasks 6, 7, 15.
- §11 build, CLI, schedule with commit: Tasks 1, 8, 15, 16.
- §12 limits in every report: Task 15.

Names checked across tasks: `Arm`, `Source` (1) everywhere; `Usage`, `Event`, `Session` (2) → 4, 5, 8, 12, 14; `PR`, `ChangedFile` (3) → 4, 5, 8, 11; `Attribution` (4) → 5, 7, 8, 9; `Phase`, `PhaseOverride` (5) → 7, 8, 9, 12, 14; store rows (7) → 8, 11, 12, 13, 14, 15; `ClaudeCall`, `run_claude_json`, `ISOLATED` (9) → 12, 13, 14; `CodexRun` (10) → 12, 14; `ArmConfig`, `run_task` (12) → 13.
