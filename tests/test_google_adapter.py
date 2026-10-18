"""Gemini Live (Vertex) adapter tests: offline fake server speaking the message shapes seen in the Phase 0 probe."""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import websockets

from voice_harness.adapters import (
    AdapterEventAdapter,
    AudioOut,
    Interrupted,
    LiveAdapter,
    ProviderError,
    ToolCall,
    TranscriptDelta,
    TranscriptFinal,
    TurnEnd,
    Usage,
)
from voice_harness.adapters import google_gemini_live as gem
from voice_harness.adapters.google_gemini_live import DEFAULT_MODEL, GeminiLiveAdapter
from voice_harness.mock_provider import CapabilityUnsupported

TOKEN = "ya29.TEST-SECRET-TOKEN-0123456789"
TOOL = {"name": "get_account_summary", "description": "d",
        "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}}}
PCM = base64.b64encode(bytes(4800)).decode()  # 100 ms at 24 kHz


def audio():
    return {"serverContent": {"modelTurn": {"role": "model", "parts": [{"inlineData": {"mimeType": "audio/pcm", "data": PCM}}]}}}


def usage():
    return {"promptTokenCount": 18, "candidatesTokenCount": 13, "totalTokenCount": 31,
            "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 18}],
            "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 13}]}


def complete():
    return {"serverContent": {"turnComplete": True, "groundingMetadata": {}}, "usageMetadata": usage()}


class FakeServer:
    def __init__(self, on_event):
        self.on_event = on_event
        self.received, self.headers, self.url_path = [], None, None

    async def handler(self, ws):
        self.headers, self.url_path = ws.request.headers, ws.request.path
        async for raw in ws:
            ev = json.loads(raw)
            self.received.append(ev)
            if "setup" in ev:
                await ws.send(json.dumps({"setupComplete": {"sessionId": "sess_g"}}))
            else:
                await self.on_event(ws, ev, self)

    async def __aenter__(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/ws/x.LlmBidiService/BidiGenerateContent"
        return self

    async def __aexit__(self, *a):
        self.server.close()
        await self.server.wait_closed()


async def noop(ws, ev, srv):
    pass


async def collect(adapter, until, timeout=5):
    out = []

    async def run():
        async for ev in adapter.receive():
            out.append(ev)
            if until(ev):
                return
    await asyncio.wait_for(run(), timeout)
    return out


class GeminiAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def _open(self, srv, **cfg):
        a = GeminiLiveAdapter(token_provider=lambda: TOKEN, url=srv.url, project="proj-1", ack_timeout=0.5)
        a.sink = []
        a.native_sink = lambda ad, ty, ev: a.sink.append((ad, ty, ev))
        receipt = await a.open_session({"session_id": "s1", "instructions": "hi", "tools": [TOOL], **cfg})
        self.addAsyncCleanup(a.close, "test")
        return a, receipt

    async def test_setup_shape_receipt_headers_and_capabilities(self):
        async with FakeServer(noop) as srv:
            a, receipt = await self._open(srv)
            setup = srv.received[0]["setup"]
            self.assertEqual(setup["model"], f"projects/proj-1/locations/us-central1/publishers/google/models/{DEFAULT_MODEL}")
            self.assertEqual(setup["generationConfig"], {"responseModalities": ["AUDIO"]})
            self.assertEqual((setup["inputAudioTranscription"], setup["outputAudioTranscription"]), ({}, {}))
            self.assertEqual(setup["systemInstruction"], {"parts": [{"text": "hi"}]})
            self.assertEqual(setup["tools"], [{"functionDeclarations": [
                {"name": "get_account_summary", "description": "d", "parameters": TOOL["parameters"]}]}])
            self.assertNotIn("realtimeInputConfig", setup)  # automatic activity detection by default
            self.assertEqual(srv.headers["Authorization"], f"Bearer {TOKEN}")
            # setupComplete echoes no model: the returned version is unresolved
            self.assertEqual((receipt["model_id"], receipt["provider_session_id"]), (None, "sess_g"))
            caps = a.capabilities()
            self.assertEqual((caps["provider"], caps["tool_delivery"], caps["input_sample_rate"], caps["output_sample_rate"]),
                             ("google_gemini_live", "gateway_bridge", 16000, 24000))
            self.assertEqual((caps["interrupt_truncate"], caps["interrupt_detection"]), ("unsupported", "supported"))
            self.assertIsInstance(a, LiveAdapter)
            self.assertEqual(DEFAULT_MODEL, "gemini-live-2.5-flash-native-audio")
            self.assertEqual(a.connection_info()["region"], "us-central1")

    async def test_endpoint_host_follows_the_region(self):
        self.assertEqual(GeminiLiveAdapter(token_provider=lambda: TOKEN, region="us-central1").connection_info()["endpoint_host"],
                         "us-central1-aiplatform.googleapis.com")
        self.assertEqual(GeminiLiveAdapter(token_provider=lambda: TOKEN, region="global").connection_info()["endpoint_host"],
                         "aiplatform.googleapis.com")

    async def test_token_and_native_events_never_leak_into_adapter_events_or_repr(self):
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv)
            self.assertNotIn(TOKEN, repr(a))
            self.assertNotIn(TOKEN, json.dumps(a.connection_info()))
            self.assertNotIn(TOKEN, json.dumps(a.capabilities()))

    async def test_manual_turn_detection_disables_automatic_activity_detection(self):
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv, turn_detection="manual")
            self.assertEqual(srv.received[0]["setup"]["realtimeInputConfig"], {"automaticActivityDetection": {"disabled": True}})
            await a.request_turn()
            await asyncio.sleep(0.05)
            self.assertEqual(srv.received[-1], {"realtimeInput": {"activityEnd": {}}})

    async def test_audio_is_resampled_to_16k_and_wrongly_sized_audio_is_refused(self):
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv)
            pcm = bytes(960)  # 20 ms at 24 kHz
            for _ in range(5):
                await a.send_audio(pcm, {"sample_count": 480, "sample_rate": 24000})
            await a.send_audio(bytes(640), {"sample_count": 320, "sample_rate": 16000})
            await asyncio.sleep(0.1)
            sent = [m["realtimeInput"]["audio"] for m in srv.received if "realtimeInput" in m]
            self.assertTrue(all(s["mimeType"] == "audio/pcm;rate=16000" for s in sent))
            self.assertEqual(len(base64.b64decode(sent[-1]["data"])), 640)
            total = sum(len(base64.b64decode(s["data"])) for s in sent[:-1])
            self.assertLessEqual(abs(total - 5 * 640), 640)  # 100 ms at 16 kHz, streaming resampler may hold a little back
            for bad in (b"\x00\x01\x02", b""):
                with self.assertRaises(ValueError):
                    await a.send_audio(bad, {"sample_count": 99})

    async def test_turn_events_audio_transcripts_and_usage(self):
        async def on_event(ws, ev, srv):
            if "clientContent" in ev:
                await ws.send(json.dumps({"serverContent": {"inputTranscription": {"text": "hello "}}}))
                await ws.send(json.dumps({"serverContent": {"inputTranscription": {"text": "there"}}}))
                await ws.send(json.dumps({"serverContent": {"inputTranscription": {"finished": True}}}))
                await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": "Hi"}}}))
                await ws.send(json.dumps(audio()))
                await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": " there."}}}))
                await ws.send(json.dumps(audio()))
                await ws.send(json.dumps({"serverContent": {"outputTranscription": {"finished": True}}}))
                await ws.send(json.dumps({"serverContent": {"generationComplete": True}}))
                await ws.send(json.dumps(complete()))
        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("Say hello.")
            evs = await collect(a, lambda e: isinstance(e, TurnEnd))
            kinds = [type(e).__name__ for e in evs]
            self.assertEqual(kinds.count("TurnStart"), 1)
            self.assertLess(kinds.index("TurnStart"), kinds.index("AudioOut"))
            self.assertEqual(kinds[-2:], ["Usage", "TurnEnd"])
            outs = [e for e in evs if isinstance(e, AudioOut)]
            self.assertEqual((len(outs), outs[0].sample_rate, outs[0].sample_count), (2, 24000, 2400))
            finals = [(e.role, e.text) for e in evs if isinstance(e, TranscriptFinal)]
            self.assertEqual(finals, [("user", "hello there"), ("assistant", "Hi there.")])
            self.assertEqual([e.text for e in evs if isinstance(e, TranscriptDelta) and e.role == "assistant"], ["Hi", " there."])
            u = next(e for e in evs if isinstance(e, Usage))
            self.assertEqual((u.input_tokens, u.output_tokens), (18, 13))
            self.assertEqual(u.raw["candidatesTokensDetails"], [{"modality": "AUDIO", "tokenCount": 13}])
            for e in evs:
                AdapterEventAdapter.validate_python(e.model_dump())
            self.assertIn("clientContent", srv.received[-1])
            self.assertTrue(srv.received[-1]["clientContent"]["turnComplete"])

    async def test_context_only_text_does_not_complete_the_turn(self):
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv)
            await a.send_text("note", respond=False)
            await asyncio.sleep(0.05)
            self.assertFalse(srv.received[-1]["clientContent"]["turnComplete"])

    async def test_tool_call_round_trip_by_id_and_wrong_ids_are_refused(self):
        async def on_event(ws, ev, srv):
            if "clientContent" in ev:
                await ws.send(json.dumps({"toolCall": {"functionCalls": [
                    {"name": "get_account_summary", "args": {"account_id": "ACC-001"}, "id": "call-1"}]}}))
                await ws.send(json.dumps(complete()))
        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            self.assertTrue(a.TOOL_CALL_ENDS_RESPONSE)
            await a.send_text("balance?")
            evs = await collect(a, lambda e: isinstance(e, TurnEnd))
            call = next(e for e in evs if isinstance(e, ToolCall))
            self.assertEqual((call.call_id, call.name, call.arguments), ("call-1", "get_account_summary", {"account_id": "ACC-001"}))
            with self.assertRaisesRegex(ValueError, "TOOL_RESULT_CORRELATION_FAILED"):
                await a.submit_tool_result("unknown-call", {"call_id": "call-1"})
            with self.assertRaisesRegex(ValueError, "TOOL_RESULT_CORRELATION_FAILED"):
                await a.submit_tool_result("unknown-call", {"call_id": "unknown-call"})
            ack = await a.submit_tool_result("call-1", {"call_id": "call-1", "balance": "1.00"})
            self.assertEqual((ack["call_id"], ack["provider_acknowledgement"]), ("call-1", "not_reported"))
            await asyncio.sleep(0.05)
            self.assertEqual(srv.received[-1], {"toolResponse": {"functionResponses": [
                {"id": "call-1", "name": "get_account_summary", "response": {"call_id": "call-1", "balance": "1.00"}}]}})
            with self.assertRaisesRegex(ValueError, "TOOL_RESULT_CORRELATION_FAILED"):  # a result is accepted once
                await a.submit_tool_result("call-1", {"call_id": "call-1"})

    async def test_barge_in_is_reported_once_with_generated_audio_and_truncation_is_unsupported(self):
        async def on_event(ws, ev, srv):
            if "clientContent" in ev:
                for _ in range(3):
                    await ws.send(json.dumps(audio()))
                await ws.send(json.dumps({"serverContent": {"outputTranscription": {"text": "Well,"}}}))
                await ws.send(json.dumps({"serverContent": {"interrupted": True}}))
        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("talk")
            evs = await collect(a, lambda e: isinstance(e, TurnEnd))
            it = [e for e in evs if isinstance(e, Interrupted)]
            self.assertEqual(len(it), 1)
            self.assertEqual(it[0].audio_end_ms, 300)  # generated audio of the cut turn, not a played position
            self.assertEqual([e.text for e in evs if isinstance(e, TranscriptFinal)], ["Well,"])
            with self.assertRaises(CapabilityUnsupported):
                await a.interrupt(it[0].item_id, 100)

    async def test_provider_error_and_close_codes_become_events_without_hanging(self):
        async def on_event(ws, ev, srv):
            if "clientContent" in ev:
                await ws.close(1008, "Publisher model was not found")
        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            evs = await collect(a, lambda e: isinstance(e, ProviderError))
            err = evs[-1]
            self.assertEqual((err.code, err.fatal), ("PROVIDER_REFUSED", True))
            self.assertIn("1008", err.message)

    async def test_connect_failures_do_not_reveal_the_token(self):
        a = GeminiLiveAdapter(token_provider=lambda: TOKEN, url="ws://127.0.0.1:9/x", project="p")
        with self.assertRaises(ValueError) as ctx:
            await a.open_session({"session_id": "s"})
        self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertTrue(str(ctx.exception).startswith("PROVIDER_UNREACHABLE"))
        a2 = GeminiLiveAdapter(token_provider=lambda: TOKEN, project=None)
        with mock.patch.object(gem, "project_id", lambda environ=None: None), self.assertRaisesRegex(ValueError, "PROVIDER_AUTH_MISSING"):
            await a2.open_session({"session_id": "s"})

    async def test_malformed_frames_are_reported_not_fatal(self):
        async def on_event(ws, ev, srv):
            if "clientContent" in ev:
                await ws.send("not json")
                await ws.send(json.dumps({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm"}}]}}}))
        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            evs = await collect(a, lambda e: isinstance(e, ProviderError), 3)
            self.assertEqual(evs[-1].code, "MALFORMED_PROVIDER_EVENT")
            self.assertFalse(evs[-1].fatal)


class CredentialTests(unittest.TestCase):
    def adc(self, data):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "adc.json"
        p.write_text(json.dumps(data))
        return mock.patch.dict("os.environ", {gem.CREDENTIALS_ENV: str(p)}, clear=False)

    def test_availability_checks_presence_only(self):
        secret = "refresh-SECRET-VALUE"
        with self.adc({"type": "authorized_user", "refresh_token": secret, "quota_project_id": "proj-1"}):
            ok, reason = gem.credentials_available({})
            self.assertEqual((ok, reason), (True, None))
            self.assertNotIn(secret, json.dumps(gem.credentials_available({})))
            self.assertEqual(gem.project_id({}), "proj-1")
            self.assertEqual(gem.project_id({"GOOGLE_CLOUD_PROJECT": "other"}), "other")
        with self.adc({"type": "authorized_user", "refresh_token": secret}):
            self.assertEqual(gem.credentials_available({}), (False, "NO_GOOGLE_PROJECT"))
        with mock.patch.dict("os.environ", {gem.CREDENTIALS_ENV: "/nonexistent/adc.json"}):
            self.assertEqual(gem.credentials_available({}), (False, "NO_GOOGLE_ADC"))

    def test_refresh_flow_returns_only_the_access_token(self):
        info = {"type": "authorized_user", "client_id": "cid", "client_secret": "csecret", "refresh_token": "rtok"}
        seen = {}

        def fake_post(url, **kw):
            seen["url"], seen["data"] = url, kw["data"]
            return mock.Mock(status_code=200, json=lambda: {"access_token": TOKEN, "expires_in": 3599})
        with self.adc(info), mock.patch("httpx.post", fake_post):
            self.assertEqual(gem._fetch_access_token(), TOKEN)
        self.assertEqual(seen["url"], gem.TOKEN_URL)
        self.assertEqual(seen["data"]["grant_type"], "refresh_token")

        with self.adc(info), mock.patch("httpx.post", lambda url, **kw: mock.Mock(status_code=400, json=lambda: {"error": "invalid_grant"})):
            with self.assertRaisesRegex(ValueError, "PROVIDER_AUTH_FAILED") as ctx:
                gem._fetch_access_token()
            self.assertNotIn("rtok", str(ctx.exception))
        with self.adc({"type": "service_account"}), mock.patch.dict("sys.modules", {"google": None, "google.auth": None}):
            with self.assertRaisesRegex(ValueError, "PROVIDER_AUTH_UNSUPPORTED_CREDENTIAL_TYPE"):
                gem._fetch_access_token()


if __name__ == "__main__":
    unittest.main()
