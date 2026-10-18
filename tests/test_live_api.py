"""Live scenarios launched through the API run lifecycle (finding 8); provider traffic is scripted."""

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from test_phase4_scenarios import ScriptedAdapter

from voice_harness import live_providers
from voice_harness.api import create_app
from voice_harness.asr import ScriptedAsr
from voice_harness.storage import Database

LIVE = "identity-impatient-en-gb-clean-001"


class FakeAdapter(ScriptedAdapter):
    async def open_session(self, config):
        self.config = config

    async def close(self, reason):
        self.closed = reason


class HangingAdapter(FakeAdapter):
    """A provider session that never answers, so the run is in flight until it is cancelled."""

    opened = None

    async def receive(self):
        self.opened.set()
        await asyncio.Event().wait()
        yield  # pragma: no cover


class LiveApi(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "t.sqlite")))
        self.app = create_app(self.database, root, "operator")
        self.headers = {"Authorization": "Bearer operator", "Idempotency-Key": "live-" + root.name}

    def launch(self, client, body, key="a"):
        return client.post("/api/v1/runs", headers={**self.headers, "Idempotency-Key": self.headers["Idempotency-Key"] + key}, json=body)

    def test_unconfigured_provider_is_refused_at_launch(self):
        with mock.patch.dict(os.environ, {}, clear=True), TestClient(self.app) as client:
            response = self.launch(client, {"scenario_id": LIVE, "provider_config_id": "openai-realtime-live"})
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "PROVIDER_NOT_CONFIGURED"))

    def test_provider_availability_is_listed_with_reason_codes_only(self):
        with mock.patch.dict(os.environ, {}, clear=True), TestClient(self.app) as client:
            items = client.get("/api/v1/live-providers", headers=self.headers).json()["items"]
            scenario = next(i for i in client.get("/api/v1/scenarios", headers=self.headers).json()["items"] if i["scenario_id"] == LIVE)
        self.assertEqual({i["provider_config_id"] for i in items}, set(live_providers.LIVE_PROVIDERS))
        self.assertTrue(all(not i["available"] and i["reason"] == "NO_ASR_BACKEND_CONFIGURED" for i in items))
        self.assertTrue(scenario["runnable"] and scenario["execution"] == "live_adapter")

    def test_scenario_and_provider_must_pair(self):
        with TestClient(self.app) as client:
            mock_with_live = self.launch(client, {"scenario_id": LIVE}, "b")
            live_with_builtin = self.launch(client, {"provider_config_id": "nova-sonic-live"}, "c")
            unknown = self.launch(client, {"scenario_id": "nope-001"}, "d")
        self.assertEqual(mock_with_live.json()["error"]["code"], "SCENARIO_PROVIDER_MISMATCH")
        self.assertEqual(live_with_builtin.json()["error"]["code"], "SCENARIO_PROVIDER_MISMATCH")
        self.assertEqual((unknown.status_code, unknown.json()["error"]["code"]), (422, "UNKNOWN_SCENARIO"))

    def test_cancel_stops_an_in_flight_live_run_and_closes_the_provider_session(self):
        adapter = HangingAdapter()
        adapter.opened = threading.Event()
        patches = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=["x"])),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with TestClient(self.app) as client:
            created = self.launch(client, {"scenario_id": LIVE, "provider_config_id": "nova-sonic-live"}, "f")
            run = created.json()["run_ids"][0]
            self.assertTrue(adapter.opened.wait(30))
            self.assertEqual(client.get(f"/api/v1/runs/{run}", headers=self.headers).json()["state"], "running")
            cancel = client.post(f"/api/v1/runs/{run}/cancel", headers=self.headers, json={"reason": "operator"})
            self.assertEqual((cancel.status_code, cancel.json()["state"]), (202, "cancelled"))
            self.app.state.jobs[run].result(timeout=30)  # the worker unwinds instead of waiting for the provider
            events = client.get(f"/api/v1/runs/{run}/events?limit=1000", headers=self.headers).json()["items"]
            view = client.get(f"/api/v1/runs/{run}", headers=self.headers).json()
        types = [e["type"] for e in events]
        self.assertEqual(adapter.closed, "cancelled")
        self.assertEqual(view["state"], "cancelled")
        self.assertIn("run.cancelled", types)
        self.assertNotIn("run.completed", types)
        self.assertNotIn("run.failed", types)
        self.assertNotIn("judge.adjudicated", types)  # a cancelled run is not assessed
        self.assertIsNone(self.database.get(run)["assurance"])

    def test_live_run_goes_through_the_lifecycle_with_live_budgets(self):
        adapter = FakeAdapter()
        patches = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=["Please verify your identity."] * 2)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with TestClient(self.app) as client:
            created = self.launch(client, {"scenario_id": LIVE, "provider_config_id": "openai-realtime-live"}, "e")
            self.assertEqual(created.status_code, 202)
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            view = client.get(f"/api/v1/runs/{run}", headers=self.headers).json()
            events = client.get(f"/api/v1/runs/{run}/events?limit=1000", headers=self.headers).json()["items"]
            effective = client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()
        types = [e["type"] for e in events]
        self.assertEqual(view["state"], "completed")
        self.assertEqual(adapter.closed, "done")
        for expected in ("run.probing", "run.running", "run.live_binding", "input.caller_audio", "run.completed"):
            self.assertIn(expected, types)
        self.assertEqual(self.database.get(run)["limits"]["pending_audio_seconds"], 120.0)
        self.assertIn("judge.adjudicated", types)
        assurance = self.database.get(run)["assurance"]
        self.assertEqual(assurance["overall_assurance"], effective["overall_assurance"])
        self.assertEqual(effective["reason_codes"], [])  # deterministic checks only; no judge was configured


if __name__ == "__main__":
    unittest.main()
