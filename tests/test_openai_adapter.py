"""OpenAI Realtime adapter tests: offline fake server replaying Phase 0 raw logs + gated live test."""
import asyncio
import base64
import json
import os
import unittest
from pathlib import Path

import websockets

from voice_harness.adapters import AdapterEventAdapter
from voice_harness.adapters.openai_realtime import OpenAIRealtimeAdapter, redact

LOG = Path(__file__).resolve().parents[1] / "artifacts/phase0/openai-probe-2026-10-16T22-31-44-780Z.jsonl"
TOOL = {"name": "get_account_summary", "description": "d",
        "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}}}


def logged(session, first_type, last_type):
    """Inbound events of one session from first_type up to and including the next last_type."""
    rows = [json.loads(line) for line in LOG.read_text().splitlines()]
    rows = [r for r in rows if r["session"] == session and r["dir"] == "in"]
    start = next(i for i, r in enumerate(rows) if r["type"] == first_type)
    out = []
    for r in rows[start:]:
        d = r["detail"]
        if r["type"] == "response.output_audio.delta":  # raw audio is redacted in the log; restore silence
            d = dict(d, delta=base64.b64encode(bytes(d["delta"]["b64_len"] * 3 // 4 // 2 * 2)).decode())
        out.append(d)
        if r["type"] == last_type:
            break
    return out


class FakeServer:
    """Scriptable fake Realtime endpoint; records every client event."""

    def __init__(self, on_event):
        self.on_event = on_event
        self.received = []
        self.headers = None

    async def handler(self, ws):
        self.headers = ws.request.headers
        await ws.send(json.dumps({"type": "session.created", "session": {"id": "sess_fake", "model": "gpt-realtime-2.1"}}))
        async for raw in ws:
            ev = json.loads(raw)
            self.received.append(ev)
            if ev["type"] == "session.update":
                await ws.send(json.dumps({"type": "session.updated", "session": dict(ev["session"], id="sess_fake")}))
            else:
                await self.on_event(ws, ev, self)

    async def __aenter__(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1/realtime"
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


class OfflineTests(unittest.IsolatedAsyncioTestCase):
    async def _open(self, srv, **cfg):
        a = OpenAIRealtimeAdapter(api_key="sk-test-secret", url=srv.url, ack_timeout=1)
        info = await a.open_session({"session_id": "s1", "instructions": "hi", "tools": [TOOL], **cfg})
        self.addAsyncCleanup(a.close, "test")
        return a, info

    async def test_session_update_and_capabilities(self):
        async def noop(ws, ev, srv): pass
        async with FakeServer(noop) as srv:
            a, info = await self._open(srv)
            su = srv.received[0]["session"]
            self.assertEqual(su["type"], "realtime")
            self.assertEqual(su["audio"]["input"]["format"], {"type": "audio/pcm", "rate": 24000})
            self.assertEqual(su["audio"]["output"]["format"], {"type": "audio/pcm", "rate": 24000})
            self.assertIsNone(su["audio"]["input"]["turn_detection"])
            self.assertEqual(su["tools"][0]["type"], "function")
            self.assertEqual(srv.headers["Authorization"], "Bearer sk-test-secret")
            self.assertEqual(info["provider_session_id"], "sess_fake")
            caps = a.capabilities()
            self.assertEqual(caps["provider"], "openai")
            self.assertEqual(caps["tool_delivery"], "gateway_bridge")
            self.assertEqual(caps["input_sample_rate"], 24000)
            self.assertIn(caps["interrupt_truncate"], ("supported", "unsupported"))

    async def test_tool_round_trip_replayed_from_log(self):
        replay = logged("s1", "response.created", "response.done")
        second = logged("s1", "response.created", "response.done")  # shape reused for the post-tool reply
        state = {"n": 0}

        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create":
                state["n"] += 1
                for e in (replay if state["n"] == 1 else second[:2] + second[-1:]):
                    await ws.send(json.dumps(e))
            elif ev["type"] == "conversation.item.create" and ev["item"]["type"] == "function_call_output":
                await ws.send(json.dumps({"type": "conversation.item.added", "item": ev["item"] | {"id": "item_out"}}))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("What is the balance for account ACC-001?")
            evs = await collect(a, lambda e: e.type == "turn_end")
            for e in evs:
                AdapterEventAdapter.validate_python(e.model_dump())
            types = [e.type for e in evs]
            self.assertEqual(types[0], "turn_start")
            self.assertIn("audio_out", types)
            self.assertIn("transcript_delta", types)
            self.assertIn("transcript_final", types)
            call = next(e for e in evs if e.type == "tool_call")
            self.assertEqual(call.name, "get_account_summary")
            self.assertEqual(call.arguments, {"account_id": "ACC-001"})
            usage = next(e for e in evs if e.type == "usage")
            self.assertGreater(usage.input_tokens, 0)
            self.assertTrue(all(e.pcm and e.sample_count == len(e.pcm) // 2 for e in evs if e.type == "audio_out"))

            with self.assertRaisesRegex(ValueError, "TOOL_RESULT_CORRELATION_FAILED"):
                await a.submit_tool_result(call.call_id, {"call_id": "other"})
            ack = await a.submit_tool_result(call.call_id, {"call_id": call.call_id, "balance": "1234.56"})
            self.assertEqual(ack["provider_acknowledgement"], "acknowledged")
            out = [e for e in srv.received if e["type"] == "conversation.item.create"][-1]["item"]
            self.assertEqual(out["type"], "function_call_output")
            self.assertEqual(out["call_id"], call.call_id)
            self.assertEqual(json.loads(out["output"])["balance"], "1234.56")
            await asyncio.sleep(0.1)
            self.assertEqual(srv.received[-1]["type"], "response.create")
            await collect(a, lambda e: e.type == "turn_end")  # drain the reply so teardown is quiet

    async def test_audio_in_validation_and_append(self):
        async def noop(ws, ev, srv): pass
        async with FakeServer(noop) as srv:
            a, _ = await self._open(srv)
            pcm = bytes(960)
            await a.send_audio(pcm, {"sample_count": 480, "sample_rate": 24000})
            await asyncio.sleep(0.05)
            ev = srv.received[-1]
            self.assertEqual(ev["type"], "input_audio_buffer.append")
            self.assertEqual(base64.b64decode(ev["audio"]), pcm)
            with self.assertRaisesRegex(ValueError, "INVALID_AUDIO"):
                await a.send_audio(pcm, {"sample_count": 100})
            with self.assertRaisesRegex(ValueError, "INVALID_AUDIO_RATE"):
                await a.send_audio(pcm, {"sample_count": 480, "sample_rate": 16000})

    async def test_vad_barge_in_interrupt_and_truncate(self):
        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create":
                await ws.send(json.dumps({"type": "response.created", "response": {"id": "resp_1"}}))
                await ws.send(json.dumps({"type": "response.output_item.added", "item": {"id": "item_a", "type": "message", "role": "assistant"}}))
                chunk = base64.b64encode(bytes(48000)).decode()  # 1 s at 24 kHz
                await ws.send(json.dumps({"type": "response.output_audio.delta", "item_id": "item_a", "delta": chunk}))
                await ws.send(json.dumps({"type": "input_audio_buffer.speech_started", "audio_start_ms": 0, "item_id": "item_u"}))
                await ws.send(json.dumps({"type": "response.done", "response": {"id": "resp_1", "status": "cancelled", "output": [], "usage": {"input_tokens": 5, "output_tokens": 2}}}))
            elif ev["type"] == "conversation.item.truncate":
                await ws.send(json.dumps({"type": "conversation.item.truncated", "item_id": ev["item_id"], "content_index": 0, "audio_end_ms": ev["audio_end_ms"]}))

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv, turn_detection="server_vad")
            self.assertEqual(srv.received[0]["session"]["audio"]["input"]["turn_detection"]["type"], "server_vad")
            await a.send_text("count")
            evs = await collect(a, lambda e: e.type == "turn_end")
            interrupts = [e for e in evs if e.type == "interrupted"]
            self.assertEqual(len(interrupts), 1)  # no duplicate from the cancelled response.done
            self.assertEqual((interrupts[0].item_id, interrupts[0].audio_end_ms), ("item_a", 1000))
            res = await a.interrupt("item_a", 500)
            self.assertEqual(res["history_truncation"], "acknowledged")
            t = [e for e in srv.received if e["type"] == "conversation.item.truncate"][0]
            self.assertEqual((t["item_id"], t["audio_end_ms"]), ("item_a", 500))
            self.assertNotIn("response.cancel", [e["type"] for e in srv.received])  # nothing in flight

    async def test_error_mapping_from_logged_errors_and_connection_loss(self):
        errs = [json.loads(r)["detail"] for r in LOG.read_text().splitlines()
                if json.loads(r)["type"] == "error" and json.loads(r)["dir"] == "in"]
        self.assertTrue(errs)

        async def on_event(ws, ev, srv):
            if ev["type"] == "response.create":
                for e in errs[:2]:
                    await ws.send(json.dumps(e))
                await ws.send(json.dumps({"type": "error", "error": {"type": "invalid_request_error", "code": "invalid_api_key", "message": "bad"}}))
                await ws.close()

        async with FakeServer(on_event) as srv:
            a, _ = await self._open(srv)
            await a.send_text("x")
            evs = [e async for e in a.receive()]  # stream ends after connection loss
            codes = [(e.code, e.fatal) for e in evs if e.type == "error"]
            self.assertEqual(codes[0], (errs[0]["error"]["code"], False))
            self.assertIn(("invalid_api_key", True), codes)
            self.assertTrue(any(c == "CONNECTION_CLOSED" and f for c, f in codes))

    async def test_auth_failure_maps_to_error(self):
        async def deny(connection, request):
            return connection.respond(401, "no\n")
        server = await websockets.serve(lambda ws: None, "127.0.0.1", 0, process_request=deny)
        try:
            port = server.sockets[0].getsockname()[1]
            a = OpenAIRealtimeAdapter(api_key="sk-x", url=f"ws://127.0.0.1:{port}/v1/realtime")
            with self.assertRaisesRegex(ValueError, "PROVIDER_AUTH_FAILED"):
                await a.open_session({"session_id": "s"})
            with self.assertRaisesRegex(ValueError, "PROVIDER_AUTH_MISSING"):
                await OpenAIRealtimeAdapter(api_key="", url="ws://x").open_session({"session_id": "s"})
        finally:
            server.close()
            await server.wait_closed()

    def test_redaction(self):
        ev = {"type": "input_audio_buffer.append", "audio": "AAAA" * 10}
        self.assertNotIn("AAAA", json.dumps(redact(ev)))
        d = {"type": "response.output_audio.delta", "delta": "QUJD"}
        self.assertNotIn("QUJD", json.dumps(redact(d)))
        t = {"type": "response.output_audio_transcript.delta", "delta": "hello"}
        self.assertEqual(redact(t)["delta"], "hello")
        self.assertNotIn("sk-", json.dumps(redact({"Authorization": "Bearer sk-abc"})))


class BargeInReplayTests(unittest.IsolatedAsyncioTestCase):
    """Offline replay of the live barge-in shape: long streamed answer, speech_started on caller audio,
    server cancels (response.done cancelled), truncate acknowledged."""

    def _server(self, ack_truncate=True, keep_streaming=False):
        state = {"cancelled": False}

        async def on_event(ws, ev, srv):
            t = ev["type"]
            if t == "response.create":
                async def stream():
                    await ws.send(json.dumps({"type": "response.created", "response": {"id": "resp_1"}}))
                    await ws.send(json.dumps({"type": "response.output_item.added",
                                              "item": {"id": "item_1", "type": "message", "role": "assistant"}}))
                    for _ in range(400):
                        if state["cancelled"] and not keep_streaming:
                            return
                        await ws.send(json.dumps({"type": "response.output_audio.delta", "item_id": "item_1",
                                                  "delta": base64.b64encode(bytes(3840)).decode()}))
                        await asyncio.sleep(0.01)
                srv.task = asyncio.ensure_future(stream())
            elif t == "input_audio_buffer.append" and not state["cancelled"]:
                state["cancelled"] = True
                await ws.send(json.dumps({"type": "input_audio_buffer.speech_started", "item_id": "u1"}))
                await ws.send(json.dumps({"type": "response.done", "response": {"id": "resp_1", "status": "cancelled"}}))
            elif t == "conversation.item.truncate" and ack_truncate:
                await ws.send(json.dumps({"type": "conversation.item.truncated", "item_id": ev["item_id"],
                                          "audio_end_ms": ev["audio_end_ms"]}))
        return FakeServer(on_event)

    async def _run(self, srv, bound=1000):
        from unittest import mock

        from adapter_conformance import _collect, _judge_interruption

        from voice_harness.adapters import openai_realtime as m

        a = OpenAIRealtimeAdapter(api_key="k", url=srv.url, ack_timeout=0.5)
        await a.open_session({"session_id": "s1"})
        self.addAsyncCleanup(a.close, "t")
        with mock.patch.object(m, "synth_caller_pcm", return_value=bytes(960 * 5)):
            info = await m._barge_in(a)
        evs = await _collect(a, lambda e: any(x.type == "interrupted" for x in e), 5)
        evs += await _collect(a, lambda _e: False, 0.4)
        return info, evs, await _judge_interruption(a, evs, info, bound), srv

    async def test_replay_passes(self):
        async with self._server() as srv:
            info, evs, res, srv = await self._run(srv)
            self.assertEqual(res["status"], "pass", res)
            self.assertEqual(sum(e.type == "interrupted" for e in evs), 1)
            self.assertEqual(res["truncate"]["history_truncation"], "acknowledged")
            self.assertLessEqual(res["truncate"]["audio_end_ms"], res["generated_audio_ms"])
            self.assertEqual(info["barge_in"], "synthetic_speech")
            td = [r for r in srv.received if r["type"] == "session.update"][-1]["session"]["audio"]["input"]["turn_detection"]
            self.assertTrue(td["interrupt_response"])
            self.assertFalse(td["create_response"])

    async def test_replay_fails_when_audio_keeps_flowing(self):
        async with self._server(keep_streaming=True) as srv:
            _, _, res, _ = await self._run(srv, bound=200)
            self.assertEqual(res["status"], "fail", res)
            self.assertIn("audio continued", res["reason"])

    async def test_replay_fails_when_truncate_unacknowledged(self):
        async with self._server(ack_truncate=False) as srv:
            _, _, res, _ = await self._run(srv)
            self.assertEqual(res["status"], "fail", res)
            self.assertIn("truncate not acknowledged", res["reason"])


@unittest.skipUnless(os.environ.get("OPENAI_API_KEY"), "OPENAI_API_KEY not set")
class LiveOpenAITests(unittest.IsolatedAsyncioTestCase):
    async def test_text_turn_with_tool_round_trip(self):
        a = OpenAIRealtimeAdapter()
        await a.open_session({"session_id": "live", "tools": [TOOL],
                              "instructions": "Banking test assistant. Use tools for accounts. One short sentence."})
        self.addAsyncCleanup(a.close, "live")
        await a.send_text("What is the balance for account ACC-001?")
        evs = await collect(a, lambda e: e.type == "turn_end", 40)
        call = next(e for e in evs if e.type == "tool_call")
        self.assertEqual(call.arguments.get("account_id"), "ACC-001")
        ack = await a.submit_tool_result(call.call_id, {"call_id": call.call_id, "balance": "1234.56", "currency": "GBP"})
        self.assertEqual(ack["provider_acknowledgement"], "acknowledged")
        evs = await collect(a, lambda e: e.type == "turn_end", 40)
        self.assertTrue(any(e.type == "audio_out" and e.sample_rate == 24000 for e in evs))
        self.assertTrue(any(e.type == "transcript_final" and e.role == "assistant" for e in evs))
        self.assertTrue(any(e.type == "usage" for e in evs))


if __name__ == "__main__":
    unittest.main()
