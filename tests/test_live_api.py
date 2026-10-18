"""Live scenarios launched through the API run lifecycle (finding 8); provider traffic is scripted."""

import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from test_judges import cfg, reference_judge
from test_phase4_scenarios import ScriptedAdapter, tone

from voice_harness import live_providers
from voice_harness.api import create_app
from voice_harness.asr import ScriptedAsr
from voice_harness.storage import Database

LIVE = "identity-impatient-en-gb-clean-001"
CARE = "identity-bereaved-en-gb-clean-001"  # declares CARE-01, which only a judge can decide


class FakeAdapter(ScriptedAdapter):
    receipt = {"model_id": "gpt-realtime-2.1-returned", "provider_session_id": "sess_abc"}

    async def open_session(self, config):
        self.config = config
        return self.receipt

    async def close(self, reason):
        self.closed = reason

    async def send_text(self, text, respond=True):
        self.texts = [*getattr(self, "texts", []), text]


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

    @staticmethod
    def no_live_env():
        """Drop only the live-provider settings; the database credentials must survive."""
        stripped = {k: v for k, v in os.environ.items() if k not in ("VOICE_HARNESS_LIVE_ASR", "OPENAI_API_KEY")}
        return mock.patch.dict(os.environ, stripped, clear=True)

    def launch(self, client, body, key="a"):
        return client.post("/api/v1/runs", headers={**self.headers, "Idempotency-Key": self.headers["Idempotency-Key"] + key}, json=body)

    def test_unconfigured_provider_is_refused_at_launch(self):
        with self.no_live_env(), TestClient(self.app) as client:
            response = self.launch(client, {"scenario_id": LIVE, "provider_config_id": "openai-realtime-live"})
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (409, "PROVIDER_NOT_CONFIGURED"))

    def test_provider_availability_is_listed_with_reason_codes_only(self):
        with self.no_live_env(), TestClient(self.app) as client:
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

    def completed_run(self, scenario, judges, asr=("Please verify your identity.",) * 2):
        adapter = FakeAdapter()
        patches = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=list(asr))),
            mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0: tone(200)),
            mock.patch.object(live_providers, "auto_judges", lambda environ=None: judges),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with TestClient(self.app) as client:
            created = self.launch(client, {"scenario_id": scenario, "provider_config_id": "openai-realtime-live"}, scenario)
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            return client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()

    def test_configured_auto_judge_decides_judge_criteria_and_is_bound_without_calibration(self):
        judge = reference_judge(cfg("auto-ref"))  # a different provider and family from the tested model
        assurance = self.completed_run(CARE, ([judge], []))
        self.assertEqual(assurance["criteria"]["CARE-01"]["source"], "judges")
        self.assertEqual(assurance["judge_calibration"][0]["status"], "no_calibration_report")
        self.assertFalse(assurance["judge_calibration"][0]["approved"])
        self.assertEqual(assurance["judge_calibration"][0]["blocking_enabled_criteria"], [])

    def assert_blank_asr_is_unresolved(self, blank):
        assurance = self.completed_run(LIVE, ([], []), asr=(blank,) * 4)
        fact = assurance["criteria"]["FACT-01"]
        self.assertEqual(fact["verdict"], "untestable", repr(blank))
        self.assertIn("EMPTY_TRANSCRIPT", fact["reason_codes"])
        self.assertNotEqual(assurance["overall_assurance"], "pass")

    def test_empty_independent_asr_is_never_a_factual_pass(self):
        self.assert_blank_asr_is_unresolved("")

    def test_whitespace_independent_asr_is_never_a_factual_pass(self):
        self.assert_blank_asr_is_unresolved("   ")

    def test_a_run_without_independent_asr_is_never_a_factual_pass(self):
        adapter = FakeAdapter()
        patches = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: None),  # strict speech unavailable
            mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0: tone(200)),
            mock.patch.object(live_providers, "auto_judges", lambda environ=None: ([], [])),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with TestClient(self.app) as client:
            created = self.launch(client, {"scenario_id": LIVE, "provider_config_id": "openai-realtime-live"}, "noasr")
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            assurance = client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()
        self.assertEqual(assurance["criteria"]["FACT-01"]["verdict"], "untestable")
        self.assertIn("INDEPENDENT_ASR_UNAVAILABLE", assurance["criteria"]["FACT-01"]["reason_codes"])
        self.assertNotEqual(assurance["overall_assurance"], "pass")  # the scripted adapter also proposes the tool early

    def injected_run(self, summary=None, assess_error=None):
        async def run_live(engine, run_id, provider_config_id, scenario_id, seed=0):
            return summary

        patches = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "run_live", run_live),
            mock.patch.object(live_providers, "auto_judges", lambda environ=None: ([], [])),
        ]
        if assess_error:
            patches.append(mock.patch("voice_harness.api.judge_service.assess_run", side_effect=assess_error))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with TestClient(self.app) as client:
            created = self.launch(client, {"scenario_id": LIVE, "provider_config_id": "nova-sonic-live"}, "inj")
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            events = client.get(f"/api/v1/runs/{run}/events?limit=1000", headers=self.headers).json()["items"]
            return client.get(f"/api/v1/runs/{run}", headers=self.headers).json()["state"], events

    def test_a_timed_out_turn_is_a_failed_run_with_a_typed_reason(self):
        state, events = self.injected_run({"turns": [{"turn": 0, "strict_speech_approval": "turn_timeout"}], "adapter_errors": []})
        failed = [e["content"] for e in events if e["type"] == "run.failed"]
        self.assertEqual((state, failed), ("failed", [{"reason": "TURN_TIMEOUT"}]))
        self.assertNotIn("run.completed", [e["type"] for e in events])

    def test_an_assessment_failure_never_follows_run_completed(self):
        state, events = self.injected_run(
            {"turns": [{"turn": 0, "strict_speech_approval": "approved"}], "adapter_errors": []},
            assess_error=RuntimeError("judge store down"),
        )
        types = [e["type"] for e in events]
        self.assertEqual(state, "failed")
        self.assertIn("run.evaluating", types)
        self.assertNotIn("run.completed", types)  # the terminal state is written once, after the assessment

    def exported(self, scenario, provider="openai-realtime-live"):
        adapter = FakeAdapter()
        patches = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=["Please verify your identity."] * 3)),
            mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0: tone(200)),
            mock.patch.object(live_providers, "auto_judges", lambda environ=None: ([], [])),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with TestClient(self.app) as client:
            created = self.launch(client, {"scenario_id": scenario, "provider_config_id": provider}, scenario + provider)
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            bundle = client.get(f"/api/v1/runs/{run}/evidence", headers=self.headers).json()
            events = client.get(f"/api/v1/runs/{run}/events?limit=1000", headers=self.headers).json()["items"]
        return bundle, events

    def test_a_live_export_describes_the_live_provider_session_not_a_mock_fixture(self):
        bundle, events = self.exported(LIVE)
        manifest = bundle["manifest"]
        provider = manifest["provider"]
        self.assertEqual(provider["provider_config_id"], "openai-realtime-live")
        self.assertEqual(provider["adapter"], "openai_realtime")  # from the session binding, not a mock label
        self.assertEqual((provider["requested_model_id"], provider["returned_model_id"]),
                         ("gpt-realtime-2.1", "gpt-realtime-2.1-returned"))
        self.assertEqual(provider["provider_session_id"], "sess_abc")
        self.assertEqual(provider["version_resolution"], "provider_returned")
        self.assertNotIn(provider["capability_report_id"], ("mock-fixture-v1",))
        self.assertNotEqual(provider["capability_status"], "mock_only")
        self.assertTrue(provider["session_binding_recorded"])
        text = " ".join(manifest["known_limitations"])
        self.assertNotIn("Scripted fixture provider", text)
        self.assertNotIn("PCM silence", text)
        self.assertNotIn("NO_PROVIDER_BILLING_IN_MOCK", json.dumps(manifest["pricing"]))
        probing = next(e["content"] for e in events if e["type"] == "run.probing")
        self.assertNotEqual(probing.get("capability_status"), "mock_only")
        self.assertIn("capability_report_id", probing)

    def test_a_live_export_verifies_offline(self):
        from voice_harness.evidence import verify_bundle

        bundle, _ = self.exported(LIVE)
        self.assertTrue(verify_bundle(bundle), "the live bundle must verify offline (ledger chain, judging, cost)")

    def test_a_mock_export_keeps_its_fixture_labels(self):
        with TestClient(self.app) as client:
            created = self.launch(client, {}, "mockexport")
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            manifest = client.get(f"/api/v1/runs/{run}/evidence", headers=self.headers).json()["manifest"]
        self.assertEqual(manifest["provider"]["adapter"], "mock")
        self.assertEqual(manifest["provider"]["capability_status"], "mock_only")
        self.assertIn("Scripted fixture provider: no live ASR, judge or provider behaviour", manifest["known_limitations"])

    def test_the_confirmed_cancellation_scenario_is_labelled_out_of_band_and_excluded_from_voice_consent(self):
        with mock.patch("voice_harness.engine.Engine.present_cancellation", lambda self, run, payment: ("req", "digest")), \
                mock.patch("voice_harness.engine.Engine.confirm", lambda self, *a, **k: {"confirmation_id": "conf_1"}):
            bundle, events = self.exported("commit-cancel-confirmed-001")
        binding = next(e["content"] for e in events if e["type"] == "run.live_binding")
        self.assertEqual(binding["harness_mechanics"], ["out_of_band_confirmation"])
        self.assertEqual(bundle["manifest"]["provider"]["harness_mechanics"], ["out_of_band_confirmation"])
        self.assertTrue(any("out of band" in line for line in bundle["manifest"]["known_limitations"]))
        rubric = next(e["content"] for e in events if e["type"] == "rubric.evaluated")
        self.assertNotIn("CONFIRM-01", rubric["applicable_criteria"])

    def test_the_unconfirmed_cancellation_scenario_exercises_voice_consent_and_carries_no_label(self):
        bundle2, events2 = self.exported("commit-cancel-unconfirmed-001")
        self.assertIn("CONFIRM-01", next(e["content"] for e in events2 if e["type"] == "rubric.evaluated")["applicable_criteria"])
        self.assertEqual(next(e["content"] for e in events2 if e["type"] == "run.live_binding")["harness_mechanics"], [])
        self.assertFalse(any("out of band" in line for line in bundle2["manifest"]["known_limitations"]))

    def test_an_unavailable_auto_judge_leaves_judge_criteria_untestable(self):
        assurance = self.completed_run(CARE, ([], [{"backend": "openai", "reason": "JUDGE_UNAVAILABLE"}]))
        self.assertEqual(assurance["criteria"]["CARE-01"]["verdict"], "untestable")

    def test_live_providers_lists_configured_auto_judges_and_failures(self):
        with TestClient(self.app) as client, mock.patch.object(
            live_providers, "auto_judges", lambda environ=None: ([reference_judge()], [{"backend": "openai", "reason": "JUDGE_UNAVAILABLE"}])
        ):
            payload = client.get("/api/v1/live-providers", headers=self.headers).json()
        self.assertEqual([j["available"] for j in payload["auto_judges"]], [True, False])
        self.assertEqual(payload["auto_judges"][1]["reason"], "JUDGE_UNAVAILABLE")

    def test_auto_judges_reads_the_environment_and_reports_unstartable_backends(self):
        backends, unavailable = live_providers.auto_judges({"HARNESS_AUTO_JUDGES": " openai ,nope"})
        self.assertEqual(backends, [])
        self.assertEqual([u["backend"] for u in unavailable], ["openai", "nope"])
        self.assertEqual(live_providers.auto_judges({}), ([], []))

    def test_cancel_stops_an_in_flight_live_run_and_closes_the_provider_session(self):
        adapter = HangingAdapter()
        adapter.opened = threading.Event()
        patches = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=["x"])),
            mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0: tone(200)),
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
            mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0: tone(200)),
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
        failure = [e["content"] for e in events if e["type"] == "run.failed"]
        self.assertEqual(view["state"], "completed", failure)
        self.assertEqual(adapter.closed, "done")
        for expected in ("run.probing", "run.running", "run.live_binding", "input.caller_audio", "run.evaluating", "run.completed"):
            self.assertIn(expected, types)
        self.assertEqual(self.database.get(run)["limits"]["pending_audio_seconds"], 120.0)
        self.assertIn("judge.adjudicated", types)
        self.assertLess(types.index("judge.adjudicated"), types.index("run.completed"))
        completed = next(e for e in events if e["type"] == "run.completed")
        self.assertEqual(completed["content"]["assurance"]["overall_assurance"], self.database.get(run)["assurance"]["overall_assurance"])
        assurance = self.database.get(run)["assurance"]
        self.assertEqual(assurance["overall_assurance"], effective["overall_assurance"])
        self.assertEqual(effective["reason_codes"], [])  # deterministic checks only; no judge was configured


if __name__ == "__main__":
    unittest.main()
