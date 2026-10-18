"""Indexed evidence projections, atomically derived from canonical events."""

import hashlib
import json


def migrate(db, payload_type):
    statements = [
        "CREATE TABLE IF NOT EXISTS harness_turns (turn_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), session_id TEXT NOT NULL, generation_epoch INTEGER NOT NULL, start_event_id TEXT REFERENCES harness_events(event_id))",
        f"CREATE TABLE IF NOT EXISTS harness_provider_capability_reports (report_id TEXT PRIMARY KEY, provider TEXT NOT NULL, model_id TEXT NOT NULL, profile TEXT NOT NULL, checked_at TEXT NOT NULL, definition {payload_type} NOT NULL)",
        "CREATE TABLE IF NOT EXISTS harness_audio_artifacts (artifact_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), audio_hash TEXT NOT NULL, size_bytes INTEGER NOT NULL, kind TEXT NOT NULL, stored_event_id TEXT REFERENCES harness_events(event_id))",
        "CREATE TABLE IF NOT EXISTS harness_transcript_segments (revision_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), response_id TEXT NOT NULL, text_value TEXT NOT NULL, text_hash TEXT NOT NULL, finality INTEGER NOT NULL, source TEXT NOT NULL, start_sample INTEGER NOT NULL, end_sample INTEGER NOT NULL, event_id TEXT REFERENCES harness_events(event_id))",
        "CREATE TABLE IF NOT EXISTS harness_guardrail_decisions (decision_id TEXT PRIMARY KEY REFERENCES harness_events(event_id), run_id TEXT REFERENCES harness_runs(run_id), target_id TEXT NOT NULL, action TEXT NOT NULL, policy_version TEXT NOT NULL, target_hash TEXT, created_at TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS harness_fixture_snapshots (snapshot_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), seed INTEGER NOT NULL, content_hash TEXT NOT NULL, definition {payload_type} NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS harness_scenario_definitions (scenario_id TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL, definition {payload_type} NOT NULL, PRIMARY KEY(scenario_id, version))",
        "CREATE TABLE IF NOT EXISTS harness_judge_runs (judge_run_id TEXT PRIMARY KEY REFERENCES harness_events(event_id), run_id TEXT REFERENCES harness_runs(run_id), judge_model TEXT NOT NULL, rubric_version TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS harness_judge_findings (finding_id TEXT PRIMARY KEY, judge_run_id TEXT REFERENCES harness_judge_runs(judge_run_id), run_id TEXT REFERENCES harness_runs(run_id), criterion_id TEXT NOT NULL, criterion_version INTEGER NOT NULL, verdict TEXT NOT NULL, severity TEXT NOT NULL, evidence {payload_type} NOT NULL, rationale TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS harness_metric_observations (observation_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), event_id TEXT REFERENCES harness_events(event_id), metric TEXT NOT NULL, value_ms DOUBLE PRECISION NOT NULL, start_ns BIGINT NOT NULL, end_ns BIGINT NOT NULL, clock_domain TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS harness_evidence_manifests (manifest_hash TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), terminal_state TEXT NOT NULL, ledger_root TEXT, event_count INTEGER NOT NULL, definition {payload_type} NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS harness_human_reviews (review_id TEXT PRIMARY KEY, finding_id TEXT REFERENCES harness_judge_findings(finding_id), reviewer_id TEXT NOT NULL, disposition TEXT NOT NULL, rationale TEXT NOT NULL, created_at TEXT NOT NULL, evidence {payload_type} NOT NULL)",
        "CREATE INDEX IF NOT EXISTS harness_findings_by_run_criterion ON harness_judge_findings(run_id, criterion_id)",
        "CREATE INDEX IF NOT EXISTS harness_metrics_by_run_name ON harness_metric_observations(run_id, metric)",
    ]
    for statement in statements:
        db.execute(statement)
    db.execute("INSERT INTO harness_migrations VALUES (3) ON CONFLICT DO NOTHING")
    # v0.3 projections: preservation holds, simulator decisions, confirmation events.
    for statement in [
        f"CREATE TABLE IF NOT EXISTS harness_preservation_holds (hold_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), owner_id TEXT NOT NULL, requester_id TEXT NOT NULL, scope_type TEXT NOT NULL, scope {payload_type} NOT NULL, access_classification TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL, released_at DOUBLE PRECISION, release_reason TEXT, audit_event_id TEXT REFERENCES harness_events(event_id))",
        f"CREATE TABLE IF NOT EXISTS harness_simulator_decisions (decision_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), event_id TEXT UNIQUE REFERENCES harness_events(event_id), policy_hash TEXT NOT NULL, previous_node TEXT NOT NULL, next_node TEXT NOT NULL, observed_event_ids {payload_type} NOT NULL, predicate_results {payload_type} NOT NULL, selected_branch_id TEXT NOT NULL, selection_method TEXT NOT NULL, selector_model_config_hash TEXT, seed INTEGER, random_draw DOUBLE PRECISION, output_event_id TEXT REFERENCES harness_events(event_id))",
        f"CREATE TABLE IF NOT EXISTS harness_confirmation_events (event_id TEXT PRIMARY KEY REFERENCES harness_events(event_id), run_id TEXT REFERENCES harness_runs(run_id), event_type TEXT NOT NULL, request_id TEXT, observation_id TEXT, confirmation_id TEXT, proposal_hash TEXT, session_id TEXT, idempotency_key TEXT, evidence_event_ids {payload_type} NOT NULL)",
        "CREATE UNIQUE INDEX IF NOT EXISTS harness_consumption_per_confirmation ON harness_confirmation_events(confirmation_id) WHERE event_type='confirmation.consumed'",
        "CREATE UNIQUE INDEX IF NOT EXISTS harness_consumption_per_key ON harness_confirmation_events(idempotency_key) WHERE event_type='confirmation.consumed'",
    ]:
        db.execute(statement)
    db.execute("INSERT INTO harness_migrations VALUES (4) ON CONFLICT DO NOTHING")
    # Migration 5: rows written before declarative scenarios existed were provider
    # placeholders, not scenario definitions; drop them so versions stay immutable.
    for row in db.execute(
        "SELECT scenario_id, version, definition FROM harness_scenario_definitions"
    ).fetchall():
        definition = row["definition"]
        definition = json.loads(definition) if isinstance(definition, str) else definition
        if "turns" not in definition:
            db.execute(
                "DELETE FROM harness_scenario_definitions WHERE scenario_id=? AND version=?",
                (row["scenario_id"], row["version"]),
            )
    db.execute("INSERT INTO harness_migrations VALUES (5) ON CONFLICT DO NOTHING")
    from . import clock_runtime

    clock_runtime.migrate(db)
    from . import indexing

    indexing.migrate(db, payload_type)
    from . import cost_store

    cost_store.migrate(db, payload_type)


