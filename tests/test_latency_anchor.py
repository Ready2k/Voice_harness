"""First-audio latency is measured on the same footing whatever the turn-control mode (bias review finding 1)."""

import asyncio
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path

from voice_harness import bakeoff
from voice_harness import bakeoff_metrics as bm
from voice_harness.adapters import AudioOut, TranscriptDelta, TurnEnd
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.live_providers import (
    CALLER_TRAIL_MS,
    COMMIT_DELAY_MS,
    commit_delay_ms,
    configure_transport,
)
from voice_harness.live_scenario import RATE, LiveScenarioRunner
from voice_harness.storage import Database

SPEECH_MS, TRAIL_MS, DELAY_MS, RESPONSE_MS = 100, 400, 100, 150
TOLERANCE_MS = 60
AUDIO = "response.output_audio.delta"


def clip():
    speech = struct.pack(f"<{24 * SPEECH_MS}h", *([8000, -8000] * (12 * SPEECH_MS)))
    return speech + bytes(RATE * 2 * TRAIL_MS // 1000)


class FixedDelayAdapter:
    """Answers RESPONSE_MS after its turn ends: on request_turn (explicit) or after DELAY_MS of heard silence (VAD)."""

    def __init__(self, vad):
        self.vad, self.silence_ms, self.heard_speech, self.triggered = vad, 0, False, False
        self.queue, self.native_sink = asyncio.Queue(), None

    async def send_audio(self, data, metadata):
        if not self.vad:
            return
        if any(data):
            self.heard_speech, self.silence_ms, self.triggered = True, 0, False
            return
        self.silence_ms += len(data) // 2 * 1000 // RATE
        if self.heard_speech and not self.triggered and self.silence_ms >= DELAY_MS:
            self.triggered = True  # this chunk ends DELAY_MS of silence once it has played
            self._respond((len(data) // 2 * 1000 / RATE + RESPONSE_MS) / 1000)

    async def request_turn(self):
        if not self.vad:
            self._respond(RESPONSE_MS / 1000)

    def _respond(self, after_s):
        asyncio.get_running_loop().call_later(after_s, self._speak)

    def _speak(self):
        self.native_sink("fake", AUDIO, {"at": time.monotonic()})
        for event in (TranscriptDelta(role="assistant", text="Hello.", ts_ms=1),
                      AudioOut(pcm=bytes(960), sample_rate=RATE, sample_count=480, turn_id="t", ts_ms=2),
                      TurnEnd(turn_id="t", ts_ms=3)):
            self.queue.put_nowait(event)

    async def submit_tool_result(self, call_id, result):
        return {"delivered": True}

    async def receive(self):
        while True:
            yield await self.queue.get()


class EndpointingParity(unittest.TestCase):
    def measure(self, vad, transport):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        engine = Engine(database)
        run = engine.create(RunConfig().model_dump())
        runner = LiveScenarioRunner(engine, run, FixedDelayAdapter(vad), IndependentAssessor(engine, ScriptedAsr(queue=["Hello."] * 4)),
                                    "happy-policy-question-001", caller_audio=lambda text: clip(), turn_timeout_s=10,
                                    provider_session={"harness_transport": transport}, trail_ms=TRAIL_MS)
        asyncio.run(runner.run())
        events = database.events(run)
        turns = bakeoff.first_audio_turns("openai-realtime-live", events)
        self.assertEqual(len(turns), 1)
        return turns[0], next(e["content"] for e in events if e["type"] == "input.caller_audio")

    def test_explicit_commit_and_provider_vad_measure_the_same_first_audio(self):
        explicit, caller = self.measure(False, {"turn_commit": "explicit", "commit_delay_ms": DELAY_MS})
        vad, vad_caller = self.measure(True, {"turn_commit": "provider_vad", "commit_delay_ms": None})
        self.assertEqual((explicit["anchor"], explicit["turn_commit"]), ("speech_end", "explicit"))
        self.assertEqual((vad["anchor"], vad["turn_commit"]), ("speech_end", "provider_vad"))
        self.assertLess(abs(explicit["first_audio_ms"] - vad["first_audio_ms"]), TOLERANCE_MS)
        self.assertLess(abs(explicit["first_audio_ms"] - (DELAY_MS + RESPONSE_MS)), TOLERANCE_MS)
        # The commit follows the end of speech by the emulated delay; the rest of the silence is not sent.
        self.assertLess(abs(caller["turn_signal_ms"] - caller["speech_ended_ms"] - DELAY_MS), TOLERANCE_MS)
        self.assertLess(caller["streamed_byte_count"], caller["byte_count"])
        self.assertEqual(caller["commit_delay_ms"], DELAY_MS)
        self.assertIsNone(vad_caller["turn_signal_ms"])
        self.assertEqual(vad_caller["streamed_byte_count"], vad_caller["byte_count"])

    def test_committing_after_the_whole_trailing_silence_would_fail_parity(self):
        # The pre-fix runner: stream the whole clip, then commit; the trailing silence is charged as latency.
        legacy, _ = self.measure(False, {"turn_commit": "explicit"})
        vad, _ = self.measure(True, {"turn_commit": "provider_vad"})
        self.assertGreater(legacy["first_audio_ms"] - vad["first_audio_ms"], TRAIL_MS - DELAY_MS - TOLERANCE_MS)


class SlowSendAdapter(FixedDelayAdapter):
    """Each send costs 5 ms, as a real websocket write can: a sleep-after-send loop would run 25% slow."""

    async def send_audio(self, data, metadata):
        await asyncio.sleep(0.005)
        await super().send_audio(data, metadata)


class StreamPacing(unittest.TestCase):
    def test_caller_audio_is_deadline_paced_and_caller_wait_stays_a_label(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "t.sqlite"))
        engine = Engine(database)
        run = engine.create(RunConfig().model_dump())
        long_clip = struct.pack(f"<{24 * 1000}h", *([8000, -8000] * 12000)) + bytes(RATE * 2 * TRAIL_MS // 1000)
        runner = LiveScenarioRunner(engine, run, SlowSendAdapter(False), IndependentAssessor(engine, ScriptedAsr(queue=["Hello."] * 4)),
                                    "happy-policy-question-001", caller_audio=lambda text: long_clip, turn_timeout_s=10,
                                    provider_session={"harness_transport": {"turn_commit": "explicit", "commit_delay_ms": DELAY_MS}},
                                    trail_ms=TRAIL_MS)
        asyncio.run(runner.run())
        caller = next(e["content"] for e in database.events(run) if e["type"] == "input.caller_audio")
        # 1000 ms of speech is delivered in about 1000 ms, not 1250 ms.
        self.assertLess(abs(caller["speech_ended_ms"] - caller["stream_started_ms"] - 1000), TOLERANCE_MS)
        self.assertEqual(caller["caller_wait"], "first_turn")


class LegacyAnchors(unittest.TestCase):
    def ledger(self, commit, **turn):
        return [{"type": "run.live_binding", "content": {"provider": {"harness_transport": {"turn_commit": commit}}}},
                {"type": "input.caller_audio", "content": {"stream_started_ms": 1000, "stream_ended_ms": 5000, **turn}},
                {"type": "adapter.native_event", "content": {"native_type": AUDIO, "received_ms": 5600, "payload_bytes": 9}}]

    def test_legacy_explicit_runs_anchor_at_the_commit(self):
        turn = bakeoff.first_audio_turns("xai-voice-live", self.ledger("explicit"))[0]
        self.assertEqual((turn["anchor"], turn["turn_commit"], turn["first_audio_ms"], turn["commit_delay_ms"]),
                         ("legacy_commit", "explicit", 600, 0))

    def test_legacy_provider_vad_runs_keep_the_trailing_silence_estimate(self):
        turn = bakeoff.first_audio_turns("nova-sonic-live", self.ledger("provider_vad"))[0]
        self.assertEqual((turn["anchor"], turn["turn_commit"], turn["first_audio_ms"]),
                         ("legacy_speech_end", "provider_vad", 600 + CALLER_TRAIL_MS))

    def test_runs_without_a_transport_profile_are_treated_as_provider_vad(self):
        events = self.ledger("provider_vad")[1:]
        self.assertEqual(bm.speech_anchor(events[0]["content"], bm.run_turn_commit(events), CALLER_TRAIL_MS)[:3],
                         (5000 - CALLER_TRAIL_MS, "legacy_speech_end", None))

    def test_recorded_speech_end_wins(self):
        turn = bakeoff.first_audio_turns("xai-voice-live", self.ledger(
            "explicit", speech_ended_ms=4000, turn_commit="explicit", commit_delay_ms=500))[0]
        self.assertEqual((turn["anchor"], turn["first_audio_ms"], turn["commit_delay_ms"]), ("speech_end", 1600, 500))

    def test_latency_detail_uses_the_same_anchor(self):
        events = [{**e, "sequence": i} for i, e in enumerate(self.ledger("explicit"))]
        detail = bm.observe_metrics("xai-voice-live", {"turns": [{}]}, events, trail_ms=CALLER_TRAIL_MS,
                                    is_audio=lambda provider, n: n["native_type"] == AUDIO)["turns"][0]
        self.assertEqual((detail["anchor"], detail["turn_commit"], detail["content_audio_ms"]), ("legacy_commit", "explicit", 600))


class TransportProfile(unittest.TestCase):
    def test_explicit_commits_record_the_common_delay_and_vad_records_none(self):
        for provider, delay in (("xai-voice-live", COMMIT_DELAY_MS), ("nova-sonic-live", None), ("openai-live", None)):
            profile = configure_transport(provider, {}, "fixed")
            self.assertEqual((profile["profile_version"], profile["commit_delay_ms"]), ("provider-transport-v3", delay))
        self.assertIsNone(configure_transport("gemini-live", {}, "human_microphone")["commit_delay_ms"])

    def test_delay_override_is_clamped_to_the_trailing_silence(self):
        self.assertEqual(commit_delay_ms({"HARNESS_COMMIT_DELAY_MS": "700"}), 700)
        self.assertEqual(commit_delay_ms({"HARNESS_COMMIT_DELAY_MS": "9000"}), CALLER_TRAIL_MS)
        self.assertEqual(commit_delay_ms({"HARNESS_COMMIT_DELAY_MS": "-5"}), 0)
        self.assertEqual(commit_delay_ms({"HARNESS_COMMIT_DELAY_MS": "x"}), COMMIT_DELAY_MS)


if __name__ == "__main__":
    unittest.main()
