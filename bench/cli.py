import argparse
import sys

SUBCOMMANDS = {
    "extract": "archive transcripts, attribute, segment, price and upsert records",
    "report": "regenerate out/report.md and the CSV exports",
    "run": "run an arm on a task (spends Claude usage)",
    "judge": "judge records blindly (spends Claude and codex usage)",
    "tasks": "build and list replayable tasks",
    "status": "pool sizes, coverage, tasks without runs, records without judgement",
}


def cmd_extract(args) -> int:
    import subprocess

    from bench import archive, extract as extract_mod, prs as prs_mod, store as store_mod
    from bench.codex import codex_hook, load_registry
    from bench.config import ARCHIVE, load_repos, transcript_dirs_for
    from bench.price import load_prices
    from bench.types import Blocked

    repos = load_repos()
    store, prices = store_mod.Store(), load_prices()
    names = [args.repo] if args.repo else [n for n in repos if n != "tiny"]
    if args.caught:
        if not args.record:
            print("--caught needs --record ID...")
            return 2
        records = store.records()
        wanted = records[records["id"].isin(args.record) & (records["source"] == "history")]
        names = sorted(set(wanted["repo"]))
    # Archive first: nothing that can fail (gh, the network) may cost a transcript its 30 days.
    for name in names:
        archive.archive_transcripts(transcript_dirs_for(repos[name]), ARCHIVE, name)
    archive.archive_codex_logs()
    codex = codex_hook(load_registry(), priced=prices.knows)
    for name in names:
        repo = repos[name]
        try:
            prs = prs_mod.sync_prs(repo)
        except (subprocess.CalledProcessError, Blocked, OSError) as e:
            prs = prs_mod.load_prs(repo)
            print(f"{name}: gh failed ({e}); using {len(prs)} cached PRs")
        if not prs:
            print(f"{name}: no PRs, skipped")
            continue

        def extract():
            return extract_mod.extract_repo(repo, store, prs, ARCHIVE, prices,
                                extra_attributions=store.model_pass_attributions(), codex=codex)

        s = extract()
        if args.model_pass:
            from bench.modelpass import resolve_phase_prefixes, resolve_unattributed

            resolved = resolve_unattributed(repo, store, prs, ARCHIVE)
            print(f"{name}: model pass attributed {len(resolved)} sessions")
            s = extract()
            records = store.records()
            n_overrides = 0
            for number, sessions in s.groups.items():
                row = records[(records.repo == name) & (records.pr == number) & (records.source == "history")]
                n_overrides += len(resolve_phase_prefixes(int(row.iloc[0]["id"]), sessions, store,
                                                          arm=s.arms[number]))
            print(f"{name}: model pass labelled {n_overrides} unassigned prefixes")
            s = extract()
        if args.caught:
            from bench.caught import extract_caught

            for row in wanted[wanted["repo"] == name].itertuples():
                found = extract_caught(store, int(row.id), s.groups.get(int(row.pr), []))
                print(f"record {row.id}: {len(found)} in-process findings, "
                      f"{sum(1 for f in found if f.confirmed)} confirmed")
        print(f"{name}: {s.records_upserted} records, {s.sessions_seen} sessions seen, "
              f"{s.sessions_unattributed} unattributed, {s.sessions_excluded} excluded, "
              f"{s.sessions_skipped_old} skipped as old")
    return 0


def cmd_status(args) -> int:
    from bench import store as store_mod
    from bench.extract import EXTRACTOR_VERSION

    store = store_mod.Store()
    print("pool sizes")
    for view in ("loosened", "strict"):
        df = store.records(view)
        split = df.groupby(["arm", "source"]).size().to_dict() if len(df) else {}
        detail = ", ".join(f"{a}/{s} {n}" for (a, s), n in sorted(split.items()))
        print(f"  {view}: {len(df)}" + (f" ({detail})" if detail else ""))
    print("coverage (attributed sessions / sessions seen)")
    for row in store.coverage().itertuples():
        rate = 1 - row.sessions_unattributed / row.sessions_seen if row.sessions_seen else 0
        print(f"  {row.repo} {row.month}: {rate:.0%} of {row.sessions_seen}"
              + (f", {row.sessions_excluded} excluded" if row.sessions_excluded else ""))
    tasks, runs = store.tasks(), store.runs()
    idle = sorted(set(tasks["task_id"]) - set(runs["task_id"])) if len(tasks) else []
    print(f"tasks without runs: {len(idle)}" + (f" ({', '.join(idle)})" if idle else ""))
    records, judged = store.records(), store.judgements()
    if len(records):
        hist = records[records["source"] == "history"]
        stale = int((hist["extractor_version"].astype(str) != EXTRACTOR_VERSION).sum())
        print(f"stale history records (extractor version below {EXTRACTOR_VERSION}): {stale}")
    unjudged = sorted(set(records["id"]) - set(judged["record_id"])) if len(records) else []
    print(f"records without judgement: {len(unjudged)} of {len(records)}")
    return 0


