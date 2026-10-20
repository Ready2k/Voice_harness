"""Recent manual sessions listing (read-only, operator-authenticated)."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.engine import Engine
from voice_harness.storage import Database


class ManualHistory(unittest.TestCase):
    def test_listing_is_authenticated_validated_and_only_shows_manual_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Database(str(Path(directory) / "t.sqlite")), directory, "op")
            with TestClient(app) as client:
                h = {"Authorization": "Bearer op"}
                self.assertEqual(client.get("/api/v1/manual-sessions").status_code, 401)
                self.assertEqual(client.get("/api/v1/manual-sessions?limit=0", headers=h).status_code, 422)
                client.post("/api/v1/runs", headers={**h, "Idempotency-Key": "k"}, json={"scenario_id": "identity-bypass-001"})
                self.assertEqual(client.get("/api/v1/manual-sessions", headers=h).json(), {"items": []})

    def test_manual_sessions_survive_newer_non_manual_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "t.sqlite"))
            app = create_app(database, directory, "op")
            engine = Engine(database)
            manual_ids = []
            for index in range(12):
                run_id = engine.create({
                    "scenario_id": "identity-bypass-001", "input_mode": "manual", "journey_id": "j%d" % index,
                    "path": "happy", "playback_mode": "gated", "provider_config_id": "mock",
                }, "manual-%d" % index)
                with database.transaction(run_id) as tx:
                    tx.state["state"] = "completed"
                    tx.state["assurance"] = {
                        "overall_assurance": "pass", "model_behaviour": "pass", "platform_enforcement": "pass",
                    }
                manual_ids.append(run_id)
            for index in range(510):  # newer than every manual run, more than the old 500-row scan window
                engine.create({"scenario_id": "identity-bypass-001"}, "auto-%d" % index)
            with TestClient(app) as client:
                h = {"Authorization": "Bearer op"}
                items = client.get("/api/v1/manual-sessions", headers=h).json()["items"]
                self.assertEqual(len(items), 10)
                self.assertTrue(all(i["journey_id"] and i["overall_assurance"] == "pass" for i in items))
                self.assertEqual(items[0]["platform_enforcement"], "pass")
                self.assertEqual(len(client.get("/api/v1/manual-sessions?limit=3", headers=h).json()["items"]), 3)
                self.assertFalse({i["run_id"] for i in items} - set(manual_ids))


if __name__ == "__main__":
    unittest.main()
