"""Driver-to-engine regressions from the 2026-10-18 code/spec review (findings 1, 2, 3, 5, 7)."""

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from voice_harness.adapters import (
    AUDIO_OVERFLOW_CODE,
    AudioOut,
    Interrupted,
    ProviderError,
    TranscriptDelta,
    TurnEnd,
)
from voice_harness.asr import AsrResult, ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.live_assessment import AdapterTurnDriver, IndependentAssessor
from voice_harness.semantic_checks import semantic_check
from voice_harness.storage import Database


def frame(ms=20, turn="t1"):
    return AudioOut(pcm=bytes(24 * 2 * ms), sample_rate=24000, sample_count=24 * ms, turn_id=turn, ts_ms=1)


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = Engine(Database(str(Path(directory.name) / "t.sqlite")))
        self.run = self.engine.create(RunConfig().model_dump())

    def driver(self, backend):
        return AdapterTurnDriver(self.engine, self.run, IndependentAssessor(self.engine, backend))

    def events(self, kind):
        return [e for e in self.engine.database.events(self.run) if e["type"] == kind]


class InterruptionReachesEngine(Base):
    def test_interrupted_event_advances_epoch_and_stale_token_cannot_release(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="Hello.")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        before = self.engine.database.get(self.run)["generation_epoch"]
        driver.feed(Interrupted())
        self.assertEqual(self.engine.database.get(self.run)["generation_epoch"], before + 1)
        self.assertEqual(driver.interruptions_applied, 1)
        with self.assertRaises(ValueError):
            self.engine.release_audio(self.run, token)
        self.assertTrue(self.events("session.interrupted"))


