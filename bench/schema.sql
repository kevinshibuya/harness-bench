CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL, pr INTEGER NOT NULL, arm TEXT NOT NULL, source TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    title TEXT, head_branch TEXT, base_commit TEXT, reference_commit TEXT, merged_at TEXT,
    additions INTEGER, deletions INTEGER, files_changed INTEGER, stratum TEXT, kind TEXT,
    harness_version TEXT, models TEXT, raw_ask TEXT, outcome TEXT, test_gate TEXT,
    extractor_version TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS records_key ON records (repo, pr, arm, source, run_id);

CREATE TABLE IF NOT EXISTS phases (
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    name TEXT NOT NULL, start TEXT, "end" TEXT, wall_s REAL, human_turns INTEGER, attention_s REAL,
    agent_ids TEXT, session_ids TEXT,
    usage TEXT,  -- JSON {model: {input, cache_write, cache_read, output}}
    cost_usd REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL, rule TEXT, start_uuid TEXT, end_uuid TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    origin TEXT NOT NULL, stage TEXT, severity TEXT, confirmed INTEGER, verified INTEGER, text TEXT, leg TEXT
);

CREATE TABLE IF NOT EXISTS judgements (
    id INTEGER PRIMARY KEY,
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    judged_at TEXT, legs TEXT, n_findings INTEGER, status TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, record_id INTEGER REFERENCES records(id), arm TEXT, task_id TEXT,
    started TEXT, ended TEXT, standin_rounds INTEGER, cost_usd REAL, wall_s REAL, num_turns INTEGER,
    outcome TEXT, diff_path TEXT, result_dir TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY, repo TEXT, pr INTEGER, slice_index INTEGER, base_commit TEXT,
    reference_commit TEXT, raw_ask TEXT, reference_tests TEXT, kind TEXT
);

CREATE TABLE IF NOT EXISTS coverage (
    repo TEXT NOT NULL, month TEXT NOT NULL, sessions_seen INTEGER, sessions_unattributed INTEGER,
    sessions_excluded INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, month)
);

CREATE TABLE IF NOT EXISTS phase_overrides (
    session_id TEXT NOT NULL, start_uuid TEXT NOT NULL, phase TEXT NOT NULL,
    PRIMARY KEY (session_id, start_uuid)
);

CREATE TABLE IF NOT EXISTS model_pass_attributions (
    session_id TEXT PRIMARY KEY, pr INTEGER, start_uuid TEXT NOT NULL, end_uuid TEXT NOT NULL, why TEXT
);
