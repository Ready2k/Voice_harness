"""Fixed callers wait for the previous reply to play out; stale provider interruptions never cancel a new reply."""

import asyncio
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path

from voice_harness.adapters import AudioOut, Interrupted, TranscriptDelta, TurnEnd, TurnStart
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.live_scenario import RATE, LiveScenarioRunner
from voice_harness.storage import Database

REPLY_MS, GAP_MS, TEXT = 400, 50, "Please verify your identity."


def tone(ms=60):
    return struct.pack(f"<{24 * ms}h", *([8000, -8000] * (12 * ms)))


def reply(n, ms=REPLY_MS):
    pcm = bytes(RATE * 2 * ms // 1000)
    return [TurnStart(turn_id=f"r{n}", ts_ms=1),
            AudioOut(pcm=pcm, sample_rate=RATE, sample_count=len(pcm) // 2, item_id=f"item_{n}", turn_id=f"r{n}", ts_ms=2),
            TranscriptDelta(role="assistant", text=TEXT, ts_ms=3)]


class QueueAdapter:
    """Answers each committed caller turn from ``script[turn]``; ``on_audio[turn]`` is queued at that turn's first chunk."""

    def __init__(self, script, on_audio=None, on_interrupt=()):
        self.script, self.on_audio, self.on_interrupt = script, on_audio or {}, list(on_interrupt)
        self.queue, self.native_sink, self.turn, self.first_chunk, self.interrupts = asyncio.Queue(), None, 0, {}, []

    async def send_audio(self, data, metadata):
        if self.turn not in self.first_chunk:
            self.first_chunk[self.turn] = time.monotonic() * 1000
            for event in self.on_audio.get(self.turn, ()):
                self.queue.put_nowait(event)

    async def request_turn(self):
        for event in self.script[self.turn]:
            self.queue.put_nowait(event)
        self.turn += 1

    async def interrupt(self, item_id, audio_end_ms):
        self.interrupts.append(item_id)
        for event in self.on_interrupt:
            self.queue.put_nowait(event)
        return {"generation_cancelled": "confirmed", "history_truncation": "confirmed"}

    async def submit_tool_result(self, call_id, result):
        return {"delivered": True}

    async def receive(self):
        while True:
            yield await self.queue.get()


class TurnTaking(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        self.engine = Engine(self.database)
        self.run_id = self.engine.create(RunConfig().model_dump())

    def run_scenario(self, adapter, scenario="happy-balance-clean-001", **kwargs):
        runner = LiveScenarioRunner(self.engine, self.run_id, adapter, IndependentAssessor(self.engine, ScriptedAsr(queue=[TEXT] * 8)),
                                    scenario, caller_audio=lambda text: tone(), turn_timeout_s=5,
                                    provider_session={"harness_transport": {"caller_turn_gap_ms": GAP_MS}}, **kwargs)
        return asyncio.run(runner.run())

    def events(self, kind):
        return [e["content"] for e in self.database.events(self.run_id) if e["type"] == kind]

    def test_next_caller_turn_waits_for_the_reply_to_finish_playing(self):
        adapter = QueueAdapter([[*reply(0), TurnEnd(turn_id="r0", ts_ms=4)], [*reply(1), TurnEnd(turn_id="r1", ts_ms=4)]])
        summary = self.run_scenario(adapter)
        self.assertEqual([t["strict_speech_approval"] for t in summary["turns"]], ["approved", "approved"])
        first, second = self.events("input.caller_audio")
        self.assertEqual((first["caller_wait"], first["caller_waited_ms"]), ("first_turn", 0))
        self.assertEqual((second["caller_wait"], second["caller_turn_gap_ms"]), ("after_reply", GAP_MS))
        self.assertGreater(second["caller_waited_ms"], 0)
        # The reply was received at once; turn 1 began only after its 400 ms played out, plus the gap.
        self.assertGreaterEqual(adapter.first_chunk[1], second["reply_playback_end_ms"] + GAP_MS - 1)
        self.assertGreaterEqual(second["reply_playback_end_ms"] - first["stream_ended_ms"], REPLY_MS - 50)
        self.assertEqual(self.events("session.interrupted"), [])

    def test_a_caller_interrupts_target_turn_does_not_wait(self):
        adapter = QueueAdapter([reply(0, 2000), [*reply(1), TurnEnd(turn_id="r1", ts_ms=4)]],
                               on_interrupt=[Interrupted(item_id="item_0", turn_id="r0", ts_ms=5), TurnEnd(turn_id="r0", ts_ms=6)])
        summary = self.run_scenario(adapter, "identity-fault-caller-interrupts-001")
        _, second = self.events("input.caller_audio")
        self.assertEqual((second["caller_wait"], second["caller_waited_ms"]), ("barge_in", 0))
        self.assertLess(adapter.first_chunk[1] - adapter.first_chunk[0], 2000)  # spoke over the 2 s reply
        # Legitimate client barge-in still cancels: interrupt() was called and the engine recorded the interruption.
        self.assertEqual(adapter.interrupts, ["item_0"])
        self.assertEqual(summary["turns"][0]["strict_speech_approval"], "barge_in_not_assessed")
        self.assertEqual(summary["turns"][1]["strict_speech_approval"], "approved")
        self.assertGreaterEqual(len(self.events("session.interrupted")), 1)
        self.assertEqual(self.events("interruption.stale_ignored"), [])

    def test_provider_vad_barge_in_on_the_target_turn_is_applied(self):
        # interrupt_mode "vad": the runner does not cancel; the provider reports the cut while turn 1 is spoken.
        adapter = QueueAdapter([reply(0, 2000), [*reply(1), TurnEnd(turn_id="r1", ts_ms=4)]],
                               on_audio={1: [Interrupted(item_id="item_0", turn_id="r0", ts_ms=5), TurnEnd(turn_id="r0", ts_ms=6)]})
        summary = self.run_scenario(adapter, "identity-fault-caller-interrupts-001", interrupt_mode="vad")
        self.assertEqual(adapter.interrupts, [])
        self.assertEqual(summary["interruptions"], 1)
        self.assertEqual(len(self.events("session.interrupted")), 1)
        self.assertEqual(summary["turns"][1]["strict_speech_approval"], "approved")

    def test_stale_interruption_does_not_cancel_the_next_reply(self):
        # xAI 26 Oct: speech over the finished reply, then on commit a cancelled response.done for item_0 (Interrupted
        # + TurnEnd with no response open) just before the confirmation turn's real response.
        stale = [Interrupted(item_id="item_0", turn_id=None, ts_ms=5), TurnEnd(turn_id=None, ts_ms=6)]
        adapter = QueueAdapter([[*reply(0), TurnEnd(turn_id="r0", ts_ms=4)], [*stale, *reply(1), TurnEnd(turn_id="r1", ts_ms=7)]],
                               on_audio={1: [Interrupted(item_id=None, turn_id=None, ts_ms=4)]})
        summary = self.run_scenario(adapter)
        self.assertEqual([t["strict_speech_approval"] for t in summary["turns"]], ["approved", "approved"])
        self.assertEqual(adapter.interrupts, [])
        self.assertEqual(self.events("session.interrupted"), [])
        self.assertEqual(summary["interruptions"], 0)
        ignored = self.events("interruption.stale_ignored")
        self.assertEqual([(i["turn"], i["reason"], i["item_id"]) for i in ignored],
                         [(1, "NO_RESPONSE_OPEN", None), (1, "RESPONSE_ALREADY_ENDED", "item_0")])
        self.assertEqual(len(self.events("audio.output")), 2)

    def test_interruption_of_the_open_reply_still_applies(self):
        # A reply still open when the provider reports the cut (then answers again) is a real interruption.
        cut = [*reply(0), Interrupted(item_id="item_0", turn_id="r0", ts_ms=4), TurnEnd(turn_id="r0", ts_ms=5),
               *reply(9, 100), TurnEnd(turn_id="r9", ts_ms=6)]
        adapter = QueueAdapter([cut, [*reply(1), TurnEnd(turn_id="r1", ts_ms=4)]])
        summary = self.run_scenario(adapter)
        self.assertEqual(summary["interruptions"], 1)
        self.assertEqual(len(self.events("session.interrupted")), 1)
        self.assertEqual(self.events("interruption.stale_ignored"), [])
        # The cut reply stopped playing when it was cut, so its full 400 ms is not waited out.
        _, second = self.events("input.caller_audio")
        self.assertLess(second["caller_waited_ms"], REPLY_MS)

if __name__ == "__main__":
    unittest.main()
