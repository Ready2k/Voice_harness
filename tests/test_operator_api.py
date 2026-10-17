"""Read endpoints behind the operator UI and hold review (FN-03, HD-04)."""

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.storage import Database


class OperatorApi(unittest.TestCase):
    def test_run_list_hold_register_and_static_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Database(str(Path(directory) / "t.sqlite")), directory, "op")
            with TestClient(app) as client:
                h = {"Authorization": "Bearer op"}
                self.assertEqual(client.get("/api/v1/runs").status_code, 401)
                self.assertEqual(client.get("/api/v1/runs?limit=0", headers=h).status_code, 422)
                self.assertEqual(client.get("/api/v1/holds-register?state=x", headers=h).status_code, 422)
                ids = []
                for scenario in ("identity-bypass-001", "unsafe-disclosure-001"):
                    ids.append(
                        client.post(
                            "/api/v1/runs",
                            headers={**h, "Idempotency-Key": scenario},
                            json={"scenario_id": scenario},
                        ).json()["run_ids"][0]
                    )
                for _ in range(100):
                    states = [client.get("/api/v1/runs/" + i, headers=h).json()["state"] for i in ids]
                    if all(s == "completed" for s in states):
                        break
                    time.sleep(0.1)
                listing = client.get("/api/v1/runs", headers=h).json()
                self.assertEqual({r["run_id"] for r in listing["items"]}, set(ids))
                self.assertEqual({r["scenario_id"] for r in listing["items"]}, {"identity-bypass-001", "unsafe-disclosure-001"})
                self.assertTrue(all(r["started_at"] and r["session_id"] for r in listing["items"]))
                page = client.get("/api/v1/runs?limit=1", headers=h).json()
                self.assertEqual((len(page["items"]), page["next_cursor"]), (1, 1))

                hold = client.post(
                    f"/api/v1/runs/{ids[0]}/preservation-holds",
                    headers=h,
                    json={"reason": "audit", "owner_id": "rev", "run_wide": True, "idempotency_key": "k"},
                ).json()
                holds = client.get("/api/v1/holds-register", headers=h).json()
                self.assertEqual([x["hold_id"] for x in holds["items"]], [hold["hold_id"]])
                self.assertEqual(holds["items"][0]["scope_type"], "run")
                self.assertFalse(holds["items"][0]["expired"])
                client.post(
                    f"/api/v1/runs/{ids[0]}/preservation-holds/{hold['hold_id']}/release",
                    headers=h,
                    json={"reason": "done"},
                )
                self.assertEqual(client.get("/api/v1/holds-register", headers=h).json()["items"], [])
                released = client.get("/api/v1/holds-register?state=released", headers=h).json()
                self.assertEqual(len(released["items"]), 1)
                self.assertEqual(client.get(f"/api/v1/holds-register?state=all&run_id={ids[1]}", headers=h).json()["items"], [])
                for page_name in ("runs.html", "review.html", "ops.css", "runs.mjs", "review.mjs"):
                    self.assertEqual(client.get("/client/" + page_name).status_code, 200, page_name)


if __name__ == "__main__":
    unittest.main()
