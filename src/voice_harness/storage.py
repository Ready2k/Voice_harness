"""Transactional run namespaces and append-only canonical evidence."""

from __future__ import annotations

import atexit
import copy
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .events import Event

_POOLS = {}
_POOL_LOCK = threading.Lock()


def _pool(url):
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    with _POOL_LOCK:
        key = (url, os.environ.get("HARNESS_DB_PASSWORD_FILE"))
        if key not in _POOLS:
            options = {"row_factory": dict_row}
            if key[1]:
                options["password"] = Path(key[1]).read_text().strip()
            pool = ConnectionPool(
                url, kwargs=options, min_size=1, max_size=20, timeout=10, open=True
            )
            pool.wait(timeout=10)
            _POOLS[key] = pool
            atexit.register(pool.close)
        return _POOLS[key]


class Database:
    def __init__(self, url: str):
        self.url = url
        self.postgres = url.startswith("postgresql://")
        self.lock = threading.RLock()
        self.fail_writes = False
        with self.connection() as db:
            if self.postgres:
                # API and MCP workers can start together. Serialize schema DDL across
                # processes; PostgreSQL cannot concurrently replace the same function.
                db.execute("SELECT pg_advisory_xact_lock(782104319)")
            payload_type = "JSONB" if self.postgres else "TEXT"
            for statement in [
                f"CREATE TABLE IF NOT EXISTS harness_runs (run_id TEXT PRIMARY KEY, state TEXT NOT NULL, payload {payload_type} NOT NULL)",
                f"CREATE TABLE IF NOT EXISTS harness_events (run_id TEXT REFERENCES harness_runs(run_id), sequence INTEGER NOT NULL, event_id TEXT UNIQUE NOT NULL, payload {payload_type} NOT NULL, PRIMARY KEY(run_id, sequence))",
                "CREATE TABLE IF NOT EXISTS harness_requests (request_key TEXT PRIMARY KEY, body_hash TEXT NOT NULL, run_id TEXT REFERENCES harness_runs(run_id))",
                "CREATE TABLE IF NOT EXISTS harness_migrations (version INTEGER PRIMARY KEY)",
                "CREATE TABLE IF NOT EXISTS harness_sessions (session_id TEXT PRIMARY KEY, run_id TEXT UNIQUE REFERENCES harness_runs(run_id), identity_state TEXT NOT NULL, identity_version INTEGER NOT NULL, identity_expires DOUBLE PRECISION NOT NULL)",
                f"CREATE TABLE IF NOT EXISTS harness_tool_calls (run_id TEXT REFERENCES harness_runs(run_id), call_id TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL, argument_hash TEXT NOT NULL, state TEXT NOT NULL, result {payload_type} NOT NULL, PRIMARY KEY(run_id, call_id))",
                "CREATE TABLE IF NOT EXISTS harness_approval_units (unit_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), state TEXT NOT NULL, audio_hash TEXT NOT NULL, generation_epoch INTEGER NOT NULL, assessment_epoch INTEGER NOT NULL, end_sample INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS harness_confirmations (confirmation_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), proposal_hash TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL, consumed INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS harness_outbox (run_id TEXT REFERENCES harness_runs(run_id), call_id TEXT NOT NULL, argument_hash TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(run_id, call_id))",
                "CREATE INDEX IF NOT EXISTS harness_events_by_run_id ON harness_events(run_id, event_id)",
            ]:
                db.execute(statement)
            db.execute(
                "INSERT INTO harness_migrations VALUES (1) ON CONFLICT DO NOTHING"
            )
            db.execute(
                "INSERT INTO harness_migrations VALUES (2) ON CONFLICT DO NOTHING"
            )
            from .relational import migrate

            migrate(db, payload_type)
            if self.postgres:
                db.execute(
                    "CREATE INDEX IF NOT EXISTS harness_events_by_type ON harness_events(run_id, (payload->>'type'))"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS harness_producer_sequence ON harness_events(run_id, (payload->>'producer_id'), (payload->>'producer_sequence'))"
                )
                db.execute(
                    """CREATE OR REPLACE FUNCTION harness_reject_evidence_edit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'UPDATE' AND current_setting('harness.retention_purge', true) = 'on' AND NEW.run_id = OLD.run_id AND NEW.sequence = OLD.sequence AND NEW.event_id = OLD.event_id AND NEW.payload->>'event_hash' = OLD.payload->>'event_hash' AND NEW.payload->>'prev_hash' IS NOT DISTINCT FROM OLD.payload->>'prev_hash' AND OLD.payload->'tombstone' IS NULL AND NEW.payload->'tombstone' IS NOT NULL THEN RETURN NEW; END IF; RAISE EXCEPTION 'APPEND_ONLY_EVIDENCE'; END $$"""
                )
                # A single idempotent trigger protects all existing ledger rows.
                db.execute(
                    """DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='harness_events_immutable') THEN CREATE TRIGGER harness_events_immutable BEFORE UPDATE OR DELETE ON harness_events FOR EACH ROW EXECUTE FUNCTION harness_reject_evidence_edit(); END IF; END $$"""
                )

    @contextmanager
    def connection(self):
        if self.postgres:
            with _pool(self.url).connection() as connection:
                yield _Connection(connection, True)
        else:
            with self.lock:
                connection = sqlite3.connect(self.url, timeout=10)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                try:
                    with connection:
                        yield _Connection(connection, False)
                finally:
                    connection.close()

    def encoded(self, value):
        if self.postgres:
            from psycopg.types.json import Jsonb

            return Jsonb(value)
        return json.dumps(value, sort_keys=True, allow_nan=False)

    @staticmethod
    def decoded(value):
        return json.loads(value) if isinstance(value, str) else value

    def create(self, run_id: str, payload: dict, *, request_key=None, body_hash=None):
        if self.fail_writes:
            raise OSError("EVIDENCE_UNAVAILABLE")
        with self.connection() as db:
            if request_key:
                # Serializes creation by key across processes without holding a run lock.
                if self.postgres:
                    db.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(?))", (request_key,)
                    )
                else:
                    db.execute("BEGIN IMMEDIATE")
                old = db.execute(
                    "SELECT * FROM harness_requests WHERE request_key=?", (request_key,)
                ).fetchone()
                if old:
                    if old["body_hash"] != body_hash:
                        raise ValueError("IDEMPOTENCY_CONFLICT")
                    return old["run_id"]
            db.execute(
                "INSERT INTO harness_runs VALUES (?, ?, ?)",
                (run_id, payload["state"], self.encoded(payload)),
            )
            if request_key:
                db.execute(
                    "INSERT INTO harness_requests VALUES (?, ?, ?)",
                    (request_key, body_hash, run_id),
                )
        return run_id

    @contextmanager
    def transaction(self, run_id):
        if self.fail_writes:
            raise OSError("EVIDENCE_UNAVAILABLE")
        with self.connection() as db:
            if not self.postgres:
                db.execute("BEGIN IMMEDIATE")
            suffix = " FOR UPDATE" if self.postgres else ""
            row = db.execute(
                "SELECT payload FROM harness_runs WHERE run_id=?" + suffix, (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError("RUN_NOT_FOUND")
            state = copy.deepcopy(self.decoded(row["payload"]))
            transaction = RunTransaction(self, db, run_id, state)
            yield transaction
            if self.fail_writes:
                raise OSError("EVIDENCE_UNAVAILABLE")
            db.execute(
                "UPDATE harness_runs SET state=?, payload=? WHERE run_id=?",
                (state["state"], self.encoded(state), run_id),
            )
            db.execute(
                "INSERT INTO harness_sessions VALUES (?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET identity_state=excluded.identity_state, identity_version=excluded.identity_version, identity_expires=excluded.identity_expires",
                (
                    state["session_id"],
                    run_id,
                    state["identity"],
                    state["identity_version"],
                    state["identity_expires"],
                ),
            )
            for call_id, call in state["calls"].items():
                db.execute(
                    "INSERT INTO harness_outbox VALUES (?, ?, ?, ?) ON CONFLICT(run_id, call_id) DO UPDATE SET state=excluded.state",
                    (run_id, call_id, call["request_hash"], call["state"]),
                )
                db.execute(
                    "INSERT INTO harness_tool_calls VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, call_id) DO UPDATE SET state=excluded.state, result=excluded.result",
                    (
                        run_id,
                        call_id,
                        run_id + ":" + call_id,
                        call["request_hash"],
                        call["state"],
                        self.encoded(call["result"]),
                    ),
                )
            for unit_id, unit in state["units"].items():
                db.execute(
                    "INSERT INTO harness_approval_units VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(unit_id) DO UPDATE SET state=excluded.state, assessment_epoch=excluded.assessment_epoch",
                    (
                        unit_id,
                        run_id,
                        unit["state"],
                        unit["audio_sha256"],
                        unit["generation_epoch"],
                        unit["assessment_epoch"],
                        unit["end_sample"],
                    ),
                )
            for confirmation_id, confirmation in state["confirmations"].items():
                db.execute(
                    "INSERT INTO harness_confirmations VALUES (?, ?, ?, ?, ?) ON CONFLICT(confirmation_id) DO UPDATE SET consumed=excluded.consumed",
                    (
                        confirmation_id,
                        run_id,
                        confirmation["hash"],
                        confirmation["expires_at"],
                        int(confirmation["consumed"]),
                    ),
                )

    def get(self, run_id):
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM harness_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError("RUN_NOT_FOUND")
            return self.decoded(row["payload"])

    def events(self, run_id, after=0, limit=None):
        with self.connection() as db:
            sql = "SELECT payload FROM harness_events WHERE run_id=? AND sequence>? ORDER BY sequence"
            parameters = [run_id, after]
            if limit is not None:
                sql += " LIMIT ?"
                parameters.append(limit)
            return [
                self.decoded(r["payload"])
                for r in db.execute(sql, parameters).fetchall()
            ]


class _Connection:
    def __init__(self, db, postgres):
        self.db, self.postgres = db, postgres

    def execute(self, sql, parameters=()):
        return self.db.execute(
            sql.replace("?", "%s") if self.postgres else sql, parameters
        )


class RunTransaction:
    def __init__(self, database, db, run_id, state):
        self.database, self.db, self.run_id, self.state = database, db, run_id, state

    def event(self, source, kind, content, *, causes=()):
        if self.database.fail_writes:
            raise OSError("EVIDENCE_UNAVAILABLE")
        from .contracts import EVENT_CONTRACTS

        if kind in EVENT_CONTRACTS:
            EVENT_CONTRACTS[kind].model_validate(content)

        def evidence_ids(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"native_envelope", "provider_event"}:
                        continue  # Untrusted native payload is evidence data.
                    if key in {
                        "evidence_event_ids",
                        "presentation_event_ids",
                        "observed_event_ids",
                    }:
                        yield from item
                    else:
                        yield from evidence_ids(item)
            elif isinstance(value, list):
                for item in value:
                    yield from evidence_ids(item)

        for event_id in evidence_ids(content):
            if (
                self.db.execute(
                    "SELECT 1 FROM harness_events WHERE run_id=? AND event_id=?",
                    (self.run_id, event_id),
                ).fetchone()
                is None
            ):
                raise ValueError("INVALID_EVIDENCE_REFERENCE")
        last = self.db.execute(
            "SELECT sequence, payload FROM harness_events WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
            (self.run_id,),
        ).fetchone()
        sequence = last["sequence"] + 1 if last else 1
        previous = (
            self.database.decoded(last["payload"])["event_hash"] if last else None
        )
        for event_id in causes:
            if (
                self.db.execute(
                    "SELECT 1 FROM harness_events WHERE run_id=? AND event_id=?",
                    (self.run_id, event_id),
                ).fetchone()
                is None
            ):
                raise ValueError("INVALID_EVIDENCE_REFERENCE")
        event = Event(
            self.run_id,
            source,
            kind,
            copy.deepcopy(content),
            session_id=self.state["session_id"],
            turn_id=self.state.get("turn_id"),
            response_id=content.get("response_id", self.state.get("response_id")),
            sequence=sequence,
            producer_sequence=sequence,
            connection_epoch=max(1, self.state.get("connection_epoch", 1)),
            causation_event_ids=list(causes),
        )
        if (
            content.get("tool") == "verify_identity"
            and content.get("arguments", {}).get("answer") == "[REDACTED]"
        ):
            event.redaction = {
                "applied": True,
                "fields": ["content.arguments.answer"],
                "reasons": {"content.arguments.answer": "CHALLENGE_ANSWER_SECRET"},
            }
        event.seal(previous)
        self.db.execute(
            "INSERT INTO harness_events VALUES (?, ?, ?, ?)",
            (
                self.run_id,
                sequence,
                event.event_id,
                self.database.encoded(event.to_dict()),
            ),
        )
        from .relational import project

        project(self, event)
        from .telemetry import observe

        observe(event.to_dict())  # best-effort; never raises
        return event.event_id
