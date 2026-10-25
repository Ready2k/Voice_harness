"""ElevenLabs Agents adapter tests: offline fake server speaking the documented event shapes."""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import websockets

from voice_harness import live_providers
from voice_harness.adapters import AdapterEventAdapter, LiveAdapter
from voice_harness.adapters import elevenlabs_agent as agent
from voice_harness.adapters.elevenlabs_voice import ElevenLabsVoiceAdapter, credentials_available, redact
from voice_harness.mock_provider import CapabilityUnsupported

KEY = "xi-TEST-SECRET-0123456789"
PCM = base64.b64encode(bytes(2400)).decode()  # 50 ms of silence at 24 kHz
CHUNK = bytes(960)


def audio(n=1):
    return {"type": "audio", "audio_event": {"audio_base_64": PCM, "event_id": n}}


class FakeServer:
    def __init__(self, on_message=None, *, audio_format="pcm_24000"):
        self.on_message, self.audio_format = on_message, audio_format
        self.received, self.headers, self.path = [], None, None

    async def handler(self, ws):
        self.headers, self.path = ws.request.headers, ws.request.path
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            if msg.get("type") == "conversation_initiation_client_data":
                await ws.send(json.dumps({"type": "conversation_initiation_metadata", "conversation_initiation_metadata_event": {
                    "conversation_id": "conv_x", "agent_output_audio_format": self.audio_format,
                    "user_input_audio_format": self.audio_format}}))
                await ws.send(json.dumps({"type": "ping", "ping_event": {"event_id": 7, "ping_ms": 12}}))
            elif self.on_message:
                await self.on_message(ws, msg)

    async def __aenter__(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1/convai/conversation"
        return self

    async def __aexit__(self, *a):
        self.server.close()
        await self.server.wait_closed()


async def collect(adapter, until, timeout=5):
    out = []

    async def run():
        async for ev in adapter.receive():
            out.append(ev)
            if until(ev):
                return
    await asyncio.wait_for(run(), timeout)
    return out


def adapter_for(srv, **kw):
    return ElevenLabsVoiceAdapter(api_key=KEY, url=srv.url, agent="agent_test", idle_s=0.1, **kw)


class ElevenLabsAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_satisfies_protocol_and_declares_no_truncation(self):
        a = ElevenLabsVoiceAdapter(api_key=KEY, agent="a")
        self.assertIsInstance(a, LiveAdapter)
        caps = a.capabilities()
        self.assertEqual(caps["interrupt_truncate"], "unsupported")
        self.assertEqual((caps["input_sample_rate"], caps["output_sample_rate"]), (24000, 24000))
        self.assertNotIn(KEY, repr(a))

    async def test_open_session_sends_overrides_and_authenticates_by_header(self):
        async with FakeServer() as srv:
            a = adapter_for(srv)
            receipt = await a.open_session({"session_id": "s1", "instructions": "Be brief.", "tools": []})
            await asyncio.sleep(0.1)
            await a.close("done")
        init = srv.received[0]
        override = init["conversation_config_override"]
        self.assertEqual(override["agent"]["prompt"], {"prompt": "Be brief."})
        self.assertEqual(override["agent"]["first_message"], "")
        self.assertEqual(srv.headers["xi-api-key"], KEY)
        self.assertIn("agent_id=agent_test", srv.path)
        self.assertNotIn(KEY, srv.path)
        self.assertEqual((receipt["provider_session_id"], receipt["session_id"]), ("conv_x", "s1"))

    async def test_ping_is_answered_with_pong_and_not_emitted(self):
        async with FakeServer() as srv:
            a = adapter_for(srv)
            await a.open_session({})
            await asyncio.sleep(0.2)
            await a.close("done")
        self.assertIn({"type": "pong", "event_id": 7}, srv.received)

    async def test_audio_text_and_transcripts_become_normalised_events(self):
        async def respond(ws, msg):
            if msg.get("user_audio_chunk"):
                await ws.send(json.dumps({"type": "user_transcript", "user_transcription_event": {"user_transcript": "hello"}}))
                await ws.send(json.dumps(audio(1)))
                await ws.send(json.dumps(audio(2)))
                await ws.send(json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": "Hi there."}}))

        async with FakeServer(respond) as srv:
            a = adapter_for(srv)
            await a.open_session({})
            await a.send_audio(CHUNK, {"sample_count": 480, "sample_rate": 24000})
            events = await collect(a, lambda e: e.type == "turn_end")
            await a.close("done")
        self.assertEqual([e.type for e in events],
                         ["transcript_final", "turn_start", "audio_out", "audio_out", "transcript_final", "turn_end"])
        self.assertEqual((events[0].role, events[0].text), ("user", "hello"))
        self.assertEqual((events[4].role, events[4].text), ("assistant", "Hi there."))
        self.assertEqual(events[2].sample_count, 1200)
        for e in events:
            AdapterEventAdapter.validate_python(e.model_dump())

    async def test_audio_after_the_text_response_stays_in_the_same_turn(self):
        """Observed live: agent_response arrives before its audio has finished streaming."""
        async def respond(ws, msg):
            if msg.get("type") == "user_message":
                await ws.send(json.dumps(audio(1)))
                await ws.send(json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": "Hi."}}))
                for n in range(2, 6):
                    await asyncio.sleep(0.04)
                    await ws.send(json.dumps(audio(n)))

        async with FakeServer(respond) as srv:
            a = adapter_for(srv)
            await a.open_session({})
            await a.send_text("hi")
            events = await collect(a, lambda e: e.type == "turn_end")
            await asyncio.sleep(0.3)
            await a.close("done")
        kinds = [e.type for e in events]
        self.assertEqual(kinds.count("audio_out"), 5)
        self.assertEqual(kinds.count("turn_start"), 1)
        self.assertEqual(kinds[-1], "turn_end")

    async def test_send_audio_rejects_wrong_rate_and_lengths(self):
        async with FakeServer() as srv:
            a = adapter_for(srv)
            await a.open_session({})
            with self.assertRaisesRegex(ValueError, "INVALID_AUDIO_RATE"):
                await a.send_audio(CHUNK, {"sample_count": 480, "sample_rate": 16000})
            with self.assertRaisesRegex(ValueError, "INVALID_AUDIO"):
                await a.send_audio(CHUNK, {"sample_count": 100, "sample_rate": 24000})
            await a.close("done")

    async def test_text_with_and_without_reply(self):
        async with FakeServer() as srv:
            a = adapter_for(srv)
            await a.open_session({})
            await a.send_text("What is my balance?")
            await a.send_text("[orchestrator] note", respond=False)
            await asyncio.sleep(0.1)
            await a.close("done")
        self.assertIn({"type": "user_message", "text": "What is my balance?"}, srv.received)
        self.assertIn({"type": "contextual_update", "text": "[orchestrator] note"}, srv.received)

    async def test_tool_call_ends_the_response_and_result_is_correlated(self):
        async def respond(ws, msg):
            if msg.get("type") == "user_message":
                await ws.send(json.dumps(audio(1)))
                await ws.send(json.dumps({"type": "client_tool_call", "client_tool_call": {
                    "tool_name": "get_account_summary", "tool_call_id": "call_1", "parameters": {"account_id": "acct_1001"}}}))

        async with FakeServer(respond) as srv:
            a = adapter_for(srv)
            await a.open_session({})
            await a.send_text("balance?")
            events = await collect(a, lambda e: e.type == "turn_end")
            call = next(e for e in events if e.type == "tool_call")
            self.assertEqual((call.name, call.arguments), ("get_account_summary", {"account_id": "acct_1001"}))
            self.assertLess([e.type for e in events].index("tool_call"), [e.type for e in events].index("turn_end"))
            with self.assertRaisesRegex(ValueError, "TOOL_RESULT_CORRELATION_FAILED"):
                await a.submit_tool_result("call_1", {"call_id": "other"})
            ack = await a.submit_tool_result("call_1", {"call_id": "call_1", "balance": 5})
            self.assertEqual(ack["local_send"], "accepted")
            self.assertEqual(a._turn_id, None)  # closed at submit: the reply that uses the result is a new turn
            with self.assertRaisesRegex(ValueError, "TOOL_RESULT_CORRELATION_FAILED"):
                await a.submit_tool_result("call_1", {"call_id": "call_1"})  # single use
            await asyncio.sleep(0.1)
            await a.close("done")
        result = next(m for m in srv.received if m.get("type") == "client_tool_result")
        self.assertEqual((result["tool_call_id"], json.loads(result["result"])["balance"], result["is_error"]), ("call_1", 5, False))

    async def test_server_interruption_is_reported_and_closes_the_turn(self):
        async def respond(ws, msg):
            if msg.get("type") == "user_message":
                await ws.send(json.dumps(audio(1)))
                await ws.send(json.dumps({"type": "interruption", "interruption_event": {"event_id": 3}}))

        async with FakeServer(respond) as srv:
            a = adapter_for(srv)
            await a.open_session({})
            await a.send_text("go on")
            events = await collect(a, lambda e: e.type == "turn_end")
            await a.close("done")
        cut = next(e for e in events if e.type == "interrupted")
        self.assertEqual(cut.audio_end_ms, 50)
        with self.assertRaises(CapabilityUnsupported):
            await a.interrupt(None, 10)

    async def test_native_events_reach_the_sink_without_pings(self):
        seen = []

        async def respond(ws, msg):
            if msg.get("type") == "user_message":
                await ws.send(json.dumps(audio(1)))
                await ws.send(json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": "ok"}}))

        async with FakeServer(respond) as srv:
            a = adapter_for(srv)
            a.native_sink = lambda name, kind, payload: seen.append((name, kind))
            await a.open_session({})
            await a.send_text("hi")
            await collect(a, lambda e: e.type == "turn_end")
            await a.close("done")
        self.assertIn(("elevenlabs_agents", "audio"), seen)
        self.assertNotIn(("elevenlabs_agents", "ping"), seen)

    async def test_missing_key_agent_and_audio_format_fail_with_typed_codes(self):
        with self.assertRaisesRegex(ValueError, "PROVIDER_AUTH_MISSING"):
            await ElevenLabsVoiceAdapter(api_key="", agent="a").open_session({})
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(agent, "STATE_PATH", Path("/nonexistent/x.json")):
            with self.assertRaisesRegex(ValueError, "PROVIDER_AGENT_MISSING"):
                await ElevenLabsVoiceAdapter(api_key=KEY).open_session({})
        async with FakeServer(audio_format="pcm_16000") as srv:
            a = adapter_for(srv)
            with self.assertRaisesRegex(ValueError, "PROVIDER_AUDIO_FORMAT"):
                await a.open_session({})
            await a.close("done")

    async def test_stale_agent_tools_are_refused(self):
        tools = [{"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}]
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {"ELEVENLABS_AGENT_STATE": f"{d}/s.json"}, clear=True):
            Path(d, "s.json").write_text(json.dumps({"agent_id": "a", "tools_fingerprint": "stale"}))
            with self.assertRaisesRegex(ValueError, "PROVIDER_AGENT_TOOLS_STALE"):
                await ElevenLabsVoiceAdapter(api_key=KEY).open_session({"tools": tools})

    async def test_connection_closed_is_a_fatal_error(self):
        async def hang_up(ws, msg):
            if msg.get("type") == "user_message":
                await ws.close()

        async with FakeServer(hang_up) as srv:
            a = adapter_for(srv)
            await a.open_session({})
            await a.send_text("hi")
            events = await collect(a, lambda e: e.type == "error")
            await a.close("done")
        self.assertTrue(events[-1].fatal)


class RedactionTests(unittest.TestCase):
    def test_audio_and_credentials_are_not_logged(self):
        out = redact({"user_audio_chunk": "A" * 100, "audio_event": {"audio_base_64": "B" * 50}, "xi-api-key": KEY})
        self.assertEqual(out["user_audio_chunk"], "<b64 len=100>")
        self.assertEqual(out["audio_event"]["audio_base_64"], "<b64 len=50>")
        self.assertNotIn(KEY, json.dumps(out))


class AgentProvisioningTests(unittest.TestCase):
    def test_client_tools_use_scalar_types_and_descriptions_from_the_live_tool_list(self):
        tools = agent_tools = live_providers.TOOLS
        converted = {t["name"]: t for t in agent.client_tools(tools)}
        self.assertEqual(set(converted), {t["name"] for t in agent_tools})
        for tool in converted.values():
            self.assertEqual(tool["type"], "client")
            self.assertTrue(tool["expects_response"])
            for name, prop in tool["parameters"]["properties"].items():
                self.assertIn(prop["type"], {"string", "number", "integer", "boolean"}, name)
                self.assertTrue(prop["description"], name)
            self.assertLessEqual(set(tool["parameters"]["required"]), set(tool["parameters"]["properties"]))
        self.assertEqual(converted["transfer_to_human"]["parameters"]["properties"]["queue"]["enum"],
                         ["fraud", "support", "vulnerable_customer"])
        self.assertEqual(converted["list_recent_transactions"]["parameters"]["properties"]["cursor"]["type"], "string")

    def test_fingerprint_changes_with_the_tool_list(self):
        self.assertEqual(agent.fingerprint(live_providers.TOOLS), agent.fingerprint(live_providers.TOOLS))
        self.assertNotEqual(agent.fingerprint(live_providers.TOOLS), agent.fingerprint(live_providers.TOOLS[:-1]))

    def test_setup_creates_then_updates_and_records_state(self):
        calls = []

        class Response:
            def __init__(self, code, body): self.status_code, self._body, self.text = code, body, json.dumps(body)
            def json(self): return self._body

        class Http:
            def post(self, url, json): calls.append(("POST", url)); return Response(200, {"agent_id": "agent_new"})
            def patch(self, url, json): calls.append(("PATCH", url)); return Response(200, {"agent_id": "agent_new"})

        with tempfile.TemporaryDirectory() as d:
            env = {"ELEVENLABS_API_KEY": KEY, "ELEVENLABS_AGENT_STATE": f"{d}/s.json"}
            state = agent.setup(live_providers.TOOLS, environ=env, client=Http())
            self.assertEqual(state["agent_id"], "agent_new")
            agent.setup(live_providers.TOOLS, environ=env, client=Http())
            self.assertEqual([c[0] for c in calls], ["POST", "PATCH"])
            self.assertNotIn(KEY, Path(d, "s.json").read_text())
            with self.assertRaisesRegex(ValueError, "NO_ELEVENLABS_API_KEY"):
                agent.setup(live_providers.TOOLS, environ={}, client=Http())

    def test_agent_enables_prompt_and_voice_overrides(self):
        body = agent.agent_body(live_providers.TOOLS, "gemini-2.5-flash")
        override = body["platform_settings"]["overrides"]["conversation_config_override"]
        self.assertTrue(override["agent"]["prompt"]["prompt"])
        self.assertTrue(override["tts"]["voice_id"])
        self.assertEqual(body["conversation_config"]["tts"]["agent_output_audio_format"], "pcm_24000")


class LiveProviderRegistrationTests(unittest.TestCase):
    LIVE = {"VOICE_HARNESS_LIVE_ASR": "openai", "OPENAI_API_KEY": "k"}

    def test_availability_checks_key_and_agent_without_revealing_them(self):
        with mock.patch("voice_harness.caller_voice.available", lambda environ=None: (True, None)):
            self.assertEqual(live_providers.availability("elevenlabs-live", {**self.LIVE, "ELEVENLABS_API_KEY": KEY, "ELEVENLABS_AGENT_ID": "a"}),
                             (True, None))
            self.assertEqual(live_providers.availability("elevenlabs-live", self.LIVE)[1], "NO_ELEVENLABS_API_KEY")
            with mock.patch.object(agent, "STATE_PATH", Path("/nonexistent/x.json")):
                self.assertEqual(credentials_available({"ELEVENLABS_API_KEY": KEY}), (False, "NO_ELEVENLABS_AGENT"))
                self.assertNotIn(KEY, json.dumps(live_providers.availability("elevenlabs-live", {**self.LIVE, "ELEVENLABS_API_KEY": KEY})))

    def test_adapter_and_transport_use_provider_vad(self):
        adapter, config = live_providers._adapter("elevenlabs-live")
        self.assertIsInstance(adapter, ElevenLabsVoiceAdapter)
        for mode in ("fixed", "live_ai", "human_microphone"):
            transport = live_providers.configure_transport("elevenlabs-live", dict(config), mode)
            self.assertEqual((transport["turn_detection"], transport["turn_commit"]), ("provider_vad", "provider_vad"))
            self.assertFalse(transport["client_truncation"])
        self.assertNotIn("turn_detection", config)  # ElevenLabs has no manual or tunable turn detection to send

    def test_registered_everywhere_a_provider_is_listed(self):
        from voice_harness import bakeoff, contracts, telephone_providers
        self.assertEqual(live_providers.LIVE_PROVIDERS["elevenlabs-live"], "elevenlabs_agents")
        self.assertIn("elevenlabs-live", live_providers.TESTED_MODELS)
        self.assertIn("elevenlabs-live", bakeoff.NAMES)
        self.assertIn("elevenlabs-live", telephone_providers.LABELS)
        request = bakeoff.BakeoffRequest(name="five", provider_config_ids=[
            "openai-realtime-live", "nova-sonic-live", "xai-voice-live", "gemini-live", "elevenlabs-live"])
        self.assertEqual(len(request.provider_config_ids), 5)
        self.assertIn("elevenlabs-live", json.dumps(contracts.RunConfig.model_json_schema()))


if __name__ == "__main__":
    unittest.main()
