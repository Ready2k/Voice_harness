"""xAI Grok Voice adapter tests: offline fake server speaking the event shapes seen in the Phase 0 probe."""
import asyncio
import base64
import json
import unittest

import websockets

from voice_harness.adapters import AdapterEventAdapter, LiveAdapter
from voice_harness.adapters.xai_voice import DEFAULT_MODEL, XaiVoiceAdapter, _loud

KEY = "xai-TEST-SECRET-0123456789"
TOOL = {"name": "get_account_summary", "description": "d",
        "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}}}
PCM = base64.b64encode(bytes(2400)).decode()  # 50 ms of silence


def audio(item="item_a", resp="resp_1"):
    return {"type": "response.output_audio.delta", "item_id": item, "response_id": resp, "delta": PCM, "audio_duration_ms": 50}


def usage():
    return {"input_tokens": 10, "input_token_details": {"text_tokens": 10, "audio_tokens": 0, "grok_tokens": 0},
            "output_tokens": 68, "output_token_details": {"text_tokens": 4, "audio_tokens": 64, "grok_tokens": 0},
            "total_tokens": 78, "output_audio_seconds": 1.28, "billable_audio_seconds": 1}


def done(resp="resp_1", status="completed", output=(("m", "message"),)):
    return {"type": "response.done", "response": {"id": resp, "status": status, "usage": {},
                                                  "output": [{"id": i, "type": t} for i, t in output]}, "usage": usage()}


def started(resp="resp_1", item="item_a"):
    return [{"type": "response.created", "response": {"id": resp}},
            {"type": "response.output_item.added", "item": {"id": item, "type": "message", "role": "assistant"}}]


class FakeServer:
    def __init__(self, on_event, model="grok-voice-3.0", ping=True):
        self.on_event, self.model, self.ping = on_event, model, ping
        self.received, self.headers, self.url_path = [], None, None

    async def handler(self, ws):
        self.headers, self.url_path = ws.request.headers, ws.request.path
        await ws.send(json.dumps({"type": "session.created", "session": {
            "id": "sess_x", "model": self.model, "voice": "xai_ara", "turn_detection": {"type": None}}}))
        if self.ping:
            await ws.send(json.dumps({"type": "ping"}))
        async for raw in ws:
            ev = json.loads(raw)
            self.received.append(ev)
            if ev["type"] == "session.update":
                await ws.send(json.dumps({"type": "session.updated", "session": ev["session"]}))
            else:
                await self.on_event(ws, ev, self)

    async def __aenter__(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1/realtime"
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


class XaiAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def _open(self, srv, **cfg):
        a = XaiVoiceAdapter(api_key=KEY, url=srv.url, ack_timeout=0.5)
        a.sink = []
        a.native_sink = lambda ad, ty, ev: a.sink.append((ad, ty, ev))
        receipt = await a.open_session({"session_id": "s1", "instructions": "hi", "tools": [TOOL], **cfg})
        self.addAsyncCleanup(a.close, "test")
        return a, receipt

    async def test_session_shape_receipt_and_capabilities(self):
        async with FakeServer(noop, model="grok-voice-3.0") as srv:
            a, receipt = await self._open(srv, model_id="grok-voice-latest")
            su = srv.received[0]["session"]
            self.assertNotIn("type", su)
            self.assertNotIn("model", su)
            self.assertEqual(su["audio"]["input"]["format"], {"type": "audio/pcm", "rate": 24000})
            self.assertEqual(su["audio"]["output"]["format"], {"type": "audio/pcm", "rate": 24000})
            self.assertIsNone(su["turn_detection"])
            self.assertEqual(su["input_audio_transcription"], {"model": "grok-transcribe"})
            self.assertEqual(su["tools"][0], {"type": "function", "name": "get_account_summary", "description": "d",
                                              "parameters": TOOL["parameters"]})
            self.assertTrue(srv.url_path.endswith("?model=grok-voice-latest"))
            self.assertEqual(srv.headers["Authorization"], f"Bearer {KEY}")
            # the receipt carries the server-resolved model, not the alias that was requested
            self.assertEqual((receipt["model_id"], receipt["provider_session_id"]), ("grok-voice-3.0", "sess_x"))
            caps = a.capabilities()
            self.assertEqual((caps["provider"], caps["tool_delivery"], caps["input_sample_rate"]),
                             ("xai_grok_voice", "gateway_bridge", 24000))
            self.assertEqual(caps["interrupt_truncate"], "supported")
            self.assertIsInstance(a, LiveAdapter)
            self.assertEqual(DEFAULT_MODEL, "grok-voice-3.0")

    async def test_vad_config_and_pings_are_ignored(self):
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv, turn_detection="server_vad")
            self.assertEqual(srv.received[0]["session"]["turn_detection"], {"type": "server_vad"})
            await asyncio.sleep(0.05)
            self.assertNotIn("ping", [t for _, t, _ in a.sink])  # keep-alives are not provider events

    async def test_tool_round_trip_normalised_events_and_usage(self):
        script = [*started(), audio(),
                  {"type": "response.output_audio_transcript.delta", "item_id": "item_a", "delta": "Checking"},
                  {"type": "response.output_audio_transcript.done", "item_id": "item_a", "transcript": "Checking that now."},
                  {"type": "response.function_call_arguments.done", "call_id": "call-1", "name": "get_account_summary",
                   "arguments": "{\"account_id\":\"ACC-001\"}"},
                  done(output=(("m", "message"), ("f", "function_call")))]

        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create" and sum(e["type"] == "response.create" for e in srv.received) == 1:
                for e in script:
                    await ws.send(json.dumps(e))
            elif ev["type"] == "conversation.item.create" and ev["item"]["type"] == "function_call_output":
                await ws.send(json.dumps({"type": "conversation.item.added", "item": ev["item"] | {"id": "out"}}))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("What is the balance?")
            evs = await collect(a, lambda e: e.type == "turn_end")
            for e in evs:
                AdapterEventAdapter.validate_python(e.model_dump())
            self.assertEqual([e.type for e in evs],
                             ["turn_start", "audio_out", "transcript_delta", "transcript_final", "tool_call", "usage", "turn_end"])
            call = next(e for e in evs if e.type == "tool_call")
            self.assertEqual((call.call_id, call.arguments), ("call-1", {"account_id": "ACC-001"}))
            u = next(e for e in evs if e.type == "usage")
            self.assertEqual((u.input_tokens, u.output_tokens), (10, 68))
            self.assertEqual(u.raw["output_token_details"]["audio_tokens"], 64)
            self.assertEqual(u.raw["response_status"], "completed")
            self.assertTrue(XaiVoiceAdapter.TOOL_CALL_ENDS_RESPONSE)

            with self.assertRaisesRegex(ValueError, "TOOL_RESULT_CORRELATION_FAILED"):
                await a.submit_tool_result("call-1", {"call_id": "other"})
            ack = await a.submit_tool_result("call-1", {"call_id": "call-1", "balance": "1234.56"})
            self.assertEqual(ack["provider_acknowledgement"], "acknowledged")
            await asyncio.sleep(0.1)
            tail = [e["type"] for e in srv.received][-2:]
            self.assertEqual(tail, ["conversation.item.create", "response.create"])
            out = [e for e in srv.received if e["type"] == "conversation.item.create"][-1]["item"]
            self.assertEqual((out["type"], out["call_id"]), ("function_call_output", "call-1"))

    async def test_invalid_tool_arguments_are_an_error_not_a_call(self):
        bad = {"type": "response.function_call_arguments.done", "call_id": "c", "name": "n", "arguments": "[1]"}

        async def on_event(ws, ev, srv):
            await ws.send(json.dumps(bad))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            evs = await collect(a, lambda e: e.type == "error")
            self.assertEqual(evs[-1].code, "TOOL_ARGUMENTS_INVALID")

    async def test_user_transcript_is_cumulative_and_repeated_completions_collapse(self):
        script = [{"type": "conversation.item.input_audio_transcription.updated", "item_id": "u1", "transcript": "Stop."},
                  {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Stop."},
                  {"type": "conversation.item.input_audio_transcription.updated", "item_id": "u1", "transcript": "Stop. Please."},
                  {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Stop. Please."},
                  {"type": "conversation.item.input_audio_transcription.completed", "item_id": "u1", "transcript": "Stop. Please."},
                  done()]

        async def on_event(ws, ev, srv):
            for e in script:
                await ws.send(json.dumps(e))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            evs = await collect(a, lambda e: e.type == "turn_end")
            finals = [e.text for e in evs if e.type == "transcript_final" and e.role == "user"]
            self.assertEqual(finals, ["Stop.", "Stop. Please."])
            self.assertEqual([e.text for e in evs if e.type == "transcript_delta"], ["Stop.", "Stop. Please."])

    async def test_server_vad_cut_is_reported_as_one_interrupted_and_truncates_the_cut_item(self):
        events = []

        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create":
                for e in [*started(), audio(), audio()]:
                    await ws.send(json.dumps(e))
            elif ev["type"] == "input_audio_buffer.append":
                if not events:
                    events.append(1)
                    # observed order: the cut response ends (completed, empty output) just before speech_started
                    await ws.send(json.dumps(done(output=())))
                    await ws.send(json.dumps({"type": "input_audio_buffer.speech_started"}))
                    await ws.send(json.dumps({"type": "input_audio_buffer.speech_started"}))  # repeated: still one
            elif ev["type"] == "conversation.item.truncate":
                if ev["audio_end_ms"] <= 120:  # the server only keeps what had played by speech onset
                    await ws.send(json.dumps({"type": "conversation.item.truncated", "item_id": ev["item_id"],
                                              "audio_end_ms": ev["audio_end_ms"]}))

        import array
        loud = array.array("h", [8000, -8000] * 240).tobytes()
        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("long answer")
            await collect(a, lambda e: e.type == "audio_out")
            await asyncio.sleep(0.12)
            await a.send_audio(loud, {"sample_count": 480, "sample_rate": 24000})
            evs = await collect(a, lambda e: e.type == "interrupted")
            interrupted = [e for e in evs if e.type == "interrupted"]
            self.assertEqual(len(interrupted), 1)
            self.assertEqual(interrupted[0].item_id, "item_a")
            await asyncio.sleep(0.1)
            more = []
            while not a._queue.empty():
                more.append(a._queue.get_nowait())
            self.assertEqual([e.type for e in more if e.type == "interrupted"], [])
            self.assertLessEqual(interrupted[0].audio_end_ms, 200)  # played at speech onset, not generated ahead
            receipt = await a.interrupt("item_a", 5000)
            self.assertEqual(receipt["history_truncation"], "acknowledged")
            self.assertLessEqual(receipt["audio_end_ms"], 120 + 1)
            self.assertGreaterEqual(receipt["truncate_attempts"], 1)

    async def test_natural_completion_then_speech_is_not_an_interruption(self):
        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create":
                for e in [*started(), audio(), done()]:  # normal end: output has the item
                    await ws.send(json.dumps(e))
            elif ev["type"] == "input_audio_buffer.append":
                await ws.send(json.dumps({"type": "input_audio_buffer.speech_started"}))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            await collect(a, lambda e: e.type == "turn_end")
            await a.send_audio(bytes(960), {"sample_count": 480, "sample_rate": 24000})
            await asyncio.sleep(0.15)
            self.assertTrue(a._queue.empty())

    async def test_client_cancel_is_one_interrupted_and_truncate_is_clamped_to_generated_audio(self):
        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create":
                for e in [*started(), audio(), audio()]:  # 100 ms generated
                    await ws.send(json.dumps(e))
            elif ev["type"] == "response.cancel":
                await ws.send(json.dumps(done(status="cancelled", output=())))
            elif ev["type"] == "conversation.item.truncate":
                await ws.send(json.dumps({"type": "conversation.item.truncated", "item_id": ev["item_id"]}))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            await collect(a, lambda e: e.type == "audio_out")
            await asyncio.sleep(0.05)
            receipt = await a.interrupt(None, 9000)
            evs = await collect(a, lambda e: e.type == "turn_end")
            self.assertEqual(len([e for e in evs if e.type == "interrupted"]), 1)
            self.assertEqual((receipt["generation_cancelled"], receipt["history_truncation"]), (True, "acknowledged"))
            self.assertEqual(receipt["audio_end_ms"], 100)
            usage_ev = next(e for e in evs if e.type == "usage")
            self.assertEqual(usage_ev.raw["response_status"], "cancelled")
            sent = [e for e in srv.received if e["type"] == "conversation.item.truncate"]
            self.assertEqual(sent[0]["audio_end_ms"], 100)

    async def test_unacknowledged_truncate_is_reported_rejected(self):
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv)
            receipt = await a.interrupt("item_zzz", 100)
            self.assertEqual(receipt["history_truncation"], "rejected")

    async def test_error_events_map_to_provider_errors(self):
        async def on_event(ws, ev, srv):
            await ws.send(json.dumps({"type": "error", "error": {"code": "invalid_event", "message": "bad"}}))
            await ws.send(json.dumps({"type": "error", "error": {"code": "invalid_api_key", "message": "no"}}))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            evs = await collect(a, lambda e: e.type == "error" and e.fatal)
            self.assertEqual([(e.code, e.fatal) for e in evs if e.type == "error"],
                             [("invalid_event", False), ("invalid_api_key", True)])

    async def test_connection_lost_is_a_fatal_error_and_closes_the_stream(self):
        async def on_event(ws, ev, srv):
            await ws.close()

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            evs = await collect(a, lambda e: e.type == "error")
            self.assertTrue(evs[-1].fatal)
            self.assertIn(evs[-1].code, ("CONNECTION_CLOSED", "CONNECTION_LOST"))

    async def test_missing_key_and_http_rejection_have_typed_codes(self):
        with self.assertRaisesRegex(ValueError, "PROVIDER_AUTH_MISSING"):
            await XaiVoiceAdapter(api_key="", url="ws://127.0.0.1:1").open_session({})

        def reject(connection, request):
            return connection.respond(401, "unauthorized\n")

        server = await websockets.serve(noop, "127.0.0.1", 0, process_request=reject)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1/realtime"
        with self.assertRaises(ValueError) as caught:
            await XaiVoiceAdapter(api_key=KEY, url=url).open_session({"session_id": "s"})
        self.assertEqual(str(caught.exception), "PROVIDER_AUTH_FAILED")
        self.assertNotIn(KEY, str(caught.exception))

    async def test_secret_never_appears_in_events_receipts_repr_or_connection_info(self):
        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create":
                for e in [*started(), audio(), done()]:
                    await ws.send(json.dumps(e))

        async with FakeServer(on_event) as srv:
            a, receipt = await self._open(srv)
            await a.send_text("x")
            evs = await collect(a, lambda e: e.type == "turn_end")
            blob = json.dumps([e.model_dump(mode="json") for e in evs]) + json.dumps(receipt) + repr(a) + str(a) \
                + json.dumps(a.capabilities()) + json.dumps(a.connection_info()) + json.dumps(a.sink, default=str)
            self.assertNotIn(KEY, blob)
            self.assertNotIn("Bearer", blob)
            self.assertEqual(a.connection_info()["endpoint_host"], "127.0.0.1")
            self.assertEqual(set(a.connection_info()), {"endpoint_host", "region", "sdk_versions"})

    async def test_send_audio_validation_and_closed_session(self):
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv)
            with self.assertRaisesRegex(ValueError, "INVALID_AUDIO"):
                await a.send_audio(b"\x00\x01\x02", {"sample_count": 99})
            with self.assertRaisesRegex(ValueError, "INVALID_AUDIO_RATE"):
                await a.send_audio(bytes(960), {"sample_count": 480, "sample_rate": 16000})
            await a.close("done")
            with self.assertRaises(ValueError):
                await a.send_text("x")

    def test_loud_detector(self):
        import array
        self.assertFalse(_loud(bytes(960)))
        self.assertFalse(_loud(array.array("h", [100, -100] * 100).tobytes()))
        self.assertTrue(_loud(array.array("h", [8000, -8000] * 100).tobytes()))
        self.assertFalse(_loud(b""))


if __name__ == "__main__":
    unittest.main()
