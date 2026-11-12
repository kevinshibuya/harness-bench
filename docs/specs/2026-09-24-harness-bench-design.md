# harness-bench: design

Date: 2026-09-24. Grilled on Fable 5.1 in a session in one of the target repos, approved by Kevin in chat. Names of the private target repos, their PRs and quoted prompts were removed on 2026-11-12 before publishing.

## 1. The question

How do three ways of working with Claude Code compare on cost, on Kevin's attention, and on defects, on the current models (Opus 5.5 for execution, Fable 5.1 for planning)?

- **A, specs**: the full custom pipeline as practised. Grill on Fable, spec, plan, plan review wave, execution on Opus, three-leg PR review.
- **A′, inline**: the custom harness loaded (CLAUDE.md, skills, agents, hooks, codex legs), task done in the Inline lane: no grill, no spec, no plan.
- **V, vanilla**: stock Claude Code. No user CLAUDE.md, no skills, no agents, no hooks, no codex. One session, one prompt.

The first report answers "keep the whole architecture or drop it" and "what does the spec stage buy". Arms are config bundles, so ablation arms (no plan review, no PR review, no model routing) are later additions to the same runner, not a new benchmark.

## 2. What is mined and what is run

| arm | source | cost to Kevin |
| --- | --- | --- |
| A | transcripts of real work, mined | none |
| A′ | transcripts of real inline work, mined; runnable on demand | Claude usage only when Kevin runs it |
| V | run only, never mined | Claude usage when Kevin runs it |

**Nothing that spends Claude usage runs on a schedule.** Kevin picks the arm and the task set and runs them from the CLI when the usage windows allow. What runs on a schedule is mechanical: extraction of new transcripts into records, cost arithmetic, report regeneration.

## 3. The record

One record per merged PR. A record carries:

- identity: repo, PR number, title, head branch, base commit, merge commit, merged-at, reference size (lines added and deleted, files touched)
- arm, and how it was decided (see §5)
- harness version: the `~/.claude` commit current at the first session's date
- models used, by phase, with token counts per model per phase
- per phase: wall clock, human turns, capped attention seconds, cost at list price
- in-process findings (A and A′ only): count raised, count confirmed, by review stage
- judge findings (all arms): count by severity, and the test-gate result for V
- provenance: the session ids attributed, the sessions dropped as unattributable, the extractor version

### The pool

Two views of the same store:

- **Loosened**: PRs whose planning ran on Fable 5.1, execution model tagged. Today this is about fifteen PRs in one target repo and eight in the other, from 2026-09-01 on.
- **Strict**: PRs whose sessions ran entirely on Opus 5.5 and Fable 5.1. Two records today. It grows with Kevin's normal work.

Opus 5 records are stored but reported only inside the loosened view, tagged. Nothing older than Fable 5.1 (2026-09-01) is a record.

Transcripts are deleted by Claude Code 30 days after their last write and Kevin keeps that setting. The scheduled extractor therefore archives every transcript it sees into the bench repo's gitignored `archive/` before reading it, and always reads from the archive. A PR whose transcripts age out before the extractor ever ran is lost; everything the archive holds can be re-extracted after a rule change, and a record never degrades when one of its sessions expires on disk.

## 4. Sources

Transcripts live under `~/.claude/projects/<escaped-cwd>/`. Facts established on 2026-09-24:

- One `.jsonl` per session, plus `<sessionId>/subagents/agent-<id>.jsonl` per dispatched agent.
- Every line carries `type`, `timestamp`, `cwd`, `gitBranch`, `sessionId`, `parentUuid`, `uuid`, `version`. Subagent lines add `agentId` and `isSidechain: true`.
- Assistant lines carry `message.model`, `effort`, `requestId`, and `message.usage` with `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`.
- Skill invocations are `tool_use` blocks named `Skill` with `input.skill` and `input.args`.
- Agent dispatches are `tool_use` blocks named `Agent` with `input.subagent_type`, `input.model`, `input.description`. The matching `tool_result` user line carries `toolUseResult.agentId`, which names the subagent file, and `toolUseResult.resolvedModel`.
- A human turn is a user line whose `message.content` is a string, with `isMeta` not true, and whose content does not start with `<`. Tool results are user lines with array content. Lines of other types (`attachment`, `bridge-session`, `last-prompt`, `mode`, `ai-title`, `pr-link`, `queue-operation`, `system`, `cost-state`, and so on) are ignored except `pr-link`, which is read as an attribution hint.
- Codex runs are logged by `~/.claude/bin/codex-run.sh` to `~/.claude/codex-runs/registry.tsv` (timestamp, label, model, exit code, duration, log path, description). Tokens are recorded only for `exec --json` runs, summed from codex's `turn.completed` usage events; `review` and plain `exec` runs carry none (§12).
- PR metadata comes from `gh pr list --state merged` in each target repo, kept only for PRs whose base is the integration branch and whose head is neither the default nor the integration branch, so promotion PRs are never records. Commits and dates come from the mirror's `refs/pull/<N>/head`, which a `--mirror` clone of GitHub carries. A PR's **base commit is the first parent of its merge commit** and its reference diff is `merge^1..merge`, whatever the merge strategy; the merge-base is not used, since it drags in unrelated integration-branch changes.

