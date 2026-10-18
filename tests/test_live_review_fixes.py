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
    TurnStart,
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
        for spoken in ("Your balance is £-10.", "Your balance is -10 pounds."):
            self.assertNotEqual(semantic_check(spoken, {"amounts_minor": [1000]})["disposition"], "safe", spoken)
        swapped = semantic_check("Your balance is one thousand pounds.", {"amounts_minor": [1000]})
        self.assertEqual(swapped["disposition"], "unsafe")  # 1,000 pounds is 100,000 minor, not 1,000

    def test_no_claims_reports_the_narrow_scope_not_a_match(self):
        result = semantic_check("Happy to help with that.", {})
        self.assertEqual(result["disposition"], "safe")
        self.assertEqual(result["reason_codes"], ["NO_ASSESSABLE_CLAIMS"])


class LateWorkCannotRelease(Base):
    def test_late_asr_success_after_abandon_releases_nothing(self):
        started, proceed = threading.Event(), threading.Event()

        class Slow(ScriptedAsr):
            def transcribe(inner, pcm, rate):  # noqa: N805
                started.set()
                proceed.wait(5)
                return AsrResult("Hello.", inner.name, inner.model, False)

        driver = self.driver(Slow())
        driver.feed(frame())
        driver.feed(TranscriptDelta(role="assistant", text="Hello.", ts_ms=1))
        results = []
        worker = threading.Thread(target=lambda: results.append(driver.feed(TurnEnd(turn_id="t1", ts_ms=2))))
        worker.start()
        self.assertTrue(started.wait(5))
        driver.abandon()  # the turn timed out; the worker is still inside ASR
        driver.feed(frame(turn="t2"))  # unkeyed late frame: quarantined until the next response boundary
        self.assertEqual(driver._pcm_bytes, 0)
        driver.feed(TurnStart(turn_id="t2", ts_ms=3))
        driver.feed(frame(turn="t2"))  # next response buffers while the old worker is alive
        proceed.set()
        worker.join(5)
        self.assertNotEqual(results[0]["strict_speech_approval"], "approved")
        self.assertFalse(self.events("playback.released"))
        self.assertEqual(driver._pcm_bytes, 24 * 2 * 20)  # the worker did not clobber the new buffer

    def test_late_audio_of_an_interrupted_item_is_dropped(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        first = AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t1", item_id="item_a", ts_ms=1)
        driver.feed(first)
        driver.feed(Interrupted(item_id="item_a"))
        driver.feed(first)  # arrives after the interruption
        self.assertEqual(driver._pcm_bytes, 0)
        outcome = driver.feed(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertIsNone(outcome)
        self.assertFalse(self.events("playback.released"))

    def test_asr_time_counts_against_the_decision_deadline(self):
        clock = [0.0]
        self.engine.clock = lambda: clock[0]

        class Slow(ScriptedAsr):
            def transcribe(inner, pcm, rate):  # noqa: N805
                clock[0] += 6.0  # longer than the 5 s decision limit
                return AsrResult("Hello.", inner.name, inner.model, False)

        driver = self.driver(Slow())
        driver.feed(frame())
        driver.feed(TranscriptDelta(role="assistant", text="Hello.", ts_ms=1))
        outcome = driver.feed(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertNotEqual(outcome["strict_speech_approval"], "approved")
        self.assertFalse(self.events("playback.released"))


class SlowAsr(ScriptedAsr):
    def __init__(self, text="Hello.", delay=0.3):
        super().__init__()
        self.text, self.delay = text, delay

    def transcribe(self, pcm, rate):
        time.sleep(self.delay)
        return AsrResult(self.text, self.name, self.model, False)


class RunnerConcurrency(Base):
    """Runner-level orderings from the 31ccddd recheck (F1-F3): the pump, not just the driver."""

    def runner(self, adapter, asr, turn_timeout_s=10):
        from test_phase4_scenarios import tone

        from voice_harness.live_scenario import LiveScenarioRunner

        return LiveScenarioRunner(self.engine, self.run, adapter, IndependentAssessor(self.engine, asr),
                                  "identity-impatient-en-gb-clean-001", caller_audio=lambda text: tone(100),
                                  turn_timeout_s=turn_timeout_s)

    def adapter(self, events):
        class Adapter:
            async def send_audio(self, data, metadata):
                pass

            async def submit_tool_result(self, call_id, result):
                return {"delivered": True}

            async def receive(self):
                for event in events:
                    yield event
                    await asyncio.sleep(0)

        return Adapter()

    def audio(self, item):
        return AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="r1", item_id=item, ts_ms=1)

    def test_second_end_then_interruption_during_asr_releases_nothing(self):
        for second in ("duplicate", "other_response"):
            with self.subTest(second):
                self.setUp()
                events = [self.audio("audio-a"), TurnEnd(turn_id="r1", ts_ms=2)]
                events += [TurnEnd(turn_id="r1", ts_ms=3)] if second == "duplicate" else [
                    self.audio("audio-b"), TurnEnd(turn_id="r2", ts_ms=3)]
                events.append(Interrupted(item_id="audio-a"))
                runner = self.runner(self.adapter(events), SlowAsr(delay=0.3))
                summary = asyncio.run(runner.run())
                self.assertNotEqual(summary["turns"][0]["strict_speech_approval"], "approved")
                self.assertFalse(self.events("playback.released"))
                self.assertGreaterEqual(runner.driver.interruptions_applied, 1)

    def test_timeout_during_asr_retires_the_active_item(self):
        events = [self.audio("audio-a"), TurnEnd(turn_id="r1", ts_ms=2)]
        runner = self.runner(self.adapter(events + [Interrupted(item_id="never")] * 0), SlowAsr(delay=0.5),
                             turn_timeout_s=0.1)

        class Hang:
            async def send_audio(self, data, metadata):
                pass

            async def submit_tool_result(self, call_id, result):
                return {"delivered": True}

            async def receive(inner):  # noqa: N805
                for event in events:
                    yield event
                await asyncio.sleep(5)

        runner.adapter = Hang()
        summary = asyncio.run(runner.run())
        self.assertEqual(summary["turns"][0]["strict_speech_approval"], "turn_timeout")
        runner.driver.feed(self.audio("audio-a"))  # late audio of the retired item
        self.assertEqual(runner.driver._pcm_bytes, 0)
        runner.driver.feed(self.audio("audio-new"))  # a genuinely new response is still usable
        self.assertEqual(runner.driver._pcm_bytes, 960)
        time.sleep(0.6)  # the old worker finishes; nothing may have been released
        self.assertFalse(self.events("playback.released"))

    def test_facts_added_during_asr_do_not_change_an_assessment_in_flight(self):
        driver = self.driver(SlowAsr("Your balance is ten pounds.", delay=0.2))
        driver.feed(frame())
        job, _ = driver.begin(TurnEnd(turn_id="t1", ts_ms=2))
        results = []
        worker = threading.Thread(target=lambda: results.append(driver.analyse(job)))
        worker.start()
        driver.expected_claims = {**driver.expected_claims, "allowed_facts": [
            {"account_id": "acct_1001", "field": "balance_minor", "amount_minor": 1000, "currency": "GBP",
             "source_call_id": "call_late", "source_ts_ms": 1}]}
        worker.join(5)
        outcome = driver.commit(job, results[0])
        self.assertNotEqual(outcome["strict_speech_approval"], "approved")
        self.assertFalse(self.events("playback.released"))

    def test_hung_provider_cancel_does_not_stall_the_turn(self):
        import voice_harness.live_scenario as live

        class Hang:
            async def interrupt(self, item_id, ms):
                await asyncio.sleep(30)

        runner = self.runner(Hang(), ScriptedAsr())
        original, live.CANCEL_TIMEOUT_S = live.CANCEL_TIMEOUT_S, 0.1
        self.addCleanup(setattr, live, "CANCEL_TIMEOUT_S", original)
        started = time.monotonic()
        asyncio.run(runner._cancel_generation())
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(runner.interrupt_results[0]["generation_cancelled"], "failed")

    def test_wrong_field_amount_is_not_a_balance(self):
        facts = {"allowed_facts": [
            {"account_id": "acct_1001", "field": "balance_minor", "amount_minor": 123456, "currency": "GBP",
             "source_call_id": "c1", "source_ts_ms": 1},
            {"account_id": "acct_1001", "field": "amount_minor", "amount_minor": 1000, "currency": "GBP",
             "source_call_id": "c2", "source_ts_ms": 1}]}
        wrong = semantic_check("Your balance is ten pounds.", facts)
        self.assertEqual((wrong["disposition"], wrong["reason_codes"]), ("unsafe", ["FIELD_MISMATCH"]))
        right = semantic_check("Your balance is one thousand two hundred and thirty four pounds fifty six pence.", facts)
        self.assertEqual(right["disposition"], "safe")
        payment = semantic_check("The pending payment is ten pounds.", facts)
        self.assertEqual(payment["disposition"], "safe")


class QueueBudgets(unittest.TestCase):
    def adapter(self):
        from voice_harness.adapters.openai_realtime import OpenAIRealtimeAdapter

        adapter = OpenAIRealtimeAdapter(api_key="unused")
        adapter.session_id = "s"
        return adapter

    def test_final_transcripts_are_bounded_but_control_events_are_not(self):
        from voice_harness.adapters import AUDIO_QUEUE_MAX, TranscriptFinal

        adapter = self.adapter()
        for _ in range(AUDIO_QUEUE_MAX * 2):
            adapter._emit(TranscriptFinal, role="assistant", text="x")
        self.assertLessEqual(adapter._queue.qsize(), AUDIO_QUEUE_MAX + 1)  # one overflow signal
        adapter._emit(TurnEnd)
        adapter._emit(ProviderError, code="FATAL", fatal=True)
        self.assertGreater(adapter._queue.qsize(), AUDIO_QUEUE_MAX)  # control path stays open

    def test_decoded_audio_bytes_are_budgeted(self):
        from voice_harness.adapters import AUDIO_QUEUE_BYTES_MAX

        adapter = self.adapter()
        for _ in range(100):
            adapter._emit(AudioOut, pcm=bytes(96000), sample_rate=24000, sample_count=48000)
        self.assertLessEqual(adapter._budget.audio_bytes, AUDIO_QUEUE_BYTES_MAX)
        self.assertGreaterEqual(adapter.dropped_audio_events, 1)


class AccountBindingAndDisposition(Base):
    def facts(self, account):
        return {"allowed_facts": [{"account_id": account, "field": "balance_minor", "amount_minor": 123456,
                                   "currency": "GBP", "source_call_id": "c1", "source_ts_ms": 1}]}

    def test_only_the_verified_accounts_facts_can_be_spoken(self):
        text = "Your balance is one thousand two hundred and thirty four pounds fifty six pence."
        ok = semantic_check(text, {**self.facts("acct_1001"), "verified_account_id": "acct_1001"})
        other = semantic_check(text, {**self.facts("acct_2002"), "verified_account_id": "acct_1001"})
        unverified = semantic_check(text, {**self.facts("acct_1001"), "verified_account_id": None})
        self.assertEqual(ok["disposition"], "safe")
        self.assertEqual(other["disposition"], "unsafe")
        self.assertEqual(unverified["disposition"], "unsafe")

    def test_unverified_session_freezes_no_verified_account_into_the_job(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        driver.feed(frame())
        job, _ = driver.begin(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertIsNone(job.facts["verified_account_id"])  # identity not verified in this fresh run

    def test_unkeyed_audio_is_quarantined_after_interrupt_until_a_boundary(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        driver.feed(frame())
        driver.feed(Interrupted())
        driver.feed(frame())
        self.assertEqual(driver._pcm_bytes, 0)
        driver.feed(TurnEnd(turn_id="t1", ts_ms=2))  # boundary
        driver.feed(frame())
        self.assertEqual(driver._pcm_bytes, 960)
        keyed = AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t", item_id="new", ts_ms=1)
        driver.feed(Interrupted())
        driver.feed(keyed)  # a keyed new item is never quarantined
        self.assertEqual(driver._pcm_bytes, 960)

    def test_abandon_records_a_durable_disposition(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        audio = AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t", item_id="item_a", ts_ms=1)
        driver.feed(audio)
        job, _ = driver.begin(TurnEnd(turn_id="t", ts_ms=2))
        driver.feed(AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t", item_id="item_b", ts_ms=3))
        driver.abandon("TURN_TIMEOUT")
        event = self.events("assessment.abandoned")[0]["content"]
        self.assertEqual(event["reason"], "TURN_TIMEOUT")
        self.assertEqual(event["retired_jobs"][0]["item_ids"], ["item_a"])
        self.assertEqual(event["buffered_item_ids"], ["item_b"])
        self.assertFalse(event["released"])
        self.assertEqual(driver.commit(job, driver.analyse(job))["strict_speech_approval"], "superseded")


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


class ComparisonProvenance(unittest.TestCase):
    def test_provenance_is_in_the_export_and_covered_by_its_hash(self):
        from voice_harness.comparison import example_request
        from voice_harness.comparison_api import ComparisonStore, export_payload, verify_stored_export

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = ComparisonStore(Database(str(Path(directory.name) / "c.sqlite")))
        from voice_harness.comparison import ComparisonRequest

        stored = store.create(ComparisonRequest.model_validate(example_request()))
        export = export_payload(store.get(stored["comparison_id"]))
        self.assertIn("unbound_diagnostic", export["provenance"])
        self.assertTrue(verify_stored_export(export))
        tampered = {**export, "provenance": {**export["provenance"], "run_id_resolved": 99}}
        self.assertFalse(verify_stored_export(tampered))


if __name__ == "__main__":
    unittest.main()


class ClaimAttribution(unittest.TestCase):
    """89fa78f recheck finding 1: each amount is bound to its own clause's field and to the verified account."""

    FACTS = {"verified_account_id": "acct_1001", "allowed_facts": [
        {"account_id": "acct_1001", "field": "balance_minor", "amount_minor": 123456, "currency": "GBP",
         "source_call_id": "c1", "source_ts_ms": 1},
        {"account_id": "acct_1001", "field": "amount_minor", "amount_minor": 1000, "currency": "GBP",
         "source_call_id": "c2", "source_ts_ms": 1}]}
    BIG = "one thousand two hundred and thirty four pounds fifty six pence"

    def test_swapped_clauses_are_never_safe(self):
        swapped = semantic_check(f"Your balance is ten pounds and the payment is {self.BIG}.", self.FACTS)
        self.assertNotEqual(swapped["disposition"], "safe")
        digits = semantic_check("Your balance is £10.00 and the payment is £1,234.56.", self.FACTS)
        self.assertNotEqual(digits["disposition"], "safe")

    def test_correct_clauses_are_safe(self):
        ok = semantic_check(f"Your balance is {self.BIG} and the payment is ten pounds.", self.FACTS)
        self.assertEqual(ok["disposition"], "safe")

    def test_correct_amount_attributed_to_another_account_is_unsafe(self):
        other = semantic_check(f"The balance on acct_2002 is {self.BIG}.", self.FACTS)
        self.assertEqual(other["disposition"], "unsafe")
        self.assertIn("ACCOUNT_MISMATCH", other["reason_codes"])
        own = semantic_check(f"The balance on acct_1001 is {self.BIG}.", self.FACTS)
        self.assertEqual(own["disposition"], "safe")

    def test_unresolvable_association_is_held(self):
        held = semantic_check(f"Ten pounds is the payment, {self.BIG} is the balance.", self.FACTS)
        self.assertNotEqual(held["disposition"], "safe")
        mixed = semantic_check(f"Your balance is {self.BIG} and ten pounds.", self.FACTS)
        self.assertNotEqual(mixed["disposition"], "safe")


class ExpiredVerification(Base):
    """89fa78f recheck finding 2: an expired verification is not a verified account."""

    TEXT = "Your balance is one thousand two hundred and thirty four pounds fifty six pence."

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = [1000.0]
        self.engine = Engine(Database(str(Path(directory.name) / "t.sqlite")), clock=lambda: self.now[0])
        self.run = self.engine.create(RunConfig().model_dump())
        with self.engine.database.transaction(self.run) as tx:
            tx.state["identity"], tx.state["identity_expires"] = "verified", 1900
        fact = {"account_id": "acct_1001", "field": "balance_minor", "amount_minor": 123456, "currency": "GBP",
                "source_call_id": "c1", "source_ts_ms": 1}
        self.facts = {"allowed_facts": [fact]}

    def driver(self, text):
        return AdapterTurnDriver(self.engine, self.run, IndependentAssessor(self.engine, ScriptedAsr(queue=[text])),
                                 expected_claims=self.facts)

    def test_expired_before_the_job_is_created(self):
        driver = self.driver(self.TEXT)
        driver.feed(frame())
        self.now[0] = 1901
        job, _ = driver.begin(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertIsNone(job.facts["verified_account_id"])
        outcome = driver.commit(job, driver.analyse(job))
        self.assertNotEqual(outcome["strict_speech_approval"], "approved")
        self.assertFalse(self.events("playback.released"))

    def test_expires_while_the_job_is_in_flight(self):
        # Begin one second before expiry and commit one second after: well inside the five-second decision
        # deadline, so only the identity revalidation can refuse (not DECISION_TIMEOUT).
        self.now[0] = 1899
        driver = self.driver(self.TEXT)
        driver.feed(frame())
        job, _ = driver.begin(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertEqual(job.facts["verified_account_id"], "acct_1001")
        analysis = driver.analyse(job)
        self.now[0] = 1901
        outcome = driver.commit(job, analysis)
        self.assertEqual(outcome["strict_speech_approval"], "held")
        self.assertEqual(outcome["reason_codes"][0], "IDENTITY_NOT_CURRENT")
        self.assertNotIn("DECISION_TIMEOUT", outcome["reason_codes"])
        self.assertFalse(self.events("playback.released"))
        self.assertFalse(self.events("audio.decision_timeout"))

    def test_identity_replaced_while_the_job_is_in_flight_is_not_the_frozen_identity(self):
        driver = self.driver(self.TEXT)
        driver.feed(frame())
        job, _ = driver.begin(TurnEnd(turn_id="t1", ts_ms=2))
        analysis = driver.analyse(job)
        with self.engine.database.transaction(self.run) as tx:
            tx.state["identity_version"] += 1  # re-verified or invalidated since the job was frozen
        outcome = driver.commit(job, analysis)
        self.assertEqual(outcome["reason_codes"][0], "IDENTITY_NOT_CURRENT")
        self.assertFalse(self.events("playback.released"))

    def test_still_valid_verification_is_approved(self):
        driver = self.driver(self.TEXT)
        driver.feed(frame())
        job, _ = driver.begin(TurnEnd(turn_id="t1", ts_ms=2))
        self.assertEqual(driver.commit(job, driver.analyse(job))["strict_speech_approval"], "approved")


class UnkeyedQuarantineBeforeFirstFrame(Base):
    """89fa78f recheck finding 3: an unkeyed interruption before any frame still retires the unkeyed generation."""

    runner, adapter, audio = RunnerConcurrency.runner, RunnerConcurrency.adapter, RunnerConcurrency.audio

    def late(self):
        return AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="old", ts_ms=1)

    def test_runner_drops_late_unkeyed_frames_after_a_prefirst_frame_interruption(self):
        events = [Interrupted(), self.late(), TurnEnd(turn_id="old", ts_ms=2)]
        runner = self.runner(self.adapter(events), ScriptedAsr(queue=["Hello."] * 3))
        asyncio.run(runner.run())
        self.assertFalse(self.events("playback.released"))
        self.assertFalse([r for r in runner.driver.results if r.get("strict_speech_approval") == "approved"])

    def test_driver_quarantines_then_resumes_at_a_boundary(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        driver.feed(Interrupted())
        driver.feed(self.late())
        self.assertEqual(driver._pcm_bytes, 0)
        driver.feed(self.audio("new-item"))  # a keyed new item is still eligible
        self.assertEqual(driver._pcm_bytes, 960)
        driver.feed(TurnEnd(turn_id="x", ts_ms=3))
        driver.feed(self.late())
        self.assertEqual(driver._pcm_bytes, 960)  # accepted again after the boundary

    def test_mixed_keyed_and_unkeyed_response_quarantines_on_interrupt(self):
        driver = self.driver(ScriptedAsr(queue=["Hello."]))
        driver.feed(self.audio("item-a"))
        driver.feed(self.late())
        driver.feed(Interrupted(item_id="item-a"))
        driver.feed(self.late())
        self.assertEqual(driver._pcm_bytes, 0)