def upsert_hold(db, database, run_id, hold):
    scope, update = database.encoded(hold["scope"]), (
        hold["expires_at"],
        hold["released_at"],
        hold["release_reason"],
    )
    db.execute(
        "INSERT INTO harness_preservation_holds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(hold_id) DO UPDATE SET expires_at=excluded.expires_at, released_at=excluded.released_at, release_reason=excluded.release_reason",
        (
            hold["hold_id"],
            run_id,
            hold["owner_id"],
            hold["requester_id"],
            hold["scope"]["type"],
            scope,
            hold["access_classification"],
            hold["created_at"],
            *update,
            hold["audit_event_id"],
        ),
    )


def project(transaction, event):
    db, database, state = transaction.db, transaction.database, transaction.state
    content, kind, run = event.content, event.type, event.run_id

    def insert(table, values):
        db.execute(
            f"INSERT INTO {table} VALUES ({','.join('?' for _ in values)}) ON CONFLICT DO NOTHING",
            values,
        )

    if kind == "run.started":
        insert(
            "harness_provider_capability_reports",
            (
                "mock-fixture-v1",
                "mock",
                "scripted-fixture-v1",
                "controlled_fixture",
                event.occurred_at,
                database.encoded(
                    {
                        "audio_rate": 24000,
                        "codec": "pcm_s16le",
                        "tool_transport": "gateway_bridge",
                        "semantic_safety": "not_measured",
                        "live_provider": False,
                    }
                ),
            ),
        )
        snapshot = {
            "seed": content["fixture_seed"],
            "balance_minor": state["balance_minor"],
            "version": 1,
        }
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        insert(
            "harness_fixture_snapshots",
            (
                state["fixture_snapshot_id"],
                run,
                snapshot["seed"],
                hashlib.sha256(encoded).hexdigest(),
                database.encoded(snapshot),
            ),
        )
        from .scenario_catalogue import scenario_definition
        from .scenario_defs import definition_hash

        definition = scenario_definition(state["config"].get("scenario_id", "identity-bypass-001"))
        insert(
            "harness_scenario_definitions",
            (
                definition["id"],
                definition["version"],
                definition_hash(definition),
                database.encoded(definition),
            ),
        )
    elif kind == "turn.started":
        insert(
            "harness_turns",
            (
                content["turn_id"],
                run,
                state["session_id"],
                state["generation_epoch"],
                event.event_id,
            ),
        )
    elif kind == "audio.output":
        text = content["transcript"]
        insert(
            "harness_transcript_segments",
            (
                content["transcript_revision_id"],
                run,
                content["response_id"],
                text,
                hashlib.sha256(text.encode()).hexdigest(),
                int(content["final"]),
                "independent_fixture",
                content["start_sample"],
                content["end_sample"],
                event.event_id,
            ),
        )
    elif kind.startswith("clock."):
        from . import clock_runtime

        clock_runtime.project(db, event)
    elif kind == "transcript.revised":
        insert(
            "harness_transcript_segments",
            (
                content["new_revision_id"],
                run,
                state["units"][content["unit_id"]]["response_id"],
                content["new"],
                hashlib.sha256(content["new"].encode()).hexdigest(),
                1,
                content["source"],
                content["affected_sample_range"][0],
                content["affected_sample_range"][1],
                event.event_id,
            ),
        )
    elif kind == "artifact.stored":
        insert(
            "harness_audio_artifacts",
            (
                run + ":" + content["sha256"],
                run,
                content["sha256"],
                content["size"],
                content["kind"],
                event.event_id,
            ),
        )
    elif kind == "decision":
        insert(
            "harness_guardrail_decisions",
            (
                event.event_id,
                run,
                content.get("call_id", content.get("unit_id", "unknown")),
                content["action"],
                content.get("policy_version", "strict-v1"),
                content.get("argument_hash", content.get("audio_sha256")),
                event.occurred_at,
            ),
        )
    elif kind == "simulator.branch_selected":
        insert(
            "harness_simulator_decisions",
            (
                content["decision_id"],
                run,
                event.event_id,
                content["branch_policy_hash"],
                content["previous_node"],
                content["next_node"],
                database.encoded(content["observed_event_ids"]),
                database.encoded(content["predicate_results"]),
                content["selected_branch_id"],
                content["selection_method"],
                content["selector_model_config_hash"],
                content["seed"],
                content["random_draw"],
                None,
            ),
        )
    elif kind == "simulator.utterance_generated":
        db.execute(
            "UPDATE harness_simulator_decisions SET output_event_id=? WHERE decision_id=?",
            (event.event_id, content["decision_id"]),
        )
    elif kind in {
        "confirmation.requested",
        "customer.confirmation_observed",
        "confirmation.accepted",
        "confirmation.rejected",
        "confirmation.consumed",
    }:
        insert(
            "harness_confirmation_events",
            (
                event.event_id,
                run,
                kind,
                content.get("request_id"),
                content.get("observation_id"),
                content.get("confirmation_id"),
                content.get("proposal_hash"),
                content.get("session_id", state["session_id"]),
                content.get("idempotency_key"),
                database.encoded(content.get("evidence_event_ids", [])),
            ),
        )
    elif kind == "judge.completed":
        insert(
            "harness_judge_runs",
            (
                event.event_id,
                run,
                "deterministic-fixture-v1",
                "0.3",
                "completed",
                event.occurred_at,
            ),
        )
        for finding in content.get("findings", []):
            insert(
                "harness_judge_findings",
                (
                    event.event_id + ":" + finding["criterion_id"],
                    event.event_id,
                    run,
                    finding["criterion_id"],
                    finding["criterion_version"],
                    finding["verdict"],
                    finding["severity"],
                    database.encoded(finding["evidence_event_ids"]),
                    finding["rationale"],
                ),
            )


def metrics(database, observations):
    rows = []
    for observation in observations:
        for span in observation["stage_spans_ns"]:
            rows.append(
                (
                    observation["event_id"] + ":" + span["name"],
                    observation["run_id"],
                    observation["event_id"],
                    span["name"],
                    (span["end_ns"] - span["start_ns"]) / 1e6,
                    span["start_ns"],
                    span["end_ns"],
                    observation["clock_domain_id"],
                )
            )
    if rows:
        with database.connection() as db:
            sql = "INSERT INTO harness_metric_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING"
            cursor = db.db.cursor()
            try:
                cursor.executemany(
                    sql.replace("?", "%s") if database.postgres else sql, rows
                )
            finally:
                cursor.close()
