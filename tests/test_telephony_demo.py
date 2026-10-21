"""Offline containment and protocol checks; these cannot prove IVR compatibility."""
import base64
import asyncio
import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import numpy as np
from fastapi.testclient import TestClient

from voice_harness.telephony_demo import (
    DelegateDemo, TwilioControl, TwilioError, decode_mulaw, encode_mulaw,
    normalise_number, signature_valid, verify_output,
)
from voice_harness.adapters.openai_realtime import OpenAIRealtimeAdapter


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
