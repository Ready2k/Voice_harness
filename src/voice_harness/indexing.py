"""Migration 6 (DM-01): identifiers promoted to indexed, derived columns.

Run, session, turn and event identifiers stay inside the canonical JSON payload
(the ledger hashes that payload), and are exposed as GENERATED columns so they
can never drift from it. Generated columns also need no backfill, which matters
on PostgreSQL where an UPDATE trigger makes ledger rows immutable. Works on
SQLite (>= 3.31, virtual generated columns) and PostgreSQL (stored).
"""

# (table, column, sqlite JSON path, postgres json accessor, postgres cast)
EVENT_COLUMNS = (
    ("session_id", "$.session_id", "payload->>'session_id'", "TEXT"),
    ("turn_id", "$.turn_id", "payload->>'turn_id'", "TEXT"),
    ("response_id", "$.response_id", "payload->>'response_id'", "TEXT"),
    ("event_type", "$.type", "payload->>'type'", "TEXT"),
    ("occurred_at", "$.occurred_at", "payload->>'occurred_at'", "TEXT"),
    ("producer_id", "$.producer_id", "payload->>'producer_id'", "TEXT"),
    (
        "producer_sequence",
        "$.producer_sequence",
        "(payload->>'producer_sequence')::bigint",
        "BIGINT",
    ),
)
RUN_COLUMNS = (
    ("session_id", "$.session_id", "payload->>'session_id'", "TEXT"),
    ("scenario_id", "$.config.scenario_id", "payload->'config'->>'scenario_id'", "TEXT"),
    (
        "scenario_version",
        "$.config.scenario_version",
        "(payload->'config'->>'scenario_version')::bigint",
        "BIGINT",
    ),
    ("policy_profile", "$.config.policy_profile", "payload->'config'->>'policy_profile'", "TEXT"),
    (
        "provider_config_id",
        "$.config.provider_config_id",
        "payload->'config'->>'provider_config_id'",
        "TEXT",
    ),
    (
        "fixture_seed",
        "$.config.fixture_seed",
        "(payload->'config'->>'fixture_seed')::bigint",
        "BIGINT",
    ),
    ("experiment_key", "$.config.experiment_key", "payload->'config'->>'experiment_key'", "TEXT"),
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS harness_events_by_session_turn ON harness_events(session_id, turn_id)",
    "CREATE INDEX IF NOT EXISTS harness_events_by_run_time ON harness_events(run_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS harness_events_by_run_event_type ON harness_events(run_id, event_type)",
    "CREATE UNIQUE INDEX IF NOT EXISTS harness_events_unique_producer_sequence ON harness_events(run_id, producer_id, producer_sequence)",
    "CREATE UNIQUE INDEX IF NOT EXISTS harness_runs_unique_session ON harness_runs(session_id)",
    "CREATE INDEX IF NOT EXISTS harness_runs_by_scenario ON harness_runs(scenario_id, scenario_version, state)",
    "CREATE INDEX IF NOT EXISTS harness_runs_by_provider ON harness_runs(provider_config_id, policy_profile)",
    # A benchmark experiment (scenario/seed/policy/provider/repetition) may be
    # recorded once; ordinary runs carry no experiment key and are unconstrained.
    "CREATE UNIQUE INDEX IF NOT EXISTS harness_runs_unique_experiment ON harness_runs(experiment_key) WHERE experiment_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS harness_turns_by_run_session ON harness_turns(run_id, session_id)",
    "CREATE INDEX IF NOT EXISTS harness_tool_calls_by_state ON harness_tool_calls(run_id, state)",
)


def _existing(db, table, postgres):
    if postgres:
        rows = db.execute(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name=?",
            (table,),
        ).fetchall()
    else:
        rows = db.execute(f"SELECT name FROM pragma_table_xinfo('{table}')").fetchall()
    return {row["name"] for row in rows}


def _add(db, table, columns, postgres):
    present = _existing(db, table, postgres)
    for name, sqlite_path, pg_expression, pg_type in columns:
        if name in present:
            continue
        if postgres:
            db.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {pg_type} GENERATED ALWAYS AS ({pg_expression}) STORED"
            )
        else:
            db.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} GENERATED ALWAYS AS (json_extract(payload, '{sqlite_path}')) VIRTUAL"
            )


def migrate(db, payload_type):
    postgres = payload_type == "JSONB"
    _add(db, "harness_events", EVENT_COLUMNS, postgres)
    _add(db, "harness_runs", RUN_COLUMNS, postgres)
    for statement in INDEXES:
        db.execute(statement)
    db.execute("INSERT INTO harness_migrations VALUES (6) ON CONFLICT DO NOTHING")
