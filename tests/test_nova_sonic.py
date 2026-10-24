"""Offline Nova Sonic adapter tests replaying raw Phase 0 probe logs; live test is skipped without creds."""
import asyncio
import base64
import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path

from voice_harness.adapters import CapabilityUnsupported
from voice_harness.adapters.nova_sonic import NovaSonicAdapter, map_error

ROOT = Path(__file__).resolve().parents[1]
P0 = ROOT / "artifacts" / "phase0"
TOOL_LOG = P0 / "nova-probe-2026-10-16T22-21-40-853Z.jsonl"
BARGE_LOG = P0 / "nova-probe-2026-10-16T22-28-14-393Z.jsonl"
ENV = ROOT / ".local" / "aws-session.env"
EVENT_TYPES = {"completionStart", "completionEnd", "contentStart", "contentEnd", "textOutput",
               "audioOutput", "toolUse", "usageEvent"}


def inbound(path, session):
    """Raw-log inbound records as transport messages; redacted audio payloads are synthesised."""
    msgs = []
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if r["session"] != session or r["dir"] != "in":
            continue
        t, d = r["type"], r["detail"]
        if t == "stream_open":
            msgs.append({"_open_ok": {"requestId": d.get("requestId")}})
        elif t == "stream_error":
            msgs.append({"_error": d})
        elif t == "audioOutput":
            n = d["bytes_b64"] // 4 * 3
            msgs.append({"event": {"audioOutput": {"content": base64.b64encode(b"\x01\x00" * (n // 2)).decode()}}})
        elif t in EVENT_TYPES:
            msgs.append({"event": {t: d}})
    return msgs


class ReplayTransport:
    def __init__(self, msgs, hold_open=False):
        self.msgs, self.sent, self.hold_open = list(msgs), [], hold_open
        self.closed = False

    async def start(self):
        pass

    async def send(self, msg):
        self.sent.append(msg)

    async def recv(self):
        if self.msgs:
            await asyncio.sleep(0)
            return self.msgs.pop(0)
        while self.hold_open and not self.closed:
            await asyncio.sleep(0.01)
        return None

    async def close(self):
        self.closed = True


async def drain(adapter, timeout=5):
    out = []

    async def go():
        async for ev in adapter.receive():
            out.append(ev)
    await asyncio.wait_for(go(), timeout)
    return out


def sent_types(tr):
    return [next(iter(m["event"])) for m in tr.sent if "event" in m]


class NovaOffline(unittest.IsolatedAsyncioTestCase):
    async def open(self, msgs, **kw):
        tr = ReplayTransport(msgs, **kw)
        a = NovaSonicAdapter(pump_interval_s=0.001)
        await a.open_session({"session_id": "S1", "transport": tr, "system_prompt": "x",
                              "tools": [{"name": "get_account_summary", "description": "d",
                                         "input_schema": {"type": "object"}}]})
        return a, tr

    async def test_session_handshake_and_silence_pump(self):
        a, tr = await self.open([], hold_open=True)
        await asyncio.sleep(0.05)
        await a.close("done")
        types = sent_types(tr)
        self.assertEqual(types[:7], ["sessionStart", "promptStart", "contentStart", "textInput",
                                     "contentEnd", "contentStart", "audioInput"])
        audio = [m["event"]["audioInput"] for m in tr.sent if "audioInput" in m.get("event", {})]
        self.assertGreater(len(audio), 3)
        self.assertEqual(len(base64.b64decode(audio[0]["content"])), 640)  # 20 ms @16 kHz s16
        self.assertEqual(types[-3:], ["contentEnd", "promptEnd", "sessionEnd"])
        aud_start = [m for m in tr.sent if m.get("event", {}).get("contentStart", {}).get("type") == "AUDIO"][0]
        cs = aud_start["event"]["contentStart"]
        self.assertTrue(cs["interactive"])
        self.assertEqual(cs["audioInputConfiguration"]["sampleRateHertz"], 16000)

    async def test_caller_audio_resampled_24k_to_16k(self):
        a, tr = await self.open([], hold_open=True)
        for _ in range(5):  # 100 ms @24k; the streaming filter withholds a short startup delay
            await a.send_audio(b"\x10\x00" * 480, {"sample_count": 480, "sample_rate": 24000})
        await asyncio.sleep(0.15)
        await a.close("done")
        chunks = [base64.b64decode(m["event"]["audioInput"]["content"]) for m in tr.sent
                  if "audioInput" in m.get("event", {})]
        loud = [c for c in chunks if any(c)]
        self.assertGreaterEqual(len(loud), 3)
        self.assertTrue(all(len(c) == 640 for c in chunks))
        self.assertGreaterEqual(a.stats["audio_chunks"], 3)

    async def test_tool_flow_replay(self):
        a, tr = await self.open(inbound(TOOL_LOG, "s1"))
        evs = await drain(a)
        by = {}
        for e in evs:
            by.setdefault(e.type, []).append(e)
        call = by["tool_call"][0]
        self.assertEqual((call.name, call.arguments), ("get_account_summary", {"account_id": "ACC-001"}))
        self.assertEqual(call.call_id, "chatcmpl-tool-88c38ec01a9847f1")
        self.assertTrue(by["audio_out"] and by["audio_out"][0].sample_rate == 24000)
        self.assertEqual(by["audio_out"][0].sample_count, len(by["audio_out"][0].pcm) // 2)
        self.assertTrue(any("balance" in e.text for e in by.get("transcript_delta", []) + by.get("transcript_final", [])))
        self.assertTrue(by["usage"] and by["usage"][-1].input_tokens > 0)
        self.assertTrue(by["turn_start"] and by["turn_end"])
        self.assertNotIn("error", by)
        await a.close("done")

    async def test_tool_result_echoes_exact_tool_use_id(self):
        a, tr = await self.open([], hold_open=True)
        r = await a.submit_tool_result("chatcmpl-tool-88c38ec01a9847f1", {"status": "success", "data": {"b": 1}})
        self.assertEqual(r["local_send"], "accepted")
        cs = [m["event"]["contentStart"] for m in tr.sent if "contentStart" in m.get("event", {})
              and m["event"]["contentStart"]["type"] == "TOOL"][0]
        self.assertEqual(cs["toolResultInputConfiguration"]["toolUseId"], "chatcmpl-tool-88c38ec01a9847f1")
        self.assertEqual(cs["role"], "TOOL")
        self.assertIn("toolResult", sent_types(tr))
        with self.assertRaises(ValueError):
            await a.submit_tool_result("a", {"call_id": "b"})
        await a.close("done")

    async def test_unknown_tool_use_id_error_mapped(self):
        msgs = [m for m in inbound(P0 / "nova-probe-2026-10-16T22-21-40-853Z.jsonl", "s3") if "_error" in m]
        self.assertTrue(msgs, "raw log has no s3 error")
        a, _ = await self.open(msgs)
        evs = await drain(a)
        self.assertEqual(evs[0].type, "error")
        self.assertEqual(evs[0].code, "TOOL_RESULT_UNKNOWN_ID")
        self.assertTrue(evs[0].fatal)

    async def test_error_mapping(self):
        self.assertEqual(map_error("ValidationException", "Timed out waiting for audio bytes")[0], "AUDIO_TIMEOUT")
        self.assertEqual(map_error("ValidationException", "Prompt x must have at least one audio content")[0],
                         "TEXT_ONLY_PROMPT_REJECTED")
        self.assertEqual(map_error("ThrottlingException", "")[0], "RATE_LIMITED")
        self.assertEqual(map_error("ExpiredTokenException", "")[0], "AUTH_EXPIRED")
        self.assertEqual(map_error("ValidationException", "other")[0], "VALIDATION_ERROR")

    async def test_barge_in_replay_single_interrupted_per_content(self):
        a, _ = await self.open(inbound(BARGE_LOG, "s1"))
        evs = await drain(a)
        ints = [e for e in evs if e.type == "interrupted"]
        self.assertGreaterEqual(len(ints), 1)
        ids = [e.item_id for e in ints]
        self.assertEqual(len(ids), len(set(ids)))  # marker + INTERRUPTED stopReason deduplicated
        # the in-band marker is never leaked as transcript text
        for e in evs:
            if e.type.startswith("transcript"):
                self.assertNotIn("interrupted", e.text)
        users = [e for e in evs if e.type == "transcript_final" and e.role == "user"]
        self.assertTrue(any("stop" in e.text for e in users))

    async def test_interrupt_signal_variants(self):
        base = {"completionId": "c", "contentId": "k1"}
        a, _ = await self.open([
            {"event": {"contentStart": {**base, "role": "ASSISTANT", "type": "TEXT"}}},
            {"event": {"contentEnd": {**base, "stopReason": "INTERRUPTED", "type": "TEXT"}}},
            {"event": {"contentStart": {**base, "contentId": "k2", "role": "ASSISTANT", "type": "TEXT"}}},
            {"event": {"textOutput": {**base, "contentId": "k2", "role": "ASSISTANT",
                                      "content": "{ \"interrupted\" : true }"}}},
        ])
        evs = await drain(a)
        self.assertEqual([e.item_id for e in evs if e.type == "interrupted"], ["k1", "k2"])

    async def test_interrupt_truncate_unsupported_and_capabilities(self):
        a = NovaSonicAdapter()
        with self.assertRaises(CapabilityUnsupported):
            await a.interrupt("item", 100)
        c = a.capabilities()
        self.assertEqual(c["interrupt_truncate"], "unsupported")
        self.assertEqual(c["interrupt_detection"], "supported")
        self.assertEqual(c["cancel_truncate_status"], "observed")
        self.assertEqual(c["model_id"], "amazon.nova-2-5-sonic")
        self.assertEqual(c["capability_rows"]["cancel_truncate"]["status"], "observed")
        self.assertEqual(c["capability_rows"]["audio_in"]["status"], "verified")
        self.assertEqual(c["capability_rows"]["audio_alignment"]["status"], "unknown")
        self.assertEqual(c["tool_delivery"], "gateway_bridge")
        self.assertIsNone((await a.cancel_tool("x", "r"))["cancelled"] or None)

    async def test_send_text_interactive_content(self):
        a, tr = await self.open([], hold_open=True)
        await a.send_text("hello")
        await a.close("done")
        cs = [m["event"]["contentStart"] for m in tr.sent if m.get("event", {}).get("contentStart", {}).get("role") == "USER"
              and m["event"]["contentStart"]["type"] == "TEXT"][0]
        self.assertTrue(cs["interactive"])


def _creds_live():
    if not ENV.exists():
        return False
    txt = ENV.read_text()
    for line in txt.splitlines():
        if line.startswith("# expires "):
            return datetime.fromisoformat(line.split()[2]) > datetime.now(UTC)
    return False


@unittest.skipUnless(_creds_live() and os.environ.get("NOVA_LIVE") == "1",
                     "live Nova test: set NOVA_LIVE=1 with unexpired .local/aws-session.env "
                     "(refresh via `! ./scripts/aws-session.sh <MFA code>`)")
class AcknowledgingTransport(ReplayTransport):
    acknowledges_open = True


class NovaOpenAcknowledgement(unittest.IsolatedAsyncioTestCase):
    """Session setup time must include Bedrock accepting the stream, not just starting the local bridge."""

    async def open(self, msgs, **config):
        tr = AcknowledgingTransport(msgs, hold_open=True)
        a = NovaSonicAdapter(pump_interval_s=0.001)
        await a.open_session({"session_id": "S1", "transport": tr, "system_prompt": "x", **config})
        return a

    async def test_open_waits_for_the_bridge_to_report_the_stream_open(self):
        a = await self.open([{"_open_ok": {"requestId": "req-1"}}])
        self.assertEqual(a.stats["request_id"], "req-1")
        self.assertNotIn("open_unacknowledged", a.stats)
        await a.close("done")

    async def test_an_unacknowledged_open_is_recorded_and_not_fatal(self):
        a = await self.open([], open_timeout=0.05)
        self.assertTrue(a.stats["open_unacknowledged"])
        await a.close("done")


class NovaLive(unittest.IsolatedAsyncioTestCase):
    async def test_live_tool_roundtrip(self):
        env = {}
        for line in ENV.read_text().splitlines():
            line = line.removeprefix("export ")
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip("'\"")
        a = NovaSonicAdapter()
        await a.open_session({"session_id": "live", "env": env, "system_prompt": "Be brief. Use tools for accounts.",
                              "tools": [{"name": "get_account_summary", "description": "Account summary",
                                         "input_schema": {"type": "object", "properties": {"account_id": {"type": "string"}},
                                                          "required": ["account_id"]}}]})
        await a.send_text("What is the balance for account ACC-001?")
        seen, audio = [], 0

        async def go():
            nonlocal audio
            async for ev in a.receive():
                seen.append(ev.type)
                if ev.type == "tool_call":
                    await a.submit_tool_result(ev.call_id, {"status": "success", "data": {"balance": "1234.56"}})
                if ev.type == "audio_out":
                    audio += 1
                # completionEnd can lag well behind the final assistant transcript
                if ev.type == "transcript_final" and ev.role == "assistant" and audio and "tool_call" in seen:
                    break
                if ev.type == "error":
                    self.fail(ev)
        try:
            await asyncio.wait_for(go(), 60)
        finally:
            await a.close("test")
        self.assertIn("tool_call", seen)
        self.assertGreater(audio, 0)
        self.assertIn("usage", seen)


if __name__ == "__main__":
    unittest.main()
