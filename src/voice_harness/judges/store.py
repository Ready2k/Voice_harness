"""Migration 12: review queue projection and calibration records (SQLite and PostgreSQL).

Applied lazily and idempotently on first use so it stays independent of the other
migration modules. The canonical record of reviews is the ledger; the queue table is an
index over it, written in the same transaction as the events.
"""

from __future__ import annotations

import threading

MIGRATION_ID = 12
_LOCK = threading.Lock()


def migrate(db, payload_type: str):
    for statement in [
        "CREATE TABLE IF NOT EXISTS harness_review_queue (review_id TEXT PRIMARY KEY, run_id TEXT REFERENCES harness_runs(run_id), evaluation_id TEXT NOT NULL, criterion_id TEXT NOT NULL, reason TEXT NOT NULL, severity TEXT NOT NULL, state TEXT NOT NULL, claimed_by TEXT, opened_event_id TEXT REFERENCES harness_events(event_id), decided_event_id TEXT REFERENCES harness_events(event_id), created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL)",
        "CREATE INDEX IF NOT EXISTS harness_review_queue_by_state ON harness_review_queue(state, created_at)",
        f"CREATE TABLE IF NOT EXISTS harness_calibration_policies (policy_hash TEXT PRIMARY KEY, policy_id TEXT NOT NULL, version INTEGER NOT NULL, definition {payload_type} NOT NULL, created_at DOUBLE PRECISION NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS harness_calibration_approvals (policy_hash TEXT PRIMARY KEY REFERENCES harness_calibration_policies(policy_hash), approver_id TEXT NOT NULL, role TEXT NOT NULL, rationale TEXT NOT NULL, approved_at DOUBLE PRECISION NOT NULL, definition {payload_type} NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS harness_calibration_reports (report_id TEXT PRIMARY KEY, policy_hash TEXT NOT NULL REFERENCES harness_calibration_policies(policy_hash), holdout_hash TEXT NOT NULL, judge_fingerprint TEXT NOT NULL, enablement TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, definition {payload_type} NOT NULL)",
    ]:
        db.execute(statement)
    db.execute(f"INSERT INTO harness_migrations VALUES ({MIGRATION_ID}) ON CONFLICT DO NOTHING")


def ensure(database):
    if getattr(database, "_judges_ready", False):
        return
    with _LOCK:
        if getattr(database, "_judges_ready", False):
            return
        with database.connection() as db:
            migrate(db, "JSONB" if database.postgres else "TEXT")
        database._judges_ready = True