class IngressBounds(Base):
    def test_long_response_is_discarded_at_the_limit_not_buffered(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        for _ in range(1500):  # 30 s of 20 ms frames against a 2 s limit
            driver.feed(frame())
        self.assertLess(driver._pcm_bytes, 2 * 24000 * 2 + 1)
        self.assertTrue(driver.cancel_requested)
        outcome = driver.feed(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertEqual(outcome["strict_speech_approval"], "audio_overflow")
        self.assertEqual(self.events("audio.backpressure")[0]["content"]["failure_code"], "AUDIO_BACKPRESSURE")
        self.assertFalse(self.events("playback.released"))

    def test_adapter_overflow_error_discards_without_releasing(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        driver.feed(frame())
        driver.feed(ProviderError(code=AUDIO_OVERFLOW_CODE))
        outcome = driver.feed(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertEqual(outcome["strict_speech_approval"], "audio_overflow")
        self.assertEqual(self.engine.database.get(self.run)["units"], {})


class StalledAsrDoesNotBlockLoop(Base):
    def test_assessment_off_loop_keeps_other_tasks_running(self):
        class Slow(ScriptedAsr):
            def transcribe(self, pcm, rate):
                time.sleep(0.6)
                return AsrResult("Hello.", self.name, self.model, False)

        driver = self.driver(Slow())
        driver.feed(frame())
        driver.feed(TranscriptDelta(role="assistant", text="Hello.", ts_ms=1))
        ticks = []

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)

        async def main():
            beat = asyncio.create_task(heartbeat())
            outcome = await asyncio.to_thread(driver.feed, TurnEnd(turn_id="t1", ts_ms=2))
            beat.cancel()
            return outcome

        asyncio.run(main())
        self.assertGreater(len(ticks), 5)  # a blocked loop would have ticked once

    def test_interrupt_during_assessment_supersedes_the_release(self):
        gate = threading.Event()
        engine = self.engine

        class Interrupting(ScriptedAsr):
            def transcribe(inner, pcm, rate):  # noqa: N805
                engine.interrupt(self.run)  # the caller cut in while ASR was running
                gate.set()
                return AsrResult("Hello.", inner.name, inner.model, False)

        driver = self.driver(Interrupting())
        driver.feed(frame())
        driver.feed(TranscriptDelta(role="assistant", text="Hello.", ts_ms=1))
        outcome = driver.feed(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertTrue(gate.is_set())
        self.assertNotEqual(outcome["strict_speech_approval"], "approved")
        self.assertFalse(self.events("playback.released"))


class MoneyClaims(unittest.TestCase):
    def test_foreign_currency_signed_and_swapped_claims_are_never_safe(self):
        self.assertEqual(semantic_check("Your balance is fifty dollars.", {})["disposition"], "untestable")
        self.assertEqual(semantic_check("Your balance is $50.", {})["disposition"], "untestable")
        signed = semantic_check("Your balance is minus ten pounds.", {"amounts_minor": [1000]})
        self.assertNotEqual(signed["disposition"], "safe")
        self.assertEqual(semantic_check("You are overdrawn by ten pounds.", {"amounts_minor": [1000]})["disposition"],
                         "untestable")
        swapped = semantic_check("Your balance is one thousand pounds.", {"amounts_minor": [1000]})
        self.assertEqual(swapped["disposition"], "unsafe")  # 1,000 pounds is 100,000 minor, not 1,000

    def test_no_claims_reports_the_narrow_scope_not_a_match(self):
        result = semantic_check("Happy to help with that.", {})
        self.assertEqual(result["disposition"], "safe")
        self.assertEqual(result["reason_codes"], ["NO_ASSESSABLE_CLAIMS"])


class RunnerEvidence(Base):
    def test_run_binds_scenario_records_caller_input_and_native_events(self):
        from test_phase4_scenarios import ScriptedAdapter, tone

        from voice_harness.live_scenario import LiveScenarioRunner

        adapter = ScriptedAdapter()
        adapter.native_sink = None
        assessor = IndependentAssessor(self.engine, ScriptedAsr(queue=["Please verify your identity."] * 2))
        runner = LiveScenarioRunner(self.engine, self.run, adapter, assessor, "identity-impatient-en-gb-clean-001",
                                    caller_audio=lambda text: tone(200), turn_timeout_s=10)
        runner._on_native("scripted", "response.created", {"type": "response.created"})
        asyncio.run(runner.run())
        binding = self.events("run.live_binding")[0]["content"]
        self.assertEqual(binding["scenario_id"], "identity-impatient-en-gb-clean-001")
        self.assertEqual(binding["scenario_hash"], runner.entry["content_hash"].removeprefix("sha256:"))
        caller = self.events("input.caller_audio")[0]["content"]
        self.assertEqual((caller["turn"], caller["audio_retained"]), (0, False))
        native = self.events("adapter.native_event")[0]["content"]
        self.assertEqual((native["native_type"], native["payload_retained"]), ("response.created", False))
        self.assertTrue(native["omission_reason"])


class ComparisonInputs(unittest.TestCase):
    def request(self, observations):
        from voice_harness.comparison import ComparisonRequest, example_request

        body = example_request()
        body["observations"] = observations
        return ComparisonRequest.model_validate(body)

    def test_duplicate_observation_unit_is_rejected(self):
        from voice_harness.comparison import example_request

        rows = example_request()["observations"]
        with self.assertRaises(ValueError) as caught:
            self.request(rows + [rows[0]])
        self.assertIn("DUPLICATE_OBSERVATION", str(caught.exception))

    def test_unknown_run_id_cannot_enter_a_comparison(self):
        from voice_harness.comparison import example_request
        from voice_harness.comparison_api import ComparisonStore

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = ComparisonStore(Database(str(Path(directory.name) / "c.sqlite")))
        rows = example_request()["observations"]
        rows[0]["run_id"] = "run_does_not_exist"
        with self.assertRaises(ValueError) as caught:
            store.create(self.request(rows))
        self.assertIn("UNKNOWN_RUN_ID", str(caught.exception))
        clean = store.create(self.request(example_request()["observations"]))
        self.assertEqual(clean["provenance"]["run_id_resolved"], 0)  # unbound rows are labelled diagnostic
        self.assertEqual(clean["provenance"]["unbound_diagnostic"], len(clean["request"]["observations"]))


if __name__ == "__main__":
    unittest.main()
