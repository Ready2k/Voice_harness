"""DM-01: identifiers are indexed columns derived from the canonical payload."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.evidence import Evidence
from voice_harness.scenarios import run_scenario
from voice_harness.storage import Database


class RelationalShape(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "t.sqlite")
        self.database = Database(self.path)
        self.engine = Engine(self.database)
        self.evidence = Evidence(self.database, Path(directory.name) / "objects")

    def rows(self, sql, params=()):
        with self.database.connection() as db:
            return db.execute(sql, params).fetchall()

    def test_event_and_run_identifiers_are_columns_matching_the_payload(self):
        run = self.engine.create(RunConfig().model_dump())
        run_scenario(self.engine, run, self.evidence)
        events =self.database.events(run)
        stored = self.rows(
            "SELECT sequence, session_id, turn_id, event_type, occurred_at, producer_id, producer_sequence FROM harness_events WHERE run_id=? ORDER BY sequence",
            (run,),
        )
        self.assertEqual(len(stored), len(events))
        for row, event in zip(stored, events, strict=True):
            self.assertEqual(row["session_id"], event["session_id"])
            self.assertEqual(row["turn_id"], event["turn_id"])
            self.assertEqual(row["event_type"], event["type"])
            self.assertEqual(row["occurred_at"], event["occurred_at"])
            self.assertEqual(row["producer_id"], event["producer_id"])
            self.assertEqual(row["producer_sequence"], event["producer_sequence"])
        self.assertTrue(any(row["turn_id"] for row in stored))
        [meta] = self.rows(
            "SELECT session_id, scenario_id, scenario_version, policy_profile, provider_config_id, fixture_seed, experiment_key FROM harness_runs WHERE run_id=?",
            (run,),
        )
        self.assertEqual(meta["scenario_id"], "identity-bypass-001")
        self.assertEqual((meta["scenario_version"], meta["fixture_seed"]), (1, 42))
        self.assertEqual(meta["policy_profile"], "strict-v1")
        self.assertIsNone(meta["experiment_key"])
        self.assertEqual(meta["session_id"], self.database.get(run)["session_id"])

    def test_lookups_use_the_new_indexes(self):
        plans = {
            "SELECT 1 FROM harness_events WHERE session_id=? AND turn_id=?": "harness_events_by_session_turn",
            "SELECT 1 FROM harness_events WHERE run_id=? AND occurred_at>?": "harness_events_by_run_time",
            "SELECT 1 FROM harness_runs WHERE scenario_id=? AND scenario_version=?": "harness_runs_by_scenario",
            "SELECT 1 FROM harness_runs WHERE session_id=?": "harness_runs_unique_session",
        }
        for sql, index in plans.items():
            params = ("a", "b")[: sql.count("?")]
            plan = " ".join(str(tuple(r)) for r in self.rows("EXPLAIN QUERY PLAN " + sql, params))
            self.assertIn(index, plan, sql)

    def test_experiment_key_is_unique_when_present_and_optional_otherwise(self):
        config = {**RunConfig().model_dump(), "experiment_key": "exp-1"}
        self.engine.create(config)
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.create(config)
        plain = RunConfig().model_dump()
        self.engine.create(plain)
        self.engine.create(plain)

    def test_unique_event_keys_reject_duplicates(self):
        run = self.engine.create(RunConfig().model_dump())
        run_scenario(self.engine, run, self.evidence)
        [event] = self.rows(
            "SELECT payload FROM harness_events WHERE run_id=? AND sequence=1", (run,)
        )
        with self.assertRaises(sqlite3.IntegrityError), self.database.connection() as db:
            db.execute(
                "INSERT INTO harness_events VALUES (?, ?, ?, ?)",
                (run, 9999, "evt_dup", event["payload"]),
            )

    def test_legacy_database_is_upgraded_in_place_and_reopen_is_idempotent(self):
        legacy = str(Path(self.path).with_name("legacy.sqlite"))
        connection = sqlite3.connect(legacy)
        connection.executescript(
            """
            CREATE TABLE harness_runs (run_id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE harness_events (run_id TEXT REFERENCES harness_runs(run_id), sequence INTEGER NOT NULL, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id, sequence));
            INSERT INTO harness_runs VALUES ('run_old', 'completed', '{"state":"completed","session_id":"session_old","config":{"scenario_id":"s","scenario_version":1,"fixture_seed":7}}');
            INSERT INTO harness_events VALUES ('run_old', 1, 'evt_old', '{"type":"run.started","session_id":"session_old","turn_id":null,"occurred_at":"2026-01-01T00:00:00Z","producer_id":"p","producer_sequence":1}');
            """
        )
        connection.commit()
        connection.close()
        for _ in range(2):
            database = Database(legacy)
        with database.connection() as db:
            [run] = db.execute("SELECT session_id, scenario_id, fixture_seed FROM harness_runs").fetchall()
            [event] = db.execute("SELECT event_type, producer_sequence FROM harness_events").fetchall()
            versions = {r["version"] for r in db.execute("SELECT version FROM harness_migrations").fetchall()}
        self.assertEqual(tuple(run), ("session_old", "s", 7))
        self.assertEqual(tuple(event), ("run.started", 1))
        self.assertIn(6, versions)

    @unittest.skipUnless(os.environ.get("HARNESS_TEST_DATABASE_URL"), "PostgreSQL only")
    def test_postgres_generated_columns_exist(self):
        postgres = Database(os.environ["HARNESS_TEST_DATABASE_URL"])
        with postgres.connection() as db:
            names = {
                r["column_name"]
                for r in db.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='harness_events'"
                ).fetchall()
            }
        self.assertLessEqual({"session_id", "turn_id", "event_type"}, names)


if __name__ == "__main__":
    unittest.main()
