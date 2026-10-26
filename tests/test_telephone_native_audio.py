"""Offline native/gated telephone transport and authority checks; never dial."""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
from fastapi import HTTPException

from voice_harness.adapters import AudioOut, Interrupted, TranscriptFinal, TurnEnd
from voice_harness.resample import resample
from voice_harness.telephone_api import TelephoneCalls, TelephoneRequest
from voice_harness.telephone_setup import CallDetail, CallSetup
from voice_harness.telephony_demo import DelegateDemo, decode_mulaw, encode_mulaw


async def events(*items):
    for item in items:
        yield item


class NativeTelephoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.demos = []
        self.setup = CallSetup(goal="Ask for guidance about my card.", details=[
            CallDetail(label="Card", value="Barclays debit card"),
            CallDetail(label="PIN", value="193842", kind="passcode", delivery="dtmf", when_asked="PIN")])

    def demo(self, mode="native", **kwargs):
        adapter = AsyncMock()
        adapter.receive = lambda: events()
        adapter.open_session.return_value = {"provider": "offline"}
        asr = Mock()
        asr.available.return_value = (True, "")
        reviewer = Mock(model="offline-reviewer")
        reviewer.review = AsyncMock(return_value="allow")
        demo = DelegateDemo({}, Path(self.temp.name) / str(len(self.demos)), destination="01617683858",
                            caller_id="01483694454", route_id="770022", task="Demo",
                            call_setup=self.setup, brief=self.setup.brief("lost-wallet"), scenario="lost-wallet",
                            audio_mode=mode, adapter=adapter, control=AsyncMock(), asr=asr,
                            reviewer=reviewer, synthesise=Mock(), **kwargs)
        demo.active = True
        demo.socket = AsyncMock()
        demo.stream_sid = "MZtest"
        demo.sent_values.add("test_route")
        self.demos.append(demo)
        return demo

    async def asyncTearDown(self):
        for demo in self.demos:
            for task in list(demo.tasks):
                task.cancel()
            await asyncio.gather(*demo.tasks, return_exceptions=True)

    def audio(self, item="output", rate=24000, samples=2400):
        pcm = (np.sin(np.arange(samples) * 0.1) * 8000).astype("<i2").tobytes()
        return AudioOut(item_id=item, pcm=pcm, sample_rate=rate, sample_count=samples)

    async def test_gated_loop_never_forwards_native_audio(self):
        demo = self.demo("gated")
        demo.adapter.receive = lambda: events(self.audio(), TranscriptFinal(role="assistant", text="Hello"))
        await demo.provider_loop()
        demo.socket.send_json.assert_not_called()
        demo.synthesise.assert_not_called()
        self.assertEqual(demo.native_queue.qsize(), 0)
        ledger = (demo.root / "events.jsonl").read_text()
        self.assertIn("speech.model_withheld_transcript", ledger)
        self.assertNotIn("speech.native_audio_sent", ledger)

    async def test_native_provider_pcm_is_streamed_without_synthesis_or_review(self):
        demo = self.demo()
        event = self.audio()
        demo.adapter.receive = lambda: events(event, TranscriptFinal(role="assistant", text="Hello"), TurnEnd())
        await demo.provider_loop()
        sender = demo.spawn(demo.native_sender())
        for _ in range(100):
            if not demo.native_queue.qsize():
                break
            await asyncio.sleep(0.005)
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        sent = b"".join(base64.b64decode(call.args[0]["media"]["payload"])
                        for call in demo.socket.send_json.call_args_list)
        # Stateful conversion flushes to the same signal as a single whole resample.
        self.assertEqual(sent, encode_mulaw(resample(event.pcm, 24000, 8000)))
        self.assertEqual(len(sent), 800)
        demo.synthesise.assert_not_called()
        demo.reviewer.review.assert_not_called()
        ledger = (demo.root / "events.jsonl").read_text()
        self.assertIn("native_unassessed_no_preplayback_approval", ledger)
        self.assertIn('"preplayback_approved":false', ledger.replace(" ", ""))
        self.assertEqual(demo.native_transcripts, ["Hello"])
        self.assertFalse(demo.dialogue.discussed)

    async def test_interruption_clears_pending_audio_and_late_chunks(self):
        demo = self.demo()
        demo.queue_native_audio(self.audio())
        self.assertGreater(demo.native_queue.qsize(), 0)
        demo.adapter.receive = lambda: events(Interrupted(item_id="output"), self.audio())
        await demo.provider_loop()
        self.assertTrue(demo.native_queue.empty())
        self.assertIsNone(demo.native_resampler)
        self.assertEqual(demo.socket.send_json.call_args.args[0]["event"], "clear")
        demo.queue_native_audio(self.audio(item="new-output"))
        self.assertGreater(demo.native_queue.qsize(), 0)

    async def test_native_audio_is_not_sent_before_routing_or_after_stop(self):
        demo = self.demo()
        demo.sent_values.clear()
        demo.queue_native_audio(self.audio())
        self.assertTrue(demo.native_queue.empty())
        demo.sent_values.add("test_route")
        demo.done.set()
        demo.queue_native_audio(self.audio())
        self.assertTrue(demo.native_queue.empty())

    async def test_old_generation_queue_is_discarded_and_overflow_stops(self):
        demo = self.demo()
        demo.queue_native_audio(self.audio())
        demo.generation += 1
        sender = demo.spawn(demo.native_sender())
        await asyncio.sleep(0.01)
        demo.socket.send_json.assert_not_called()
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        demo.enqueue_native_pcm(bytes(320 * (demo.native_queue.maxsize + 1)))
        self.assertTrue(demo.done.is_set())
        self.assertEqual(demo.reason, "NATIVE_AUDIO_BUFFER_LIMIT")

    async def test_native_prepare_advertises_auto_speech_and_keeps_private_tools(self):
        demo = self.demo()
        demo.prepare_phrase = AsyncMock()
        await demo.prepare()
        config = demo.adapter.open_session.call_args.args[0]
        self.assertEqual(config["tool_choice"], "auto")
        tools = {tool["name"] for tool in config["tools"]}
        self.assertIn("phone_provide_detail", tools)
        self.assertIn("phone_send_dtmf", tools)
        self.assertNotIn("phone_respond", tools)
        self.assertNotIn("phone_speak", tools)
        self.assertNotIn("193842", json.dumps(config))
        self.assertIn("without approval", config["instructions"])
        self.assertNotIn("Model audio is withheld", config["instructions"])
        demo.prepare_phrase.assert_not_called()
        demo.reviewer.review.assert_not_called()
        for name, args in [("phone_respond", {}), ("phone_send_dtmf", {"value_ref": "arbitrary"}),
                           ("phone_provide_detail", {"detail_ref": "unknown"}), ("transfer_money", {})]:
            with self.subTest(tool=name), self.assertRaises(ValueError):
                await demo.execute(name, args)

    async def test_native_result_still_requires_remote_quote_and_review(self):
        demo = self.demo()
        demo.prompt_events = ["p1"]
        demo.prompt_text["p1"] = "Please contact the card team."
        demo.native_transcripts = ["What should I do about my Barclays card?"]
        with self.assertRaisesRegex(ValueError, "RESULT_QUOTE_NOT_HEARD"):
            await demo.execute("report_result", {"summary": "Done", "source_quote": "invented"})
        demo.reviewer.review.return_value = "premature_result"
        with self.assertRaisesRegex(ValueError, "DIALOGUE_REVIEW_DENIED"):
            await demo.execute("report_result", {"summary": "Done", "source_quote": "Please contact the card team."})
        payload = demo.reviewer.review.call_args.args[0]
        self.assertFalse(payload["native_transcripts_prove_audibility"])
        self.assertEqual(payload["native_speech_transcripts_unassessed"], demo.native_transcripts)

    async def test_native_api_flag_is_passed_and_static_native_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            calls = TelephoneCalls(root)
            request = TelephoneRequest(destination="01617683858", caller_id="01483694454", route_id="770022", audio_mode="native")
            process = Mock(returncode=None)
            process.stdin.drain = AsyncMock()
            with patch.object(calls, "configuration", return_value=({"OPENAI_API_KEY": "offline"}, "/test/tunnel", None, [])), patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
                await calls.start(request)
                args = spawn.call_args.args
                self.assertEqual(args[args.index("--audio-mode") + 1], "native")
            with self.assertRaisesRegex(HTTPException, "NATIVE_AUDIO_REQUIRES_AI_RESPONSES"):
                await calls.start(request.model_copy(update={"response_mode": "static"}))


    async def test_gated_and_native_summaries_identify_speech_assurance(self):
        for mode in ("gated", "native"):
            with self.subTest(mode=mode):
                demo = self.demo(mode)
                demo.reason = "OFFLINE_TEST"
                await demo.finish()
                summary = json.loads((demo.root / "summary.json").read_text())
                self.assertEqual(summary["audio_mode"], mode)
                self.assertEqual(summary["speech_authority_enforced_before_playback"], mode == "gated")
                self.assertEqual(summary["native_speech_assessment"], "not_performed" if mode == "native" else "not_applicable")
                self.assertEqual(summary["platform_outcome"], "not_independently_verified_no_oracle")

    async def test_default_is_gated_and_invalid_audio_modes_are_rejected(self):
        request = TelephoneRequest(destination="01617683858", caller_id="01483694454", route_id="770022")
        self.assertEqual(request.audio_mode, "gated")
        with self.assertRaisesRegex(ValueError, "INVALID_AUDIO_MODE"):
            self.demo("invented")
        with self.assertRaisesRegex(ValueError, "NATIVE_AUDIO_REQUIRES_AI_RESPONSES"):
            DelegateDemo({}, Path(self.temp.name) / "invalid", destination="01617683858", caller_id="01483694454",
                         route_id="770022", task="Demo", audio_mode="native")


    async def test_native_conversion_preserves_continuity_across_provider_chunks(self):
        demo = self.demo()
        event = self.audio(rate=16000, samples=1600)
        for offset in range(0, len(event.pcm), 200):
            data = event.pcm[offset:offset + 200]
            demo.queue_native_audio(AudioOut(item_id="split", pcm=data, sample_rate=16000, sample_count=len(data) // 2))
        demo.finish_native_audio()
        chunks = []
        while not demo.native_queue.empty():
            chunks.append(demo.native_queue.get_nowait()[3])
        encoded = b"".join(chunks)
        expected = encode_mulaw(resample(event.pcm, 16000, 8000))
        self.assertEqual(len(encoded), len(expected))
        # soxr streaming integer quantisation can cross a mu-law bin boundary.
        actual_samples = np.frombuffer(decode_mulaw(encoded), dtype="<i2").astype(int)
        expected_samples = np.frombuffer(decode_mulaw(expected), dtype="<i2").astype(int)
        self.assertLessEqual(int(np.max(np.abs(actual_samples - expected_samples))), 256)


    async def test_eight_second_provider_burst_drains_at_playback_rate(self):
        demo = self.demo()
        event = self.audio(item="eight-second-burst", samples=24000 * 8)
        demo.queue_native_audio(event)
        demo.finish_native_audio()
        self.assertFalse(demo.done.is_set())
        burst_frames = demo.native_queue.qsize()
        self.assertIn(burst_frames, {400, 401})
        started = asyncio.get_running_loop().time()
        sender = demo.spawn(demo.native_sender())
        async with asyncio.timeout(20):
            while len(demo.socket.send_json.call_args_list) < burst_frames:
                await asyncio.sleep(0.02)
        elapsed = asyncio.get_running_loop().time() - started
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        self.assertFalse(demo.done.is_set())
        self.assertGreaterEqual(elapsed, 7.9)
        sent = b"".join(base64.b64decode(call.args[0]["media"]["payload"])
                        for call in demo.socket.send_json.call_args_list)
        self.assertEqual(len(sent), 8 * 8000)
        self.assertEqual(sent, encode_mulaw(resample(event.pcm, 24000, 8000)))

    async def test_provider_audio_item_id_normalisation_and_missing_id_rejection(self):
        from voice_harness.adapters.elevenlabs_voice import ElevenLabsVoiceAdapter
        from voice_harness.adapters.google_gemini_live import GeminiLiveAdapter
        from voice_harness.adapters.nova_sonic import NovaSonicAdapter
        from voice_harness.adapters.openai_realtime import OpenAIRealtimeAdapter
        from voice_harness.adapters.xai_voice import XaiVoiceAdapter

        pcm = bytes(4800)
        encoded = base64.b64encode(pcm).decode()
        normalised = []
        for adapter in (OpenAIRealtimeAdapter(api_key="offline"), XaiVoiceAdapter(api_key="offline")):
            adapter._handle({"type": "response.output_audio.delta", "item_id": "provider-item", "delta": encoded})
            normalised.append(adapter._queue.get_nowait())
        nova = NovaSonicAdapter()
        normalised.extend(nova._translate({"audioOutput": {"contentId": "nova-content", "completionId": "nova-turn", "content": encoded}}))
        gemini = GeminiLiveAdapter()
        gemini._handle({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"data": encoded}}]}}})
        eleven = ElevenLabsVoiceAdapter(api_key="offline")
        eleven._handle("audio", {"audio_event": {"audio_base_64": encoded, "event_id": 1}})
        for adapter in (gemini, eleven):
            while not adapter._queue.empty():
                item = adapter._queue.get_nowait()
                if isinstance(item, AudioOut):
                    normalised.append(item)
        self.assertEqual([item.item_id for item in normalised],
                         ["provider-item", "provider-item", "nova-content", "turn_1", "turn_1"])
        for item in normalised:
            with self.subTest(item_id=item.item_id):
                demo = self.demo()
                demo.queue_native_audio(item)
                demo.finish_native_audio()
                self.assertFalse(demo.done.is_set())
                self.assertGreater(demo.native_queue.qsize(), 0)
        # The adapter contract permits absent IDs; native telephone cannot safely retire them.
        missing = nova._translate({"audioOutput": {"content": encoded}})[0]
        self.assertIsNone(missing.item_id)
        demo = self.demo()
        demo.queue_native_audio(missing)
        self.assertTrue(demo.done.is_set())
        self.assertEqual(demo.reason, "INVALID_NATIVE_AUDIO")
        self.assertTrue(demo.native_queue.empty())