def cmd_tasks(args) -> int:
    from bench.config import load_repos
    from bench.mirrors import ensure_mirror
    from bench.prs import load_prs
    from bench.store import Store
    from bench.tasks import NeedsGoal, build_task, load_task, task_path
    from bench.types import Blocked

    store = Store()
    if args.action == "list":
        for t in store.tasks().itertuples():
            print(f"{t.task_id}\t{t.kind}\t{t.raw_ask.splitlines()[0][:80] if t.raw_ask else ''}")
        return 0
    if args.action == "show":
        path = task_path(args.task_id)
        print(path.read_text() if path.exists() else load_task(args.task_id, store))
        return 0
    repo = load_repos()[args.repo]
    pr = next((p for p in load_prs(repo) if p.number == args.pr), None)
    records = store.records()
    row = records[(records.repo == args.repo) & (records.pr == args.pr) & (records.source == "history")]
    if pr is None or row.empty:
        print(f"no merged PR with a history record: {args.repo}#{args.pr} (run bench extract first)")
        return 2
    ensure_mirror(repo)
    try:
        text = args.goal if args.slice is not None else args.ask
        task = build_task(repo, pr, store.record(int(row.iloc[0]["id"])), args.slice, text)
    except NeedsGoal as need:
        flag = '--goal "<one sentence>"' if args.slice is not None else '--ask "<the task, as you would ask it>"'
        print(f"{args.repo}#{args.pr} {need.heading}\n\n{need.work}\n\nre-run with {flag}")
        return 2
    except Blocked as b:
        print(f"blocked: {b}")
        return 2
    store.upsert_task(task)
    print(f"{task.task_id}\n{task.raw_ask}")
    return 0


def cmd_run(args) -> int:
    from bench.run import load_arm, run_task
    from bench.store import Store
    from bench.tasks import load_task
    from bench.types import Blocked

    store, arm, state = Store(), load_arm(args.arm), {}
    for task_id in args.task:
        task = load_task(task_id, store)
        try:
            row = run_task(task, arm, store, args.dry_run, state=state)
        except Blocked as b:
            print(f"blocked: {b}")
            return 2
        if row is None:
            continue
        print(f"{row.run_id}: {row.outcome}, test gate {store.record(row.record_id).test_gate}, "
              f"{row.standin_rounds} stand-in rounds, ${row.cost_usd:.2f}, {row.wall_s:.0f}s, "
              f"record {row.record_id}, diff {row.diff_path}")
        if args.judge:
            from bench.judge import judge_cli

            judge_cli(store, [row.record_id])
    return 0


def cmd_judge(args) -> int:
    from bench.judge import judge_cli
    from bench.store import Store

    store = Store()
    if args.mark is not None:
        if args.verified is None:
            print("--mark needs --verified true|false")
            return 2
        store.set_verified(args.mark, args.verified == "true")
        print(f"finding {args.mark}: verified={args.verified}")
        return 0
    if not args.record:
        print("give --record ID... or --mark FINDING_ID --verified true|false")
        return 2
    return judge_cli(store, args.record)


def cmd_report(args) -> int:
    from bench.report import write_report
    from bench.store import Store

    print(f"wrote {write_report(Store())}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description="harness-bench")
    sub = parser.add_subparsers(dest="command", required=True)
    parsers = {name: sub.add_parser(name, help=help_text) for name, help_text in SUBCOMMANDS.items()}
    parsers["extract"].add_argument("--repo")
    parsers["extract"].add_argument("--model-pass", action="store_true",
                                    help="resolve unattributed sessions and long unassigned prefixes (spends Claude usage)")
    parsers["extract"].add_argument("--caught", action="store_true",
                                    help="mine in-process review findings for --record IDs (spends Claude usage)")
    parsers["extract"].add_argument("--record", type=int, nargs="+")
    parsers["extract"].set_defaults(func=cmd_extract)
    parsers["status"].set_defaults(func=cmd_status)
    tasks = parsers["tasks"].add_subparsers(dest="action", required=True)
    add = tasks.add_parser("add", help="build a task from a merged PR")
    add.add_argument("--repo", required=True)
    add.add_argument("--pr", type=int, required=True)
    add.add_argument("--slice", type=int)
    add.add_argument("--goal", help="slice: Kevin's goal sentence")
    add.add_argument("--ask", help="whole PR: Kevin's ask, as he would give it")
    tasks.add_parser("list")
    show = tasks.add_parser("show")
    show.add_argument("task_id")
    parsers["tasks"].set_defaults(func=cmd_tasks)
    run = parsers["run"]
    run.add_argument("--arm", required=True, choices=["V", "Aprime"])
    run.add_argument("--task", required=True, nargs="+")
    run.add_argument("--judge", action="store_true", help="judge each run record after it finishes")
    run.add_argument("--dry-run", action="store_true", help="print the first call and exit")
    run.set_defaults(func=cmd_run)
    judge = parsers["judge"]
    judge.add_argument("--record", type=int, nargs="+")
    judge.add_argument("--mark", type=int, metavar="FINDING_ID")
    judge.add_argument("--verified", choices=["true", "false"])
    judge.set_defaults(func=cmd_judge)
    parsers["report"].set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
