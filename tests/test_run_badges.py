"""Run-list membership fields: bake-off, review findings and active holds (additive, batched)."""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.bakeoff import BakeoffStore
from voice_harness.engine import Engine
from voice_harness.judges import store as judge_store
from voice_harness.storage import Database


class RunBadges(unittest.TestCase):
    def test_membership_fields_are_batched_and_default_to_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "t.sqlite"))
            app = create_app(database, directory, "op")
            engine = Engine(database)
            ids = [engine.create({"scenario_id": "identity-bypass-001"}, "k%d" % i) for i in range(4)]
            plain, bake, find, held = ids
            judge_store.ensure(database)
            store = BakeoffStore(database)
            old = {"bakeoff_id": "bko_old", "created_at": 1.0, "state": "done",
                   "cells": [{"attempts": [{"run_id": bake}, {"run_id": None, "pending": True}]}]}
            new = {"bakeoff_id": "bko_new", "created_at": 2.0, "state": "done",
                   "cells": [{"attempts": [{"run_id": bake}]}]}
            with database.connection() as db:
                for p in (old, new):
                    db.execute("INSERT INTO harness_bakeoffs VALUES (?, ?, ?, ?)",
                               (p["bakeoff_id"], p["created_at"], p["state"], database.encoded(p)))
                for n in range(2):
                    db.execute("INSERT INTO harness_review_queue VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               ("rev_%d" % n, find, "ev", "FACT-01", "r", "high", "open", None, None, None, 1.0, 1.0))
            self.assertIsNotNone(store)
            with TestClient(app) as client:
                h = {"Authorization": "Bearer op"}
                for key in ("live", "released"):
                    hold = client.post(f"/api/v1/runs/{held}/preservation-holds", headers=h, json={
                        "reason": "audit", "owner_id": "rev", "run_wide": True, "idempotency_key": key,
                        "expires_at": time.time() + 1000}).json()
                    if key == "released":
                        client.post(f"/api/v1/runs/{held}/preservation-holds/{hold['hold_id']}/release",
                                    headers=h, json={"reason": "done"})
                items = {i["run_id"]: i for i in client.get("/api/v1/runs", headers=h).json()["items"]}
        fields = lambda r: (items[r]["bakeoff_id"], items[r]["finding_count"], items[r]["active_hold_count"])
        self.assertEqual(fields(plain), (None, 0, 0))
        self.assertEqual(fields(bake), ("bko_new", 0, 0))
        self.assertEqual(fields(find), (None, 2, 0))
        self.assertEqual(fields(held), (None, 0, 1))  # the released hold is not counted


if __name__ == "__main__":
    unittest.main()