Target repos, and their transcript directories:

- two of Kevin's private repos, listed in the gitignored `config/repos.local.toml` (transcripts from 2026-08-22 and from 2026-09-10), including the worktree transcript directories that Claude Code creates beside them

## 5. Attribution and phases

### Session to PR

Sessions named in the repo's `exclude_sessions` list in the repos config (each entry a session id and a written reason) are dropped whole before any rule runs. The list is kept by hand. Then, in order, first match wins:

1. `gitBranch` on the session's lines equals a PR's head branch.
2. A `pr-link` line names the PR.
3. The session's time span falls inside one PR's window (branch first commit to merge) and the files it edits overlap that PR's changed files.
4. Otherwise the session is queued for the model pass: an Opus session with a fixed prompt reads the session's human turns and skill arguments (spec and plan file names name their feature) and returns a PR number or "none".

A session can attribute to more than one PR when it spans a handoff; the split point is the last human turn before the branch changes. Sessions that end unattributed are dropped and counted per repo per month so the drop rate is visible. Excluded sessions are counted apart from them, in their own column.

**Amendment, 2026-09-25 (Kevin's ruling):** one session ran in a target repo on a feature branch, and rule 1 gave it to that branch's PR, but its content was the grilling and planning of harness-bench itself, so the PR carried this benchmark's design cost and 46 review findings about it. A scan of every archived session in both target repos found no other case. Hence `exclude_sessions`.

### Arm

A record is **A** when any of its sessions invoke `grilling`, `grill-with-docs`, `grill-me`, `writing-plans` or `executing-plans`, or when a spec or plan file under `docs/` was added by the PR. Otherwise it is **A′**. Records produced by the runner are tagged by the runner and skip this rule.

### Phase

Phases are windows inside a record's timeline, opened by markers:

| marker | phase opened |
| --- | --- |
| a grill skill call | spec |
| `writing-plans` | plan |
| `Agent` with `subagent_type: reviewer` or `codex-review` skill, before any `executing-plans` | plan review |
| `executing-plans`, or the first `Edit`/`Write` to a source file in an A′ record | execution |
| `reviewer` dispatch or `codex-review` after execution began | PR review |
| `pickup` | continues the phase the handoff left open |

Everything before the first marker in a session is `unassigned`; the model pass assigns it when it is more than a few turns. A phase window's cost is the usage of the main session's assistant messages inside it plus the whole usage of every subagent dispatched inside it, priced per model.

### Human attention

Per phase and per record:

- `human_turns`: count of human turns.
- `attention_s`: sum over human turns of (turn timestamp minus previous assistant timestamp), each term capped at 600 s.

## 6. Vanilla run protocol

For a task (see §9) the runner:

1. Creates a worktree of the target repo at the PR's base commit, from a bench-owned mirror clone so no real branch or PR can be created. The worktree name carries the run id.
2. Writes a project CLAUDE.md into the worktree: what `/init` would produce for that repo, generated once per repo at the base commit and cached under `tasks/<repo>/init-claude.md`. Nothing from `~/.claude` is present.
3. Runs `claude -p` with: `--model` the arm's execution model, `CLAUDE_CONFIG_DIR` pointed at an empty bench-owned folder that has been logged in once under Kevin's subscription, `--setting-sources project`, `--disable-slash-commands`, `--strict-mcp-config`, `--output-format json`, `--permission-mode bypassPermissions`, `--effort high`. `--bare` is not used: it reads only an API key, never the subscription login, and hides every CLAUDE.md, so it would bill real dollars and defeat step 2. The isolation check before the first run: a sentinel line in the worktree CLAUDE.md must be reported back, and the "Lanes" heading of Kevin's global CLAUDE.md must not be.
4. The prompt is the task's raw ask followed by one fixed suffix shared by every arm: "Work in this checkout only. Do not push and do not open a PR. If you have a question, ask it and stop. When the change is complete, commit it and end your reply with the single word DONE."
5. If the run ends without DONE, the runner resumes the session with the fixed stand-in reply: "Go with your recommendation. Treat this as my go, and as GREEN, wherever one is needed." The loop repeats until DONE. There is no turn or dollar cap. The only guard is a stall guard: three consecutive resumes with no change to the worktree end the run as `stalled`. The guard exists so a model that never says DONE cannot run all night; it is not a budget.
6. The runner records every JSON result (cost, duration, turns, usage per model), the number of stand-in rounds, and the final diff. It runs the reference tests (the tests as they exist in the merged PR, copied over the arm's output) and records pass or fail as the test gate.

## 7. Inline (A′) run protocol

Same as §6 with these differences: the config directory is Kevin's real `~/.claude`, hooks and skills on, the main session is pinned to V's execution model (`--model claude-opus-5-5`, in `config/arms/Aprime.toml`), and the prompt is prefixed with "Lane: Inline." The stand-in reply is the same. The harness's PR review runs on the branch diff, because the suffix forbids a PR. Runner-produced A′ records are tagged `run`, mined ones `history`, and the report never pools them silently.

A run against the real harness dispatches reviewer agents including a Fable leg and codex legs; the runner does not suppress them, that is the arm. The pin covers the main session only: what the harness dispatches runs on the models the harness chooses.

**Amendment, 2026-09-25 (Kevin's ruling):** until this date the main session's model was "whatever the harness picks", which is the `model` default in `~/.claude/settings.json`: an alias (`fable`, then `opus`, then `opus[1m]`, then `opus` in September alone) that follows Kevin's day-to-day preference and resolves to a newer Opus when one ships. V is pinned to an exact model id, so A′ is pinned to the same one; A′ against V then measures the harness at equal execution model, and the two would otherwise drift apart without anyone changing the benchmark.

## 8. Judge

One blinded judging pass per record, on the final diff:

- input: the raw ask, and the diff with commit messages and branch names stripped, and with `docs/**`, `CLAUDE.md`, `CONTEXT.md`, `HANDOFF*.md` and `.claude/**` removed for every arm, since specs, plans and the injected CLAUDE.md identify the arm on sight. The A diff is `merge^1..merge`. The V and A′ run diffs are the checkout's staged diff against base, so a stalled run is judged on what it had, not on an empty diff. A judgement is recorded even when it finds nothing.
- legs: `reviewer` on Opus at high, and `codex-review` on astra at xhigh, each with the same fixed review prompt stored in the repo.
- consolidation: one Opus pass deduplicates the two legs' findings and tags each `blocker`, `major`, `minor`, or `not-a-defect`, with the rule text stored in the repo.
- Kevin hand-verifies the top findings on a sample of records; verified findings are marked so the report can show the judge's precision.

"Escaped" is the headline accuracy: findings of severity major or above on the final output, per record, across all arms. "Caught in-process" is mined for A and A′ history records only: the findings each review wave raised and the main session confirmed, extracted by an Opus pass with a fixed prompt over the review-wave segments of the transcript and the subagent outputs.

The judge is a Claude run and is therefore a manual command, with a flag to chain it after a run.

Jev (TypeSafe's decision model, released 2026-09-15) was considered as a judge and deferred. It scores and ranks but does not generate findings, and the pool is too small for it to pay. Possible future fit: severity gating once records pass a few hundred.

## 9. Tasks

A task is a PR-shaped unit that the runner can replay: repo, base commit, raw ask, reference diff, reference tests, slice or whole.

- **Whole PR**: raw ask is written by Kevin when he adds the task (amended below; the first human turn was the original rule).
- **Slice**: one numbered task from a plan file. Commits map to tasks by subject (`task K:` in any case, with or without a `feat(...)` prefix); base is the parent of the first matching commit, reference is the last matching commit, and a task with no matching commit is not sliceable. Raw ask is the task's heading plus one goal sentence that Kevin writes when adding the slice, never the plan's steps, because real Work paragraphs open with implementation prescriptions. The report marks slices, since the heading leaks a little of the plan.

Shortlist for the first runs, all with an admissible history record and under about 1,500 reference lines:

- one whole PR from the first target repo
- two whole PRs from the second target repo
- two slices from one plan in the second target repo
- ~~one slice each from two plans in the first target repo~~ (not sliceable, see the amendment)

Exact slice choice happens at execution after reading the plan files.

**Amendment, 2026-09-24 (Kevin's ruling at execution):** the mined first human turn is not a replayable ask. For the three whole PRs above it was a status question, a GREEN given to an earlier answer, and a request to pick up a handoff file, because the grill ran in an earlier session no rule attributes. A whole-PR task's raw ask is therefore written by Kevin when he adds it (`bench tasks add --ask`), as a slice's goal sentence is. The mined turn stays on the record as provenance. The two plans in the first repo carry no `task K` in any commit subject, so they are not sliceable and leave the shortlist; the two slices are tasks 2 and 5 of their plan. New small PRs become tasks as they merge; Kevin runs them when he chooses.

## 10. Measures

Per record and per phase:

| measure | definition |
| --- | --- |
| tokens | input, cache write, cache read, output, per model |
| cost_usd | tokens times the vendor's list price per model, from `config/prices.toml`. One column. It is a reference number, not what Kevin pays. |
| wall_s | last timestamp minus first, per phase, summed |
| human_turns, attention_s | §5 |
| in_process_raised, in_process_confirmed | §8, A and A′ history only |
| escaped_major, escaped_total | §8, all arms |
| test_gate | pass, fail, or n/a |
| outcome | merged, done, stalled, failed |

Prices are filled from the vendors' pricing pages at plan execution, with the date recorded in the file, and never from memory.

The headline table is per arm, split by pool view and by reference-size stratum (small under 500 lines, medium under 1,500, large above): median cost, median attention, escaped-major rate, and cost per record with zero escaped majors.

## 11. Build

Python 3.12 with `uv`, `pandas`, `argparse`, `sqlite3`, `pytest`. Repo layout:

```
harness-bench/
  bench/            package: extract, attribute, phases, price, run, judge, report
  config/           prices.toml, arms/*.toml, repos.example.toml
  prompts/          stand-in reply, judge prompt, consolidation rules, model-pass prompts
  tasks/            one toml per task, plus cached init CLAUDE.md per repo
  out/              bench.sqlite, exports/*.csv, report.md (ignored, see the amendment)
  runs/             per-run JSON results and diffs (committed, small)
  mirrors/          bare clones of target repos (ignored)
  worktrees/        run checkouts (ignored)
  docs/specs, docs/plans
  tests/
```

CLI, one entry point `bench`:

- `bench extract [--repo R]`: scan transcripts, attribute, segment, price, upsert records. Idempotent. Scheduled.
- `bench report`: regenerate `out/report.md` and the CSV exports. Scheduled after extract.
- `bench run --arm V|Aprime --task T [--task T2 …] [--judge]`: manual. A run's own transcript (written under the arm's config directory) is extracted with the same pipeline as history, so run records get phases and codex legs priced the same way.
- `bench judge --record ID [--record ID2 …]`: manual.
- `bench tasks add --repo R --pr N (--ask "…" | --slice K --goal "…")`: build a task from a merged PR.
- `bench status`: pool sizes per view, drop rate, tasks without runs, records without judgement.

The schedule is one launchd agent that runs `bench extract && bench report` daily. It spends no Claude usage.

The store is `out/bench.sqlite`, append-only per (repo, pr, arm, source, run_id). CSV exports and the markdown report are derived from it.

**Amendment, 2026-11-12:** the output directory was `results/`, committed by the nightly job. It is now `out/`, gitignored, and the job never commits or pushes. The store and the PR caches hold verbatim prompts and details of private target repos, which cannot go into a public repo. The cost is that the results no longer travel with the code: they live on the machine that runs the job.

## 12. Validity limits, stated in every report

- A′ history tasks were inline because they were small and clear. Comparisons are stratified by reference size and the confound is still there.
- The loosened pool mixes Opus 5 and Opus 5.5 execution. The strict view is the clean one and is small.
- Mined records are one sample each; runs are one sample each until Kevin repeats them. The report shows n everywhere.
- The judge is two models with fixed prompts. Its precision is known only on the hand-verified sample.
- The stand-in reply is not Kevin. It approves everything and never redirects.
- Slices leak the plan's task heading.
- The blinded diff drops `docs/**` and the harness files for every arm, so a task whose deliverable was documentation is judged on its code only.
- Codex `review` and plain `exec` legs are priced at zero: the codex wrapper records tokens only under `exec --json`. A and A′ costs are lower bounds; V runs no codex and is unaffected.
- Rule 1 attributes a session by its checkout's branch, not by what it worked on. An off-topic session is dropped only when someone spots it and lists it in `exclude_sessions`; one has been found, by hand, and nothing scans for the next.

## 13. Out of scope

Prospective double-runs of A, scheduled Claude usage of any kind, Jev, a dashboard, PRs older than the transcript window, a second execution model per arm, ablation arms. All are additions to this design, not replacements.

## 14. Glossary

- **record**: one merged PR's measurements under one arm from one source.
- **arm**: a way of working, defined by a config bundle.
- **pool view**: the subset of records a table is computed over (loosened, strict).
- **task**: a replayable PR-shaped unit for the runner.
- **stand-in**: the fixed reply that takes Kevin's place in a run.
- **escaped / caught**: judge findings on the final output / findings the pipeline's own reviews confirmed.
