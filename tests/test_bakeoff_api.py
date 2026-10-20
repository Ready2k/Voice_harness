"""Bake-offs through the API: scheduling live runs, the ledger-derived report, what-if weights and the export."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from test_live_api import FakeAdapter
from test_phase4_scenarios import tone

from voice_harness import live_providers
from voice_harness.api import create_app
from voice_harness.asr import ScriptedAsr
from voice_harness.storage import Database

SCENARIOS = ["identity-impatient-en-gb-clean-001", "identity-social-en-gb-clean-001"]
PROVIDERS = ["openai-realtime-live", "nova-sonic-live"]


class BakeoffApi(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "t.sqlite")))
        self.app = create_app(self.database, root, "operator")
        self.headers = {"Authorization": "Bearer operator"}
        for patch in (
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None, manual=False, **kw: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id, prompt_profile="standardised-v2": (FakeAdapter(), {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=["Please verify your identity."] * 4)),
            mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0, **kw: tone(200)),
            mock.patch.object(live_providers, "auto_judges", lambda environ=None: ([], [])),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def settle(self, client, bakeoff_id, timeout=120):
        """Drive the scheduler until the bake-off completes (the background loop does this every few seconds)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.state.bakeoff_tick()
            body = client.get(f"/api/v1/bakeoffs/{bakeoff_id}", headers=self.headers).json()
            if body["bakeoff"]["state"] != "running":
                return body
            time.sleep(0.2)
        raise AssertionError("bake-off did not finish")

    def test_a_launched_bakeoff_runs_every_cell_and_reports_from_the_ledger(self):
        with TestClient(self.app) as client:
            created = client.post("/api/v1/bakeoffs", headers=self.headers,
                                  json={"name": "Two providers", "provider_config_ids": PROVIDERS, "scenario_ids": SCENARIOS})
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["cells"], 4)
            self.assertGreater(created.json()["runs_in_flight"], 0)  # the first runs start on creation
            body = self.settle(client, created.json()["bakeoff_id"])
            listing = client.get("/api/v1/bakeoffs", headers=self.headers).json()["items"]
        self.assertEqual(body["bakeoff"]["state"], "completed")
        self.assertEqual(body["bakeoff"]["cells_done"], 4)
        self.assertEqual(listing[0]["bakeoff_id"], body["bakeoff"]["bakeoff_id"])
        report = body["data"]["report"]
        self.assertEqual(report["progress"], {"cells": 4, "observed": 4, "pending": 0})
        self.assertTrue(report["verdict"]["final"])
        self.assertEqual({g["provider"] for g in report["gates"]}, set(PROVIDERS))
        self.assertEqual(len(report["pairs"]), 1)
        observations = body["data"]["observations"]
        self.assertEqual(len(observations), 4)
        self.assertTrue(all(o["run_id"].startswith("run_") for o in observations))
        for cell in body["cells"]:  # each cell names the run it was settled from
            self.assertTrue(cell["done"])
            self.assertEqual(self.database.get(cell["attempts"][-1]["run_id"])["config"]["scenario_id"], cell["scenario_id"])

    def test_what_if_weights_recompute_without_storing_and_the_export_is_self_contained(self):
        with TestClient(self.app) as client:
            bakeoff_id = client.post("/api/v1/bakeoffs", headers=self.headers, json={
                "name": "Export", "provider_config_ids": PROVIDERS, "scenario_ids": SCENARIOS[:1]}).json()["bakeoff_id"]
            self.settle(client, bakeoff_id)
            what_if = client.post(f"/api/v1/bakeoffs/{bakeoff_id}/report", headers=self.headers,
                                  json={"task_success": 1, "factual": 0, "safe_speech": 0, "reliability": 0, "responsiveness": 0, "robustness": 0})
            stored = client.get(f"/api/v1/bakeoffs/{bakeoff_id}", headers=self.headers).json()
            export = client.get(f"/api/v1/bakeoffs/{bakeoff_id}/report.html", headers=self.headers)
        self.assertEqual(what_if.json()["data"]["report"]["weights"]["task_success"], 1.0)
        self.assertAlmostEqual(stored["weights"]["task_success"], 0.30)  # the stored weights are unchanged
        self.assertEqual(export.status_code, 200)
        self.assertIn("attachment", export.headers["content-disposition"])
        self.assertIn("export function renderReport", export.text)  # the shared renderer is inlined
        self.assertIn('id="report-data"', export.text)
        self.assertNotRegex(export.text, r'<(script|link)[^>]+(src|href)="https?:')

    def test_existing_runs_can_be_wrapped_as_a_bakeoff(self):
        with TestClient(self.app) as client:
            runs = []
            for provider in PROVIDERS:
                launched = client.post("/api/v1/runs", headers={**self.headers, "Idempotency-Key": "imp-" + provider},
                                       json={"scenario_id": SCENARIOS[0], "provider_config_id": provider})
                runs.append(launched.json()["run_ids"][0])
            for run_id in runs:
                # Completed workers are removed from jobs by their callback.
                job = self.app.state.jobs.get(run_id)
                if job is not None:
                    job.result(timeout=60)
                self.assertIn(self.database.get(run_id)["state"], ("completed", "failed"))
            created = client.post("/api/v1/bakeoffs", headers=self.headers,
                                  json={"name": "Imported", "runs": [{"run_id": r} for r in runs]})
            body = client.get(f"/api/v1/bakeoffs/{created.json()['bakeoff_id']}", headers=self.headers).json()
            unknown = client.post("/api/v1/bakeoffs", headers=self.headers, json={"name": "x", "runs": [{"run_id": "run_nope"}, {"run_id": runs[0]}]})
        self.assertEqual(body["bakeoff"]["mode"], "imported")
        self.assertEqual(body["bakeoff"]["state"], "completed")  # settled on creation: every run had finished
        self.assertEqual(body["data"]["report"]["progress"]["observed"], 2)
        self.assertEqual(unknown.status_code, 404)

    def test_unconfigured_providers_and_bad_plans_are_refused(self):
        with TestClient(self.app) as client, mock.patch.object(
                live_providers, "availability", lambda provider_id, environ=None, manual=False, **kw: (provider_id != "gemini-live", "NO_ADC")):
            missing = client.post("/api/v1/bakeoffs", headers=self.headers,
                                  json={"name": "x", "provider_config_ids": ["openai-realtime-live", "gemini-live"]})
            single = client.post("/api/v1/bakeoffs", headers=self.headers, json={"name": "x", "provider_config_ids": ["openai-realtime-live"]})
            absent = client.get("/api/v1/bakeoffs/bko_missing", headers=self.headers)
        self.assertEqual(missing.json()["error"]["code"], "PROVIDER_NOT_CONFIGURED:gemini-live")
        self.assertEqual(single.status_code, 422)
        self.assertEqual(absent.status_code, 404)

    def test_cancel_stops_launching_and_cancels_runs_in_flight(self):
        with TestClient(self.app) as client:
            bakeoff_id = client.post("/api/v1/bakeoffs", headers=self.headers, json={
                "name": "Cancel", "provider_config_ids": PROVIDERS, "repetitions": 2}).json()["bakeoff_id"]
            cancelled = client.post(f"/api/v1/bakeoffs/{bakeoff_id}/cancel", headers=self.headers)
            again = client.post(f"/api/v1/bakeoffs/{bakeoff_id}/cancel", headers=self.headers)
            launched_before = cancelled.json()["runs_in_flight"]
            self.app.state.bakeoff_tick()  # a cancelled bake-off launches nothing more
            after = client.get(f"/api/v1/bakeoffs/{bakeoff_id}", headers=self.headers).json()
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertEqual(again.json()["error"]["code"], "BAKEOFF_NOT_RUNNING")
        self.assertEqual(sum(bool(c["attempts"]) for c in after["cells"]), launched_before)


if __name__ == "__main__":
    unittest.main()
