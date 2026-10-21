"""Offline containment and protocol checks; these cannot prove IVR compatibility."""
import base64
import asyncio
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import httpx
import numpy as np
from fastapi.testclient import TestClient

from voice_harness.telephony_demo import (
    DelegateDemo, TwilioControl, TwilioError, decode_mulaw, encode_mulaw,
    normalise_number, signature_valid, verify_output, words,
)
from voice_harness.adapters.openai_realtime import OpenAIRealtimeAdapter
from voice_harness.caller_voice import CallerVoiceUnavailable


class ProtocolTests(unittest.TestCase):
    def test_uk_numbers_drop_domestic_zero(self):
        for value in ["0161 768 3858", "+44 (0)161 7683858", "0044 01617683858", "+441617683858"]:
            self.assertEqual(normalise_number(value), "+441617683858")
        for value in ["https://example.com", "+44<bad>", "123"]:
            with self.assertRaises(ValueError):
                normalise_number(value)

    def test_signature_requires_exact_url_and_fields(self):
        # Twilio's published validation example, independently constructed message.
        url = "https://mycompany.com/myapp.php?foo=1&bar=2"
        params = {"CallSid": "CA1234567890ABCDE", "Caller": "+14158675310",
                  "Digits": "1234", "From": "+14158675310", "To": "+18005551212"}
        message = url + "CallSidCA1234567890ABCDECaller+14158675310Digits1234From+14158675310To+18005551212"
        signature = base64.b64encode(hmac.new(b"12345", message.encode(), hashlib.sha1).digest()).decode()
        self.assertTrue(signature_valid("12345", url, params, signature))
        self.assertFalse(signature_valid("12345", url.replace("https", "http"), params, signature))
        self.assertFalse(signature_valid("wrong", url, params, signature))
        self.assertFalse(signature_valid("12345", url, {**params, "Digits": "0000"}, signature))

    def test_codec_known_values_and_roundtrip(self):
        self.assertEqual(decode_mulaw(bytes([0xff, 0x7f, 0x00, 0x80])),
                         np.array([0, 0, -32124, 32124], dtype="<i2").tobytes())
        samples = np.array([-30000, -12000, -1000, 0, 1000, 12000, 30000], dtype="<i2")
        result = np.frombuffer(decode_mulaw(encode_mulaw(samples.tobytes())), dtype="<i2")
        self.assertTrue(np.all(np.abs(result.astype(int) - samples.astype(int)) < 600))

    def test_wallet_phrase_contractions_are_equivalent_but_negation_is_not(self):
        self.assertEqual(words("I've lost my wallet."), words("I have lost my wallet."))
        self.assertNotEqual(words("I've lost my wallet."), words("I have not lost my wallet."))
        self.assertEqual(words("It's my Premier card."), words("It is my Premier card."))


class DemoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = AsyncMock()
        self.control.account, self.control.auth_token = "ACtest", "secret"
        self.adapter = AsyncMock()
        self.demo = DelegateDemo({}, Path(self.temp.name) / "run", destination="01617683858",
            caller_id="01483694454", route_id="770022", task="Check a demo balance",
            control=self.control, adapter=self.adapter, asr=AsyncMock())
        self.demo.configure_public_url("https://example.com")
        self.demo.call_sid, self.demo.active = "CAtest", True

    def start(self):
        return {"accountSid": "ACtest", "callSid": "CAtest", "streamSid": "MZtest",
                "customParameters": {"binding": self.demo.binding, "generation": "0"},
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}

    def test_stream_binding_rejects_wrong_call_and_replay(self):
        start = self.start()
        with self.assertRaises(ValueError):
            self.demo.bind_stream({**start, "callSid": "CAother"})
        self.demo.bind_stream(start)
        with self.assertRaises(ValueError):
            self.demo.bind_stream(start)
        self.demo.generation = 1
        with self.assertRaises(ValueError):
            self.demo.bind_stream(start)

    def test_only_signed_ingress_exposed(self):
        with TestClient(self.demo.app()) as client:
            self.assertEqual(client.get("/docs").status_code, 404)
            self.assertEqual(client.get("/summary.json").status_code, 404)
            self.assertEqual(client.post("/status", data={"CallSid": "CAtest"}).status_code, 403)
            with self.assertRaises(Exception):
                with client.websocket_connect("/media"):
                    pass

    def test_signed_wss_handshake_binds_stream(self):
        self.demo.dial_accepted.set()
        signature = base64.b64encode(hmac.new(b"secret", b"wss://example.com/media", hashlib.sha1).digest()).decode()
        with TestClient(self.demo.app()) as client:
            with client.websocket_connect("/media", headers={"x-twilio-signature": signature}) as socket:
                socket.send_json({"event": "connected"})
                socket.send_json({"event": "start", "start": self.start()})
                socket.send_json({"event": "stop", "streamSid": "MZtest", "sequenceNumber": "1"})
                with self.assertRaises(Exception):
                    socket.receive_json()
        self.assertIn("stream.bound", [e.type for e in self.demo.ledger.events])

    async def test_unauthorised_tools_and_digits_never_reach_twilio(self):
        for name, args in [("send_money", {}), ("phone_send_dtmf", {"digits": "999999"}),
                           ("phone_speak", {"phrase": "redeem"})]:
            with self.assertRaises(ValueError):
                await self.demo.execute(name, args)
        self.control.request.assert_not_awaited()

    async def test_dtmf_reconnect_same_call_and_replay_denied(self):
        self.demo.socket = AsyncMock()
        self.demo.stream_sid = "MZold"
        self.demo.prompt_events = ["evt_prompt"]
        async def update(method, sid, data):
            self.assertEqual(sid, "CAtest")
            self.assertIn('digits="770022#"', data["Twiml"])
            self.assertIn('value="1"', data["Twiml"])
            self.demo.stream_ready.set()
            return {"status": "in-progress"}
        self.control.request.side_effect = update
        args = {"value_ref": "test_route", "prompt_event_id": "evt_prompt", "terminator": "#"}
        result = await self.demo.execute("phone_send_dtmf", args)
        self.assertTrue(result["reconnected"])
        with self.assertRaises(ValueError):
            await self.demo.execute("phone_send_dtmf", args)
        self.assertEqual(self.control.request.await_count, 1)

    async def test_retiring_stream_does_not_close_call_before_twiml_update(self):
        socket = AsyncMock()
        signature = base64.b64encode(hmac.new(b"secret", b"wss://example.com/media", hashlib.sha1).digest()).decode()
        socket.headers = {"x-twilio-signature": signature}
        queue = asyncio.Queue()
        socket.receive_json.side_effect = queue.get
        self.demo.dial_accepted.set()
        queue.put_nowait({"event": "start", "start": self.start()})
        task = asyncio.create_task(self.demo.media(socket))
        await asyncio.wait_for(self.demo.stream_ready.wait(), 1)
        self.demo.generation, self.demo.transitioning = 1, True
        queue.put_nowait({"event": "media", "streamSid": "MZtest", "sequenceNumber": "2",
                          "media": {"track": "inbound", "payload": "/w=="}})
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        socket.close.assert_not_awaited()
        queue.put_nowait({"event": "stop", "streamSid": "MZtest", "sequenceNumber": "3"})
        await asyncio.wait_for(task, 1)
        self.assertFalse(self.demo.done.is_set())

    async def test_uncertain_digit_send_consumes_authority_and_stops(self):
        self.demo.socket = AsyncMock()
        self.demo.prompt_events = ["evt_prompt"]
        self.control.request.side_effect = TwilioError("TWILIO_TRANSPORT_OUTCOME_UNCERTAIN")
        with self.assertRaises(TwilioError):
            await self.demo.execute("phone_send_dtmf", {"value_ref": "test_route", "prompt_event_id": "evt_prompt", "terminator": ""})
        self.assertTrue(self.demo.done.is_set())
        self.assertTrue(self.demo.dtmf_sent)
        self.assertEqual(self.control.request.await_count, 1)

    async def test_missing_subtest_stops_instead_of_guessing_or_speaking(self):
        self.demo.prompt_text["evt_subtest"] = "Please enter your sub-test number."
        await self.demo.respond_to_prompt("evt_subtest")
        self.assertEqual(self.demo.reason, "TASK_BLOCKED:missing_subtest")
        self.assertTrue(self.demo.done.is_set())
        self.control.request.assert_not_awaited()
        self.adapter.send_text.assert_not_awaited()

    async def test_acknowledgements_and_stale_partial_turns_do_not_trigger_replies(self):
        self.demo.prompt_events = ["evt_old", "evt_ack"]
        self.demo.prompt_text = {"evt_old": "Please", "evt_ack": "Got it."}
        await self.demo.respond_to_prompt("evt_old")
        await self.demo.respond_to_prompt("evt_ack")
        self.adapter.send_text.assert_not_awaited()

    async def test_response_waits_for_remote_speech_to_finish(self):
        import time
        self.demo.turn_quiet_seconds = 0.03
        self.demo.prompt_events = ["evt_question"]
        self.demo.prompt_text = {"evt_question": "Why are you calling?"}
        self.demo.last_voice_at = time.monotonic()
        self.demo.prompt_received_at["evt_question"] = self.demo.last_voice_at
        task = asyncio.create_task(self.demo.respond_to_prompt("evt_question"))
        await asyncio.sleep(0.01)
        self.adapter.send_text.assert_not_awaited()
        await task
        self.adapter.send_text.assert_awaited_once()

    async def test_five_digit_wallet_scenario_uses_exact_operator_brief(self):
        demo = DelegateDemo({}, Path(self.temp.name) / "wallet", destination="01617683858",
            caller_id="01483694454", route_id="77022", subtest_id="01", scenario="lost-wallet",
            task="I've lost my wallet", control=self.control, adapter=self.adapter)
        self.assertEqual(demo.digit_values, {"test_route": "77022", "subtest": "01"})
        self.assertEqual(demo.phrases["lost_wallet"], "I've lost my wallet.")
        self.assertNotIn("balance_question", demo.phrases)
        demo.phrase_audio["lost_wallet"] = bytes(960)
        with self.assertRaisesRegex(ValueError, "COMPLETE_ROUTING"):
            await demo.execute("phone_speak", {"phrase": "lost_wallet"})
        self.control.request.assert_not_awaited()

    async def test_wallet_preflight_builds_valid_provider_session_without_dialling(self):
        asr = Mock()
        asr.available.return_value = (True, None)
        asr.transcribe.side_effect = lambda pcm, rate: SimpleNamespace(text=pcm.decode())
        adapter = AsyncMock()
        adapter.open_session.return_value = {"session_id": "offline-session"}
        async def empty_events():
            if False:
                yield None
        adapter.receive = empty_events
        demo = DelegateDemo({}, Path(self.temp.name) / "wallet-preflight", destination="01617683858",
            caller_id="01483694454", route_id="77022", subtest_id="01", scenario="lost-wallet",
            wallet_card_included=True,
            task="I've lost my wallet", control=self.control, adapter=adapter,
            asr=asr, synthesise=lambda text: text.encode())
        await demo.prepare()
        config = adapter.open_session.await_args.args[0]
        self.assertIn("phone_speak lost_wallet", config["instructions"])
        self.assertNotIn("phone_speak balance_question", config["instructions"])
        self.assertIn("phone_speak card_in_wallet", config["instructions"])
        self.assertEqual(demo.phrases["card_in_wallet"], "Yes, my Barclays card was in the wallet.")
        self.assertEqual(set(demo.phrase_audio), set(demo.phrases))
        self.control.request.assert_not_awaited()
        await asyncio.gather(*demo.tasks)

    async def test_unanswered_question_is_preserved_without_inventing_answer(self):
        self.demo.prompt_events.append("evt_question")
        self.demo.prompt_text["evt_question"] = "What is your postcode?"
        await self.demo.execute("report_blocked", {"reason": "missing_answer"})
        self.assertEqual(self.demo.blocking_question, "What is your postcode?")
        self.assertTrue(self.demo.done.is_set())
        self.control.request.assert_not_awaited()

    async def test_card_description_is_bound_to_confirmation_without_accepting_numbers(self):
        demo = DelegateDemo({}, Path(self.temp.name) / "wallet-description", destination="01617683827",
            caller_id="01483694454", route_id="770222", subtest_id="01", scenario="lost-wallet",
            wallet_card_included=True, wallet_card_description="It's my Premier and business banking card.",
            task="I've lost my wallet", control=self.control, adapter=self.adapter)
        self.assertEqual(demo.phrases["card_description"], "It's my Premier and business banking card.")
        self.assertEqual(demo.phrases["card_in_wallet"], "Yes. It's my Premier and business banking card.")
        demo.active = True
        demo.prompt_events = ["evt_priority"]
        demo.prompt_text["evt_priority"] = "Which would you like to sort first?"
        await demo.respond_to_prompt("evt_priority")
        self.assertEqual(demo.reason, "TASK_BLOCKED:missing_card_priority")
        self.adapter.send_text.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "INVALID_DEMO_CARD_DESCRIPTION"):
            DelegateDemo({}, Path(self.temp.name) / "invalid-description", destination="01617683827",
                caller_id="01483694454", route_id="770222", task="Wallet", scenario="lost-wallet",
                wallet_card_included=True, wallet_card_description="Card 1234567890123456",
                control=self.control, adapter=self.adapter)

    async def test_cached_speech_is_reassessed_and_replaced_if_transcript_fails(self):
        asr = Mock()
        phrase = "I've lost my wallet."
        asr.transcribe.return_value = SimpleNamespace(text=phrase)
        synth = Mock(return_value=b"correct_audio")
        demo = DelegateDemo({}, Path(self.temp.name) / "cache-run", destination="01617683827",
            caller_id="01483694454", route_id="770222", task="Wallet", scenario="lost-wallet",
            control=self.control, adapter=self.adapter, asr=asr, synthesise=synth,
            speech_cache=Path(self.temp.name) / "speech-cache")
        await demo.prepare_phrase("lost_wallet", phrase, asyncio.Semaphore(1))
        next(demo.speech_cache.iterdir()).write_bytes(b"corrupted_audio")
        synth.reset_mock()
        asr.transcribe.reset_mock()
        asr.transcribe.side_effect = [SimpleNamespace(text="Wrong phrase"), SimpleNamespace(text=phrase)]
        await demo.prepare_phrase("lost_wallet", phrase, asyncio.Semaphore(1))
        self.assertEqual(asr.transcribe.call_count, 2)
        synth.assert_called_once_with(phrase)
        self.assertEqual(demo.phrase_audio["lost_wallet"], b"correct_audio")

    async def test_transient_synthesis_failure_retries_only_before_dial(self):
        from unittest.mock import patch
        asr = Mock()
        asr.transcribe.return_value = SimpleNamespace(text="Hello.")
        synth = Mock(side_effect=[CallerVoiceUnavailable("CALLER_VOICE_TTS_TRANSPORT_ERROR"), b"audio"])
        demo = DelegateDemo({}, Path(self.temp.name) / "retry-run", destination="01617683827",
            caller_id="01483694454", route_id="770222", task="Wallet", scenario="lost-wallet",
            control=self.control, adapter=self.adapter, asr=asr, synthesise=synth)
        with patch("voice_harness.telephony_demo.asyncio.sleep", new=AsyncMock()):
            await demo.prepare_phrase("greeting", "Hello.", asyncio.Semaphore(1))
        self.assertEqual(synth.call_count, 2)
        self.control.request.assert_not_awaited()

    async def test_subtest_requires_bound_value_and_prior_route(self):
        self.demo.socket = AsyncMock()
        self.demo.prompt_events = ["evt_subtest"]
        args = {"value_ref": "subtest", "prompt_event_id": "evt_subtest", "terminator": "#"}
        with self.assertRaises(ValueError):
            await self.demo.execute("phone_send_dtmf", args)
        self.demo.digit_values["subtest"] = "42"
        with self.assertRaises(ValueError):
            await self.demo.execute("phone_send_dtmf", args)
        self.demo.sent_values.add("test_route")
        async def update(method, sid, data):
            self.assertIn('digits="42#"', data["Twiml"])
            self.demo.stream_ready.set()
            return {"status": "in-progress"}
        self.control.request.side_effect = update
        result = await self.demo.execute("phone_send_dtmf", args)
        self.assertTrue(result["reconnected"])
        with self.assertRaises(ValueError):
            await self.demo.execute("phone_send_dtmf", args)
        self.assertEqual(self.control.request.await_count, 1)

    async def test_evidence_failure_blocks_external_action(self):
        self.demo.socket = AsyncMock()
        self.demo.prompt_events = ["evt_prompt"]
        def broken(*args):
            raise OSError("disk full")
        self.demo.record = broken
        with self.assertRaises(OSError):
            await self.demo.execute("phone_send_dtmf", {"value_ref": "test_route", "prompt_event_id": "evt_prompt", "terminator": ""})
        self.control.request.assert_not_awaited()

    async def test_cleanup_hangs_up_active_call_and_never_claims_oracle(self):
        self.control.request.side_effect = [{"status": "in-progress"}, {"status": "completed"},
                                           {"status": "completed", "sid": "CAtest", "duration": "3"}]
        summary = await self.demo.finish()
        self.assertEqual(self.control.request.await_args_list[1].args, ("POST", "CAtest", {"Status": "completed"}))
        self.assertEqual(summary["cleanup"], "confirmed")
        self.assertEqual(summary["platform_outcome"], "not_independently_verified_no_oracle")
        self.assertTrue(summary["ledger_valid"])
        manifest = json.loads((self.demo.root / "manifest.json").read_text())
        for name, digest in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((self.demo.root / name).read_bytes()).hexdigest(), digest)
        self.assertTrue(verify_output(self.demo.root))
        (self.demo.root / "remote.wav").write_bytes(b"tampered")
        self.assertFalse(verify_output(self.demo.root))

    async def test_common_clock_audio_index_is_bound_to_original_manifest(self):
        self.control.request.return_value = {"status": "completed", "sid": "CAtest", "duration": "1"}
        self.demo.capture_audio("remote", bytes(320), at_ns=1_000_000_000, generation=0, stream_timestamp_ms=5)
        self.demo.capture_audio("remote", bytes(320), at_ns=1_020_000_000, generation=0, stream_timestamp_ms=25)
        self.demo.capture_audio("sent", bytes(160), at_ns=2_000_000_000, generation=0)
        # Use a synchronous ASR double, like the real backend called via to_thread.
        self.demo.asr = Mock()
        self.demo.asr.transcribe.return_value = SimpleNamespace(text="Demo audio", model="offline", backend="offline")
        await self.demo.finish()
        timing = json.loads((self.demo.root / "audio-timing.json").read_text())
        self.assertEqual([r["source_frame"] for r in timing["chunks"]], [0, 160, 0])
        self.assertEqual([r["at_ns"] for r in timing["chunks"]], [1_000_000_000, 1_020_000_000, 2_000_000_000])
        self.assertTrue(verify_output(self.demo.root))
        timing["chunks"][1]["at_ns"] += 1_000_000_000
        (self.demo.root / "audio-timing.json").write_text(json.dumps(timing))
        self.assertFalse(verify_output(self.demo.root))

    async def test_twilio_timeout_never_retries(self):
        count = 0
        def fail(request):
            nonlocal count
            count += 1
            raise httpx.ReadTimeout("timeout")
        client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
        control = TwilioControl({"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "secret"}, client)
        with self.assertRaisesRegex(TwilioError, "UNCERTAIN"):
            await control.request("POST", data={"To": "+441617683858"})
        self.assertEqual(count, 1)
        await client.aclose()

    async def test_tool_result_can_wait_for_remote_prompt_without_starting_response(self):
        adapter = OpenAIRealtimeAdapter(api_key="offline-only")
        adapter._create_response = AsyncMock()
        async def acknowledge(event):
            call_id = event["item"]["call_id"]
            adapter._acks["output:" + call_id].set_result({"acknowledged": True})
        adapter._send = acknowledge
        result = await adapter.submit_tool_result("call_1", {"call_id": "call_1"}, respond=False)
        self.assertEqual(result["provider_acknowledgement"], "acknowledged")
        adapter._create_response.assert_not_awaited()
        await adapter.submit_tool_result("call_2", {"call_id": "call_2"})
        adapter._create_response.assert_awaited_once()
