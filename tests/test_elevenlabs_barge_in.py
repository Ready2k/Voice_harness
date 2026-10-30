"""A caller_interrupts run must still process the provider's answer to the barging turn (ElevenLabs, 29-30 Oct 2026).

In the 29 Oct standard-prompt bake-off all three ElevenLabs runs of identity-fault-caller-interrupts-001 ended right after the
caller's second turn: ElevenLabs issued get_account_summary but the run completed first and the call was never executed
(FACT-01 untestable, no reply audio). Traced live on 30 Oct (fails ~2 runs in 3), the cause is two TurnEnds for the one cut reply.
The adapter closes a turn on its idle timer, a late audio chunk of the same cut reply opens a new turn, and the server's
``interruption`` then ends that one too. The runner counts one cancelled end per barge-in (``_cancelled_ends``), so the second
end reads as an empty reply (``no_audio``), the turn settles and returns with the real answer still queued behind it.
This replays that order, as the adapter normalised it, through the real runner and gateway.

The adapter no longer produces it (``BARGE_IDLE_S``: after a barge-in attempt the server's interruption, not a pause, closes the
cut reply; see tests/test_elevenlabs_adapter.py). The runner is also robust to it: an empty turn end for a turn the server
interrupted is that cut reply's own end, so the turn keeps waiting for the real answer.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from test_turn_taking import GAP_MS, RATE, TEXT, QueueAdapter, tone

from voice_harness.adapters import (
    AudioOut,
    CapabilityUnsupported,
    Interrupted,
    ToolCall,
    TranscriptDelta,
    TranscriptFinal,
    TurnEnd,
    TurnStart,
)
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.live_scenario import LiveScenarioRunner
from voice_harness.storage import Database

VERIFY = {"challenge_id": "challenge_001", "answer": "blue"}
SUMMARY = {"account_id": "acct_1001"}
LATE_CHUNK = 2  # the barging turn's second send_audio chunk: after the interruption, before the caller has finished


def speech(turn, ms):
    pcm = bytes(RATE * 2 * ms // 1000)
    return [AudioOut(pcm=pcm, sample_rate=RATE, sample_count=len(pcm) // 2, item_id=f"item_{turn}", turn_id=turn, ts_ms=2),
            TranscriptDelta(role="assistant", text=TEXT, ts_ms=3)]


class ElevenLabsOrder(QueueAdapter):
    """ElevenLabs' event order: tool call, a spoken reply cut by a server interruption (two turn ends), then a second tool call."""

    def __init__(self):
        super().__init__(
            script=[[TurnStart(turn_id="e1", ts_ms=1), ToolCall(call_id="c1", name="verify_identity", arguments=VERIFY, ts_ms=2),
                     TurnEnd(turn_id="e1", ts_ms=3)],
                    [TurnEnd(turn_id="e3", ts_ms=3)]],  # the idle timer closes the second tool-calling response later
            # while the caller speaks over the reply: the adapter's idle timer closes the cut reply, a late chunk of it opens a
            # new turn, and the server's interruption ends that turn as well
            on_audio={1: [*speech("e2", 40), TurnEnd(turn_id="e2", ts_ms=4), TurnStart(turn_id="e2b", ts_ms=5), *speech("e2b", 60),
                          Interrupted(item_id="item_e2b", turn_id="e2b", ts_ms=6), TurnEnd(turn_id="e2b", ts_ms=7)]})
        self.results, self.chunks = [], {}

    async def send_audio(self, data, metadata):
        await super().send_audio(data, metadata)
        self.chunks[self.turn] = self.chunks.get(self.turn, 0) + 1
        if self.turn == 1 and self.chunks[1] == LATE_CHUNK:  # ElevenLabs answered during the caller's trailing silence
            for event in (TranscriptFinal(role="user", item_id="user_2", text="Sorry, to be clear, the answer is blue.", ts_ms=1),
                          TurnStart(turn_id="e3", ts_ms=1), ToolCall(call_id="c2", name="get_account_summary", arguments=SUMMARY, ts_ms=2)):
                self.queue.put_nowait(event)

    async def interrupt(self, item_id, audio_end_ms):
        raise CapabilityUnsupported("no client cancel or truncate")  # as ElevenLabsVoiceAdapter.interrupt

    async def submit_tool_result(self, call_id, result):
        self.results.append(call_id)
        follow_up = {"c1": [TurnStart(turn_id="e2", ts_ms=1), *speech("e2", 2000)],  # long reply: the barge-in target
                     "c2": [TurnStart(turn_id="e4", ts_ms=1), *speech("e4", 300), TurnEnd(turn_id="e4", ts_ms=4)]}[call_id]
        for event in follow_up:
            self.queue.put_nowait(event)
        return {"delivered": True}


class BargeInAnswerIsProcessed(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        self.engine = Engine(self.database)
        self.run_id = self.engine.create(RunConfig().model_dump())

    def events(self, kind):
        return [e["content"] for e in self.database.events(self.run_id) if e["type"] == kind]

    def test_the_answer_to_the_barging_turn_is_not_dropped(self):
        adapter = ElevenLabsOrder()
        runner = LiveScenarioRunner(self.engine, self.run_id, adapter, IndependentAssessor(self.engine, ScriptedAsr(queue=[TEXT] * 8)),
                                    "identity-fault-caller-interrupts-001", caller_audio=lambda text: tone(), turn_timeout_s=5,
                                    provider_session={"harness_transport": {"caller_turn_gap_ms": GAP_MS}})
        summary = asyncio.run(runner.run())
        requested = [e["tool"] for e in self.events("tool.requested")]
        self.assertEqual(requested, ["verify_identity", "get_account_summary"])
        self.assertEqual(adapter.results, ["c1", "c2"])
        self.assertEqual(summary["turns"][1]["strict_speech_approval"], "approved")

if __name__ == "__main__":
    unittest.main()
