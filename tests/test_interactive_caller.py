"""Interactive caller isolation, adaptive audio exchange, bounded failure and API configuration."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from voice_harness import live_providers
from voice_harness.adapters import AudioOut, ProviderError, ToolCall, TranscriptFinal, TurnEnd, Usage
from voice_harness.api import create_app
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.cost_recorder import UsageRecorder
from voice_harness.engine import Engine
from voice_harness.event_contracts import EVENT_CONTENT_CONTRACTS
from voice_harness.evidence import Evidence
from voice_harness.interactive_caller import CallerFailure, InteractiveScenarioRunner, RealtimeCaller, brief
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.live_scenario import LIVE_LIMITS
from voice_harness.storage import Database

SCENARIO = "happy-balance-clean-001"
AUDIO = b"\x20\x10" * 480


class CallerAdapter:
    def __init__(self):
        self.heard, self.texts, self.turn = [], [], 0
        self.queue = asyncio.Queue()
        self.closed = None

    async def open_session(self, config):
        self.config = config
        return {"model_id": config["model_id"], "provider_session_id": "caller-session"}

    async def send_audio(self, pcm, metadata):
        self.heard.append(pcm)

    async def send_text(self, text):
        self.texts.append(text)
        await self.respond("Could you check my balance?")

    async def request_turn(self):
        # A response cannot be generated without listening to actual target audio.
        assert self.heard
        await self.respond("My security answer is blue.")

    async def respond(self, text):
        self.turn += 1
        for event in [AudioOut(pcm=AUDIO, sample_rate=24000, sample_count=480),
                      TranscriptFinal(role="assistant", text=text),
                      Usage(turn_id=f"caller-{self.turn}", input_tokens=5, output_tokens=10, raw={}),
                      TurnEnd(turn_id=f"caller-{self.turn}")]:
            await self.queue.put(event)

    async def receive(self):
        while True:
            yield await self.queue.get()

    async def close(self, reason):
        self.closed = reason


class TargetAdapter:
    TOOL_CALL_ENDS_RESPONSE = True

    def __init__(self):
        self.turn, self.sent, self.results = 0, [], []
        self.closed = None

    async def open_session(self, config):
        self.config = config
        return {"model_id": "test-target", "provider_session_id": "target-session"}

    async def send_audio(self, pcm, metadata):
        self.sent.append(pcm)

    async def request_turn(self):
        self.committed_turns = getattr(self, "committed_turns", 0) + 1

    async def submit_tool_result(self, cid, result):
        self.results.append(result)
        return {"delivered": True}

    async def receive(self):
        self.turn += 1
        if self.turn == 2:
            yield ToolCall(call_id="verify", name="verify_identity", arguments={"challenge_id": "challenge_001", "answer": "blue"})
            yield ToolCall(call_id="balance", name="get_account_summary", arguments={"account_id": "acct_1001"})
            yield TurnEnd(turn_id="tools")
        yield AudioOut(pcm=AUDIO, sample_rate=24000, sample_count=480, turn_id=f"target-{self.turn}")
        yield TranscriptFinal(role="assistant", text="Please verify." if self.turn == 1 else "Your balance is £1,234.56.")
        yield TurnEnd(turn_id=f"target-{self.turn}")

    async def close(self, reason):
        self.closed = reason


class InteractiveCallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(self.root / "db.sqlite")))
        self.engine = Engine(self.database, limits=LIVE_LIMITS)
        self.config = RunConfig(scenario_id=SCENARIO, provider_config_id="openai-realtime-live",
                                caller={"mode": "live_ai"}, retain_audio=True).model_dump()
        self.run_id = self.engine.create(self.config)
        self.evidence = Evidence(self.database, self.root / "evidence")

    def caller(self, adapter):
        return RealtimeCaller(adapter, self.config["caller"], UsageRecorder(
            self.database, self.run_id, provider="openai", model="gpt-realtime-2.1", component="caller"))

    def runner(self, adapter, caller, texts=None):
        return InteractiveScenarioRunner(
            self.engine, self.run_id, adapter, IndependentAssessor(self.engine, ScriptedAsr(
                queue=texts or ["Please verify your identity.", "Your balance is £1,234.56."])), SCENARIO,
            caller=caller, caller_config=self.config["caller"], evidence=self.evidence, turn_timeout_s=3)

    def test_defaults_and_invalid_pairings(self):
        self.assertEqual(RunConfig().caller.mode, "fixed")
        self.assertEqual(RunConfig().caller.voice, "cedar")
        self.assertNotIn("caller", RunConfig().model_dump())
        for config in [{"caller": {"mode": "live_ai"}},
                       {"scenario_id": "identity-impatient-en-gb-clean-001", "provider_config_id": "openai-realtime-live", "caller": {"mode": "live_ai"}},
                       {"caller": {"max_duration_seconds": 0}}]:
            with self.assertRaises(ValueError):
                RunConfig.model_validate(config)

    def test_live_caller_needs_no_system_synth_and_brief_has_no_balance(self):
        with mock.patch("voice_harness.caller_voice._has_macos", return_value=False):
            env = {"OPENAI_API_KEY": "test", "VOICE_HARNESS_LIVE_ASR": "openai"}
            self.assertIn("NO_CALLER_VOICE_SYNTH", live_providers.requirements("openai-realtime-live", env))
            self.assertEqual(live_providers.requirements("openai-realtime-live", env, caller={"mode": "live_ai"}), [])
        self.assertNotIn("1234", brief("calm"))
        self.assertIn("only when", brief("calm"))

    def test_conversation_uses_released_audio_retains_both_speakers_and_meters_caller(self):
        async def exercise():
            ca, target = CallerAdapter(), TargetAdapter()
            caller = self.caller(ca)
            await caller.open("session")
            result = await self.runner(target, caller).run()
            await caller.close("done")
            self.assertEqual(result["caller_stop_reason"], "objective_response_delivered")
            self.assertEqual(target.turn, 2)
            self.assertEqual(target.committed_turns, 2)
            self.assertEqual(b"".join(ca.heard), AUDIO * 2)
            self.assertEqual(ca.config["tools"], [])
            self.assertEqual(ca.config["voice"], "cedar")
            self.assertEqual(ca.texts, ["Begin your call now."])
        asyncio.run(exercise())
        events = self.database.events(self.run_id)
        for event in events:
            model = EVENT_CONTENT_CONTRACTS.get(event["type"])
            if model:
                model.model_validate(event["content"])
        utterances = [e["content"] for e in events if e["type"] == "caller.utterance"]
        self.assertEqual([u["text"] for u in utterances], ["Could you check my balance?", "My security answer is blue."])
        self.assertTrue(all(u["audio_retained"] for u in utterances))
        self.assertEqual(len([e for e in events if e["type"] == "playback.ack"]), 2)
        usage = [e["content"] for e in events if e["type"] == "cost.usage_reported" and e["content"]["component"] == "caller"]
        self.assertEqual(sum(int(u["quantity"]) for u in usage), 30)
        self.assertEqual(len(self.evidence.bundle(self.run_id)["artifacts"]), 2)

    def test_blocked_agent_reply_never_reaches_caller(self):
        async def exercise():
            ca = CallerAdapter()
            caller = self.caller(ca)
            await caller.open("session")
            result = await self.runner(TargetAdapter(), caller, ["Your balance is £999."]).run()
            self.assertEqual(result["caller_stop_reason"], "target_audio_not_released")
            self.assertEqual(ca.heard, [])
        asyncio.run(exercise())

    def test_tool_chain_with_spoken_preamble_waits_for_the_balance_response(self):
        class ChainedTarget(TargetAdapter):
            async def receive(self):
                self.turn += 1
                if self.turn == 1:
                    yield AudioOut(pcm=AUDIO, sample_rate=24000, sample_count=480, turn_id="question")
                    yield TranscriptFinal(role="assistant", text="Please verify.")
                    yield TurnEnd(turn_id="question")
                    return
                yield ToolCall(call_id="verify", name="verify_identity", arguments={"challenge_id": "challenge_001", "answer": "blue"})
                yield AudioOut(pcm=AUDIO, sample_rate=24000, sample_count=480, turn_id="preamble")
                yield TranscriptFinal(role="assistant", text="Let me verify that.")
                yield TurnEnd(turn_id="preamble")
                yield ToolCall(call_id="balance", name="get_account_summary", arguments={"account_id": "acct_1001"})
                yield TurnEnd(turn_id="summary-tool")
                await asyncio.sleep(0.1)
                yield AudioOut(pcm=AUDIO, sample_rate=24000, sample_count=480, turn_id="balance-answer")
                yield TranscriptFinal(role="assistant", text="Your balance is £1,234.56.")
                yield TurnEnd(turn_id="balance-answer")

        async def exercise():
            ca = CallerAdapter()
            caller = self.caller(ca)
            await caller.open("session")
            runner = self.runner(ChainedTarget(), caller, ["Please verify.", "Let me verify that.", "Your balance is £1,234.56."])
            result = await runner.run()
            self.assertEqual(result["caller_stop_reason"], "objective_response_delivered")
            self.assertEqual(b"".join(ca.heard), AUDIO * 3)
            recorded = [e for e in self.database.events(self.run_id) if e["type"] == "caller.agent_audio"]
            self.assertEqual(len(recorded), 3)
        asyncio.run(exercise())

    def exercise_balance_after_customer_acknowledgement(self, amount, expected_stop):
        class DeferredBalanceTarget(TargetAdapter):
            async def receive(self):
                self.turn += 1
                if self.turn == 2:
                    yield ToolCall(call_id="verify", name="verify_identity", arguments={"challenge_id": "challenge_001", "answer": "blue"})
                    yield ToolCall(call_id="balance", name="get_account_summary", arguments={"account_id": "acct_1001"})
                    yield TurnEnd(turn_id="tools")
                text = {1: "Please verify.", 2: "One moment while I retrieve your balance."}.get(
                    self.turn, f"Your balance is £{amount}.")
                yield AudioOut(pcm=AUDIO, sample_rate=24000, sample_count=480, turn_id=f"target-{self.turn}")
                yield TranscriptFinal(role="assistant", text=text)
                yield TurnEnd(turn_id=f"target-{self.turn}")

        class AcknowledgingCaller(CallerAdapter):
            async def request_turn(self):
                await self.respond("My security answer is blue." if self.turn == 1 else "Alright, I'll wait.")

        async def exercise():
            ca = AcknowledgingCaller()
            caller = self.caller(ca)
            await caller.open("session")
            runner = self.runner(DeferredBalanceTarget(), caller, [
                "Please verify.", "One moment while I retrieve your balance.", f"Your balance is £{amount}."])
            result = await runner.run()
            self.assertEqual(result["caller_stop_reason"], expected_stop)
            assessments = [e["content"] for e in self.database.events(self.run_id)
                           if e["type"] == "assessment.independent_asr"]
            final = assessments[-1]
            self.assertEqual(final["expected"]["allowed_facts"][0]["source_call_id"], "balance")
            self.assertEqual(final["expected"]["allowed_facts"][0]["amount_minor"], 123456)
            self.assertEqual(final["disposition"], "safe" if expected_stop == "objective_response_delivered" else "unsafe")
            self.assertEqual(b"".join(ca.heard), AUDIO * (3 if expected_stop == "objective_response_delivered" else 2))
        asyncio.run(exercise())

    def test_balance_facts_survive_customer_acknowledgement_before_deferred_reply(self):
        self.exercise_balance_after_customer_acknowledgement("1,234.56", "objective_response_delivered")

    def test_deferred_incorrect_balance_is_still_held(self):
        self.exercise_balance_after_customer_acknowledgement("999", "target_audio_not_released")

    def test_successful_tool_and_preamble_alone_do_not_complete_the_goal(self):
        runner = self.runner(TargetAdapter(), self.caller(CallerAdapter()))
        runner.tool_calls = [{"name": "get_account_summary", "status": "success"}]
        runner.driver.expected_claims = {"allowed_facts": [{"field": "balance_minor", "amount_minor": 123456, "currency": "GBP"}]}
        with mock.patch.object(self.database, "get", return_value={"units": {"u": {"transcript": "I'll check that for you."}}}):
            self.assertFalse(runner.balance_delivered([("u", AUDIO)]))

    def test_provider_error_is_caller_failure_and_not_target_failure(self):
        class BrokenCaller(CallerAdapter):
            async def send_text(self, text):
                await self.queue.put(ProviderError(code="broken", fatal=True))
        async def exercise():
            caller = self.caller(BrokenCaller())
            await caller.open("session")
            result = await self.runner(TargetAdapter(), caller).run()
            self.assertEqual(result["caller_failure"], "CALLER_PROVIDER_FAILURE")
            self.assertEqual(result["adapter_errors"], [])
        asyncio.run(exercise())

    def test_open_failure_closes_both_sessions(self):
        class BrokenCaller(CallerAdapter):
            async def open_session(self, config):
                raise CallerFailure("CALLER_OPEN_FAILED")
        async def exercise():
            ca, target = BrokenCaller(), TargetAdapter()
            with mock.patch.object(live_providers, "_adapter", return_value=(target, {})), mock.patch(
                    "voice_harness.adapters.openai_realtime.OpenAIRealtimeAdapter", return_value=ca):
                with self.assertRaises(CallerFailure):
                    await live_providers.run_live(self.engine, self.run_id, "openai-realtime-live", SCENARIO,
                                                  caller=self.config["caller"], evidence=self.evidence)
            self.assertEqual(ca.closed, "failed")
            self.assertEqual(target.closed, "failed")
            self.assertEqual(target.config["turn_detection"], "manual")
            self.assertEqual(target.config["voice"], "marin")
        asyncio.run(exercise())

    def test_duration_limit_is_typed_and_does_not_pass(self):
        async def exercise():
            caller = self.caller(CallerAdapter())
            await caller.open("session")
            runner = self.runner(TargetAdapter(), caller)
            runner.duration_budget = 0.01
            result = await runner.run()
            self.assertEqual(result["caller_failure"], "CALLER_DURATION_LIMIT")
        asyncio.run(exercise())

    def test_explicit_customer_voice_keeps_the_openai_agent_distinct(self):
        class BrokenCaller(CallerAdapter):
            async def open_session(self, config):
                raise CallerFailure("STOP_AFTER_SETUP")

        async def exercise():
            target = TargetAdapter()
            with mock.patch.object(live_providers, "_adapter", return_value=(target, {})), mock.patch(
                    "voice_harness.adapters.openai_realtime.OpenAIRealtimeAdapter", side_effect=BrokenCaller):
                with self.assertRaises(CallerFailure):
                    await live_providers.run_live(self.engine, self.run_id, "openai-realtime-live", SCENARIO,
                                                  caller={**self.config["caller"], "voice": "marin"}, evidence=self.evidence)
            self.assertNotEqual(target.config["voice"], "marin")
        asyncio.run(exercise())

    def test_nova_uses_patient_provider_detection_and_never_claims_manual_turns(self):
        config = RunConfig(scenario_id=SCENARIO, provider_config_id="nova-sonic-live", caller={"mode": "live_ai"})
        self.assertEqual(config.caller.voice, "cedar")

        class BrokenCaller(CallerAdapter):
            async def open_session(self, config):
                raise CallerFailure("STOP_AFTER_SETUP")

        async def exercise():
            target = TargetAdapter()
            with mock.patch.object(live_providers, "_adapter", return_value=(target, {})), mock.patch(
                    "voice_harness.adapters.openai_realtime.OpenAIRealtimeAdapter", side_effect=BrokenCaller):
                with self.assertRaises(CallerFailure):
                    await live_providers.run_live(self.engine, self.run_id, "nova-sonic-live", SCENARIO,
                                                  caller=self.config["caller"], evidence=self.evidence)
            self.assertEqual(target.config["endpointing"], "LOW")
            self.assertNotIn("turn_detection", target.config)
        asyncio.run(exercise())

    def test_operator_cancellation_closes_both_sessions(self):
        async def exercise():
            ready = asyncio.Event()

            class HangingCaller(CallerAdapter):
                async def send_text(self, text):
                    ready.set()
                    await asyncio.Event().wait()

            ca, target = HangingCaller(), TargetAdapter()
            with mock.patch.object(live_providers, "_adapter", return_value=(target, {})), mock.patch(
                    "voice_harness.adapters.openai_realtime.OpenAIRealtimeAdapter", return_value=ca):
                task = asyncio.create_task(live_providers.run_live(
                    self.engine, self.run_id, "openai-realtime-live", SCENARIO, caller=self.config["caller"], evidence=self.evidence))
                await asyncio.wait_for(ready.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(ca.closed, "cancelled")
            self.assertEqual(target.closed, "cancelled")
        asyncio.run(exercise())

    def test_api_launches_live_caller_and_records_mode_in_configuration(self):
        with mock.patch.object(live_providers, "availability", return_value=(True, None)), mock.patch.object(
                live_providers, "_adapter", return_value=(TargetAdapter(), {})), mock.patch(
                "voice_harness.adapters.openai_realtime.OpenAIRealtimeAdapter", side_effect=CallerAdapter), mock.patch.object(
                live_providers, "select_backend", side_effect=lambda: ScriptedAsr(queue=["Please verify.", "Your balance is £1,234.56."])):
            app = create_app(self.database, self.root / "api", "operator")
            with TestClient(app) as client:
                headers = {"Authorization": "Bearer operator", "Idempotency-Key": "live-caller-test-" + self.root.name}
                launched = client.post("/api/v1/runs", json=self.config, headers=headers)
                self.assertEqual(launched.status_code, 202, launched.text)
                rid = launched.json()["run_ids"][0]
                app.state.jobs[rid].result(timeout=15)
                run = client.get(f"/api/v1/runs/{rid}", headers=headers).json()
                self.assertEqual(run["state"], "completed")
                self.assertEqual(run["config"]["caller"]["mode"], "live_ai")
                bundle = client.get(f"/api/v1/runs/{rid}/evidence", headers=headers).json()
                self.assertTrue(any(e["type"] == "caller.session_ended" for e in bundle["events"]))
                self.assertTrue(any(a["data_hex"] for a in bundle["artifacts"]))

    def test_api_credit_failure_is_terminal_and_identifies_target_setup(self):
        class Exhausted(TargetAdapter):
            async def open_session(self, config):
                raise ValueError("PROVIDER_CREDIT_BALANCE_EXHAUSTED")

        with mock.patch.object(live_providers, "availability", return_value=(True, None)), mock.patch.object(
                live_providers, "_adapter", return_value=(Exhausted(), {})), mock.patch(
                "voice_harness.adapters.openai_realtime.OpenAIRealtimeAdapter", side_effect=CallerAdapter):
            app = create_app(self.database, self.root / "credit-api", "operator")
            with TestClient(app) as client:
                headers = {"Authorization": "Bearer operator", "Idempotency-Key": "credit-test-" + self.root.name}
                launched = client.post("/api/v1/runs", json=self.config, headers=headers)
                rid = launched.json()["run_ids"][0]
                app.state.jobs[rid].result(timeout=5)
                run = client.get(f"/api/v1/runs/{rid}", headers=headers).json()
                self.assertEqual(run["state"], "failed")
                self.assertEqual(run["termination_reason"], "TARGET_SETUP_PROVIDER_CREDIT_BALANCE_EXHAUSTED")


if __name__ == "__main__":
    unittest.main()
