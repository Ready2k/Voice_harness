"""Live scenarios launched through the API run lifecycle (finding 8); provider traffic is scripted."""

import asyncio
import json
import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from test_judges import cfg, reference_judge
from test_phase4_scenarios import ScriptedAdapter, tone

from voice_harness import live_providers
from voice_harness.adapters import AudioOut, ToolCall, TranscriptDelta, TurnEnd
from voice_harness.api import create_app
from voice_harness.asr import ScriptedAsr
from voice_harness.storage import Database

LIVE = "identity-impatient-en-gb-clean-001"
INTERRUPT = "identity-fault-caller-interrupts-001"
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


class XaiFakeAdapter(FakeAdapter):
    receipt = {"model_id": "grok-voice-3.0", "provider_session_id": "sess_xai"}


class InterruptedThenBalance(FakeAdapter):
    """First response is cut off by the caller; the second verifies, reads the balance, then speaks ``claim``."""

    def __init__(self, claim):
        super().__init__()
        self.claim, self.round, self.interrupted = claim, 0, []

    async def interrupt(self, item_id, audio_end_ms):
        self.interrupted.append(audio_end_ms)
        return {"history_truncation": "acknowledged"}

    async def receive(self):
        self.round += 1
        if self.round == 1:
            yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t1", ts_ms=1)
            yield TurnEnd(turn_id="t1", ts_ms=2)
            return
        yield ToolCall(call_id="call_v", name="verify_identity",
                       arguments={"challenge_id": "challenge_001", "answer": "blue"}, ts_ms=1)
        yield ToolCall(call_id="call_s", name="get_account_summary", arguments={"account_id": "acct_1001"}, ts_ms=2)
        yield TurnEnd(turn_id="t2", ts_ms=3)  # the tool-calling response
        yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t3", ts_ms=4)
        yield TranscriptDelta(role="assistant", text=self.claim, ts_ms=5)
        yield TurnEnd(turn_id="t3", ts_ms=6)


class InterruptedThenTransaction(InterruptedThenBalance):
    """As above, but the second response also lists recent transactions before it speaks ``claim``."""

    async def receive(self):
        self.round += 1
        if self.round == 1:
            async for item in InterruptedThenBalance.receive(self):  # pragma: no cover -- delegates round 1 behaviour
                yield item
            return
        yield ToolCall(call_id="call_v", name="verify_identity",
                       arguments={"challenge_id": "challenge_001", "answer": "blue"}, ts_ms=1)
        yield ToolCall(call_id="call_s", name="get_account_summary", arguments={"account_id": "acct_1001"}, ts_ms=2)
        yield ToolCall(call_id="call_t", name="list_recent_transactions",
                       arguments={"account_id": "acct_1001", "limit": 5}, ts_ms=3)
        yield TurnEnd(turn_id="t2", ts_ms=4)
        yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t3", ts_ms=5)
        yield TranscriptDelta(role="assistant", text=self.claim, ts_ms=6)
        yield TurnEnd(turn_id="t3", ts_ms=7)


class VerifyThenCancel(FakeAdapter):
    """Round 1 verifies the caller; round 2 cancels with whatever confirmation_id the orchestrator handed over."""

    def __init__(self):
        super().__init__()
        self.round = 0

    async def receive(self):
        self.round += 1
        if self.round == 1:
            yield ToolCall(call_id="call_v", name="verify_identity",
                           arguments={"challenge_id": "challenge_001", "answer": "blue"}, ts_ms=1)
            yield TurnEnd(turn_id="t1", ts_ms=2)
            yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t1b", ts_ms=3)
            yield TranscriptDelta(role="assistant", text="You are verified.", ts_ms=4)
            yield TurnEnd(turn_id="t1b", ts_ms=5)
            return
        given = [t.split("confirmation_id=")[1].split()[0] for t in getattr(self, "texts", []) if "confirmation_id=" in t]
        yield ToolCall(call_id="call_c", name="cancel_payment",
                       arguments={"payment_id": "payment_001", "confirmation_id": given[0] if given else "conf_invented"}, ts_ms=1)
        yield TurnEnd(turn_id="t2", ts_ms=2)
        yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t2b", ts_ms=3)
        yield TranscriptDelta(role="assistant", text="Done.", ts_ms=4)
        yield TurnEnd(turn_id="t2b", ts_ms=5)


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

    def completed_run(self, scenario, judges, asr=("Please verify your identity.",) * 2, adapter=None, events=False,
                      provider="openai-realtime-live"):
        adapter = adapter or FakeAdapter()
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
            created = self.launch(client, {"scenario_id": scenario, "provider_config_id": provider}, scenario)
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            assurance = client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()
            if not events:
                return assurance
            return assurance, client.get(f"/api/v1/runs/{run}/events?limit=1000", headers=self.headers).json()["items"]

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

    def test_interrupted_first_turn_then_a_wrong_second_turn_balance_is_a_factual_fail(self):
        claim = "Your balance is fifty pounds."
        adapter = InterruptedThenBalance(claim)
        assurance, events = self.completed_run(INTERRUPT, ([], []), asr=(claim,) * 4, adapter=adapter, events=True)
        self.assertEqual(adapter.interrupted, [0])
        types = [e["type"] for e in events]
        self.assertIn("tool.executing", types)
        calls = [e["content"].get("call_id") for e in events if e["type"] == "tool.completed"]
        self.assertEqual(calls, ["call_v", "call_s"])  # both trusted tools succeeded through the gateway
        independent = [e["content"] for e in events if e["type"] == "assessment.independent_asr"]
        self.assertEqual([(i["disposition"], i["reason_codes"]) for i in independent], [("unsafe", ["UNEXPECTED_AMOUNT"])])
        self.assertEqual(independent[0]["claims"]["amounts_minor"], [5000])
        self.assertEqual(independent[0]["expected"]["allowed_facts"][0]["amount_minor"], 123456)
        self.assertEqual([e for e in events if e["type"] == "playback.released"], [])
        fact = assurance["criteria"]["FACT-01"]
        self.assertEqual(fact["verdict"], "fail")
        self.assertIn("BALANCE_MISMATCH", fact["reason_codes"])
        self.assertNotEqual(assurance["overall_assurance"], "pass")

    def test_interrupted_first_turn_then_a_correct_second_turn_is_untestable_not_pass(self):
        claim = "Your balance is one thousand two hundred and thirty four pounds fifty six."
        assurance = self.completed_run(INTERRUPT, ([], []), asr=(), adapter=InterruptedThenBalance(claim))
        fact = assurance["criteria"]["FACT-01"]
        self.assertEqual(fact["verdict"], "untestable")
        self.assertIn("INDEPENDENT_ASR_UNAVAILABLE", fact["reason_codes"])
        self.assertNotEqual(assurance["overall_assurance"], "pass")

    def booking_date_assurance(self, claim):
        adapter = InterruptedThenTransaction(claim)
        return self.completed_run(INTERRUPT, ([], []), asr=(claim,) * 4, adapter=adapter, events=True)

    def test_a_wrong_transaction_booking_date_fails_even_when_it_matches_the_account_summary_date(self):
        # The summary's as_of is the current clock; the transaction was booked on 16 October 2026.
        today = datetime.now(UTC)
        claim = f"Transaction zero zero one was booked on {today.day} {today:%B %Y}."
        assurance, events = self.booking_date_assurance(claim)
        done = [e["content"].get("call_id") for e in events if e["type"] == "tool.completed"]
        self.assertEqual(done, ["call_v", "call_s", "call_t"])  # real local MCP returned the transaction list
        fact = assurance["criteria"]["FACT-01"]
        self.assertEqual(fact["verdict"], "fail")
        self.assertIn("DATE_MISMATCH", fact["reason_codes"])
        self.assertNotEqual(assurance["overall_assurance"], "pass")

    def test_the_transactions_own_booking_date_is_not_a_date_failure(self):
        assurance, _ = self.booking_date_assurance("Transaction zero zero one was booked on 16 October 2026.")
        self.assertNotIn("DATE_MISMATCH", assurance["criteria"]["FACT-01"]["reason_codes"])

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

    def exported(self, scenario, provider="openai-realtime-live", adapter=None):
        adapter = adapter or FakeAdapter()
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

    def test_xai_availability_checks_the_key_without_revealing_it(self):
        secret = "xai-SECRET-VALUE-123"
        live = {"VOICE_HARNESS_LIVE_ASR": "openai", "OPENAI_API_KEY": "k"}
        with mock.patch("voice_harness.caller_voice.available", lambda environ=None: (True, None)):
            self.assertEqual(live_providers.availability("xai-voice-live", {**live, "xAI_API_KEY": secret}), (True, None))
            ok, reason = live_providers.availability("xai-voice-live", live)
            self.assertEqual((ok, reason), (False, "NO_XAI_API_KEY"))
            self.assertEqual(live_providers.availability("xai-voice-live", {"xAI_API_KEY": secret})[1],
                             "NO_ASR_BACKEND_CONFIGURED")  # the same ASR gate as the other providers applies first
            self.assertNotIn(secret, json.dumps(live_providers.availability("xai-voice-live", {**live, "xAI_API_KEY": secret})))
        with mock.patch("voice_harness.caller_voice.available", lambda environ=None: (False, "NO_CALLER_VOICE_SYNTH")):
            self.assertEqual(live_providers.availability("xai-voice-live", {**live, "xAI_API_KEY": secret})[1],
                             "NO_CALLER_VOICE_SYNTH")

    def test_xai_provider_is_registered_with_the_pinned_model_and_family(self):
        self.assertEqual(live_providers.LIVE_PROVIDERS["xai-voice-live"], "xai_grok_voice")
        self.assertEqual(live_providers.TESTED_MODELS["xai-voice-live"],
                         {"provider": "xai", "model_id": "grok-voice-3.0", "family": "xai_grok_voice"})  # never the alias
        self.assertEqual(live_providers.FAMILIES["xai-voice-live"], "xai_grok_voice")

    def test_an_xai_live_run_launches_through_the_api_with_xai_session_binding_and_judge_independence(self):
        same = reference_judge(cfg("xai-self", provider="xai", model="grok-voice-3.0", family="xai_grok_voice"))
        other = reference_judge(cfg("other"))
        _, events = self.completed_run(CARE, ([same, other], []), provider="xai-voice-live", adapter=XaiFakeAdapter(), events=True)
        independence = next(e["content"] for e in events if e["type"] == "judge.independence_recorded")
        self.assertEqual(independence["tested"], {"provider": "xai", "model_id": "grok-voice-3.0", "family": "xai_grok_voice"})
        verdicts = {j["judge_id"]: j for j in independence["judges"]}
        self.assertFalse(verdicts["xai-self"]["eligible"])
        self.assertIn("JUDGE_IS_TESTED_MODEL", verdicts["xai-self"]["reason_codes"])
        self.assertTrue(verdicts["other"]["eligible"])
        probing = next(e["content"] for e in events if e["type"] == "run.probing")
        self.assertEqual(probing["capability_report_id"], "cap_xai_grok_voice_3_0_20261018")

    def test_an_xai_live_export_describes_the_xai_session_and_verifies_offline(self):
        from voice_harness.evidence import verify_bundle

        bundle, _ = self.exported(LIVE, provider="xai-voice-live", adapter=XaiFakeAdapter())
        provider = bundle["manifest"]["provider"]
        self.assertEqual((provider["provider_config_id"], provider["adapter"]), ("xai-voice-live", "xai_grok_voice"))
        self.assertEqual((provider["requested_model_id"], provider["returned_model_id"]), ("grok-voice-3.0", "grok-voice-3.0"))
        self.assertEqual(provider["provider_session_id"], "sess_xai")
        self.assertEqual(provider["version_resolution"], "provider_returned")
        self.assertNotEqual(provider["capability_status"], "mock_only")
        self.assertTrue(verify_bundle(bundle))
        self.assertNotIn("xAI_API_KEY", json.dumps(bundle))

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
                mock.patch.object(live_providers, "observe", lambda *a: {"observed_choice": "affirm", "reason_codes": [], "pcm_sha256": "0" * 64}), \
                mock.patch("voice_harness.engine.Engine.confirm", lambda self, *a, **k: {"accepted": True, "confirmation_id": "conf_1"}):
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

    def live_events(self, scenario, backend, patches=(), provider="openai-realtime-live"):
        self.launches = getattr(self, "launches", 0) + 1
        adapter = FakeAdapter()
        active = [
            mock.patch.object(live_providers, "availability", lambda provider_id, environ=None: (True, None)),
            mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []})),
            mock.patch.object(live_providers, "select_backend", lambda: backend),
            mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0: tone(200)),
            mock.patch.object(live_providers, "auto_judges", lambda environ=None: ([], [])),
            *patches,
        ]
        for patch in active:
            patch.start()
            self.addCleanup(patch.stop)
        with TestClient(self.app) as client:
            created = self.launch(client, {"scenario_id": scenario, "provider_config_id": provider}, f"{scenario}lv{self.launches}")
            run = created.json()["run_ids"][0]
            self.app.state.jobs[run].result(timeout=60)
            events = client.get(f"/api/v1/runs/{run}/events?limit=1000", headers=self.headers).json()["items"]
            assurance = client.get(f"/api/v1/runs/{run}/assurance", headers=self.headers).json()
        return adapter, events, assurance

    def test_a_declared_fault_that_never_fired_is_recorded_and_never_a_pass(self):
        # The scripted provider's get_account_summary call is denied (unverified), so no delayed result is delivered.
        _, events, assurance = self.live_events("identity-fault-tool-latency-001", ScriptedAsr(queue=["Please verify."] * 8))
        recorded = [e["content"] for e in events if e["type"] == "fault.disposition"]
        self.assertEqual([(r["fault_kind"], r["disposition"]) for r in recorded], [("tool_latency", "not_exercised")])
        self.assertEqual(assurance["fault_dispositions"], recorded)
        self.assertIn("FAULT_NOT_EXERCISED", assurance["reason_codes"])
        self.assertNotEqual(assurance["overall_assurance"], "pass")

    def test_a_scenario_without_faults_records_no_disposition(self):
        _, events, assurance = self.live_events(LIVE, ScriptedAsr(queue=["Please verify."] * 8))
        self.assertFalse([e for e in events if e["type"] == "fault.disposition"])
        self.assertEqual(assurance["fault_dispositions"], [])

    def cancel_patches(self, calls):
        def confirm(engine, run, context, request, observation, digest, choice, **kw):
            calls.append(choice)
            return {"accepted": choice == "affirm", **({"confirmation_id": "conf_1"} if choice == "affirm" else {"reason_code": "AMBIGUOUS_RESPONSE"})}

        return [mock.patch("voice_harness.engine.Engine.present_cancellation", lambda self, run, payment: ("req", "digest")),
                mock.patch("voice_harness.engine.Engine.confirm", confirm)]

    def assert_confirmation_withheld(self, transcript):
        calls = []
        adapter, events, _ = self.live_events("commit-cancel-confirmed-001", ScriptedAsr(queue=[transcript] * 8),
                                              self.cancel_patches(calls))
        observed = [e["content"] for e in events if e["type"] == "confirmation.voice_observation"]
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0]["authorised"])
        self.assertNotEqual(calls, ["affirm"])
        self.assertEqual(len(calls), 1)  # the unauthorised outcome went through the engine, which records the rejection
        self.assertFalse([t for t in getattr(adapter, "texts", []) if "confirmation_id" in t])

    def test_out_of_band_confirmation_is_withheld_when_the_caller_audio_is_not_a_confirmation(self):
        self.assert_confirmation_withheld("Please verify my identity.")

    def test_out_of_band_confirmation_is_withheld_when_the_caller_denies(self):
        self.assert_confirmation_withheld("No, do not cancel that.")

    def real_engine_cancellation(self, transcript):
        """Run the confirmed scenario through the real confirmation engine and cancel_payment (nothing mocked)."""
        adapter = VerifyThenCancel()
        patches = [mock.patch.object(live_providers, "_adapter", lambda provider_id: (adapter, {"tools": []}))]
        _, events, assurance = self.live_events("commit-cancel-confirmed-001", ScriptedAsr(queue=[transcript] * 8), patches)
        cancelled = [e for e in events if e["type"] == "tool.completed" and e["content"].get("call_id") == "call_c"
                     and e["content"].get("status") == "success"]
        accepted = [e for e in events if e["type"] == "confirmation.accepted"]
        return adapter, cancelled, accepted, assurance

    def test_real_engine_refuses_a_negated_confirmation_and_cancels_nothing(self):
        for text in ("I cannot confirm the cancellation.", "I can't confirm the cancellation."):
            self.setUp()  # one TestClient per scenario run
            adapter, cancelled, accepted, _ = self.real_engine_cancellation(text)
            self.assertEqual((cancelled, accepted), ([], []), text)
            self.assertFalse([t for t in getattr(adapter, "texts", []) if "confirmation_id=" in t], text)

    def test_real_engine_cancels_after_an_explicit_affirmation(self):
        adapter, cancelled, accepted, _ = self.real_engine_cancellation("Yes please, cancel payment payment_001 now.")
        self.assertEqual((len(cancelled), len(accepted)), (1, 1))

    def test_out_of_band_confirmation_is_withheld_for_a_negated_confirm(self):
        self.assert_confirmation_withheld("I cannot confirm the cancellation.")

    def test_out_of_band_confirmation_is_withheld_for_a_contracted_negated_confirm(self):
        self.assert_confirmation_withheld("I can\u2019t confirm yet.")

    def test_out_of_band_confirmation_is_withheld_when_the_transcript_is_blank(self):
        self.assert_confirmation_withheld("")

    def test_out_of_band_confirmation_is_withheld_without_an_independent_asr_backend(self):
        calls = []
        adapter, events, _ = self.live_events("commit-cancel-confirmed-001", None, self.cancel_patches(calls))
        observed = [e["content"] for e in events if e["type"] == "confirmation.voice_observation"]
        self.assertEqual([(o["authorised"], o["reason_codes"]) for o in observed], [(False, ["INDEPENDENT_ASR_UNAVAILABLE"])])
        self.assertEqual(calls, ["ambiguous"])

    def test_out_of_band_confirmation_is_authorised_only_after_an_observed_affirmation(self):
        calls = []
        adapter, events, _ = self.live_events("commit-cancel-confirmed-001",
                                              ScriptedAsr(queue=["Yes please, cancel payment payment_001 now."] * 8),
                                              self.cancel_patches(calls))
        observed = [e["content"] for e in events if e["type"] == "confirmation.voice_observation"]
        self.assertEqual([(o["observed_choice"], o["authorised"]) for o in observed], [("affirm", True)])
        self.assertEqual(calls, ["affirm"])
        self.assertTrue([t for t in getattr(adapter, "texts", []) if "confirmation_id=conf_1" in t])


if __name__ == "__main__":
    unittest.main()
