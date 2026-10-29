"""Phase 0 live probe for the ElevenLabs Agents conversation WebSocket (spec section 3 procedure).

Usage (key from ``ELEVENLABS_API_KEY``, stored agent from ``elevenlabs_agent setup``; neither is printed or logged):
    PYTHONPATH=src python scripts/probe/elevenlabs_probe.py [--no-speech]

Writes a redacted raw event log (``elevenlabs-probe-<ts>.jsonl``) and observations JSON
(``elevenlabs-observations-<ts>.json``) to artifacts/phase0/. Audio payloads are stored as length + hash only.
Build the report with ``python -m voice_harness.probe from-elevenlabs-observations <observations.json> --report-id <id>``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import websockets

from voice_harness.adapters.elevenlabs_agent import DEFAULT_VOICE, KEY_ENV, agent_id

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "phase0"
URL = "wss://api.elevenlabs.io/v1/convai/conversation"
INSTRUCTIONS = "Banking test assistant. Keep answers to one short sentence unless asked for more. Use tools for accounts."
CHUNK = 960  # 20 ms of 24 kHz s16le


class Probe:
    def __init__(self):
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
        OUT.mkdir(parents=True, exist_ok=True)
        self.log_path = OUT / f"elevenlabs-probe-{stamp}.jsonl"
        self.obs_path = OUT / f"elevenlabs-observations-{stamp}.json"
        self.seq = 0
        self.evidence: dict[str, list[str]] = {}
        self.notes: dict[str, list[str]] = {}
        self.meta: dict = {"startedAt": datetime.now(UTC).isoformat(), "python": sys.version.split()[0],
                           "websockets": websockets.__version__}

    def mark(self, row: str, ev_id: str, note: str | None = None) -> None:
        self.evidence.setdefault(row, []).append(ev_id)
        if note:
            self.notes.setdefault(row, []).append(note)

    def note(self, row: str, text: str) -> None:
        self.notes.setdefault(row, []).append(text)

    @staticmethod
    def redact(ev: dict) -> dict:
        out = json.loads(json.dumps(ev))
        for holder, key in ((out.get("audio_event"), "audio_base_64"), (out, "user_audio_chunk")):
            if isinstance(holder, dict) and isinstance(holder.get(key), str):
                holder[key] = {"b64_len": len(holder[key]), "sha": hashlib.sha256(holder[key].encode()).hexdigest()[:16]}
        return out

    def log(self, session: str, direction: str, etype: str, detail) -> str:
        self.seq += 1
        ev_id = f"el_ev_{self.seq}"
        with self.log_path.open("a") as f:
            f.write(json.dumps({"id": ev_id, "t": datetime.now(UTC).isoformat(), "session": session, "dir": direction,
                                "type": etype, "detail": detail}) + "\n")
        return ev_id


class Session:
    def __init__(self, probe: Probe, label: str):
        self.p, self.label = probe, label
        self.events: list[tuple[str, dict, float]] = []
        self.ws = None

    async def open(self) -> dict | None:
        self.ws = await websockets.connect(f"{URL}?agent_id={agent_id()}", additional_headers={
            "xi-api-key": os.environ[KEY_ENV]}, max_size=None, open_timeout=15)
        self.reader = asyncio.create_task(self._read())
        await self.send({"type": "conversation_initiation_client_data", "conversation_config_override": {
            "agent": {"first_message": "", "language": "en", "prompt": {"prompt": INSTRUCTIONS}},
            "tts": {"voice_id": DEFAULT_VOICE}}})
        return await self.wait(lambda e: e.get("type") == "conversation_initiation_metadata", 15)

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                ev = json.loads(raw)
                if ev.get("type") == "ping":
                    await self.ws.send(json.dumps({"type": "pong", "event_id": (ev.get("ping_event") or {}).get("event_id")}))
                    continue
                ev_id = self.p.log(self.label, "in", ev.get("type", "?"), self.p.redact(ev))
                self.events.append((ev_id, ev, time.monotonic()))
        except Exception as e:  # noqa: BLE001
            self.p.log(self.label, "in", "ws_closed", {"error": type(e).__name__})

    async def send(self, ev: dict) -> str:
        i = self.p.log(self.label, "out", ev.get("type", "user_audio_chunk"), self.p.redact(ev))
        await self.ws.send(json.dumps(ev))
        return i

    async def wait(self, pred, timeout: float = 30.0, start: int = 0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for i, (ev_id, ev, t) in enumerate(self.events[start:], start):
                if pred(ev):
                    return ev_id, ev, t, i
            await asyncio.sleep(0.05)
        return None

    async def speak(self, pcm: bytes, trail_s: float = 2.0) -> str:
        first = None
        for o in range(0, len(pcm), CHUNK):
            i = await self.send({"user_audio_chunk": base64.b64encode(pcm[o:o + CHUNK]).decode()})
            first = first or i
            await asyncio.sleep(0.02)
        for _ in range(int(trail_s * 50)):  # the server hears the caller stop only from silence
            await self.ws.send(json.dumps({"user_audio_chunk": base64.b64encode(bytes(CHUNK)).decode()}))
            await asyncio.sleep(0.02)
        return first

    async def close(self) -> None:
        await self.ws.close()
        self.reader.cancel()


async def main(speech: bool) -> int:
    for k in (KEY_ENV,):
        if not os.environ.get(k):
            print(f"{k} not set", file=sys.stderr)
            return 2
    if not agent_id():
        print("no stored agent: run python -m voice_harness.adapters.elevenlabs_agent setup", file=sys.stderr)
        return 2
    p = Probe()

    s1 = Session(p, "s1")
    meta = await s1.open()
    if not meta:
        print("no conversation_initiation_metadata", file=sys.stderr)
        return 1
    m = meta[1].get("conversation_initiation_metadata_event") or {}
    p.meta["initiationMetadata"] = {k: v for k, v in m.items() if k != "conversation_id"}
    p.mark("input_codecs_rates", meta[0], f"user_input_audio_format={m.get('user_input_audio_format')}")
    p.mark("output_codecs_rates", meta[0], f"agent_output_audio_format={m.get('agent_output_audio_format')}")
    p.note("model_discovery", "the metadata event names no model or LLM; the agent's configured LLM is not observable on the socket")

    # text turn -> client tool call(s) -> tool result -> spoken reply
    start = len(s1.events)
    await s1.send({"type": "user_message", "text": "Use your policy tool to look up the identity verification policy and tell me what it says."})
    answered = False
    for _ in range(4):
        call = await s1.wait(lambda e: e.get("type") == "client_tool_call", 25, start)
        if not call:
            break
        c = call[1]["client_tool_call"]
        p.mark("tool_arguments_and_results", call[0], f"client_tool_call {c.get('tool_name')} {json.dumps(c.get('parameters'))[:120]}")
        after = call[3] + 1
        r = await s1.send({"type": "client_tool_result", "tool_call_id": c["tool_call_id"], "is_error": False,
                           "result": json.dumps({"policy_id": "identity", "version": 1, "text": "Callers must pass identity verification before any account details are shared."})})
        p.mark("tool_arguments_and_results", r, "client_tool_result sent by tool_call_id")
        ack = await s1.wait(lambda e: e.get("type") == "agent_tool_response", 5, after)
        if ack:
            p.mark("tool_arguments_and_results", ack[0], "agent_tool_response echoed the result (an acknowledgement, not a delivery guarantee)")
        resp = await s1.wait(lambda e: e.get("type") == "agent_response", 25, after)
        start = after
        if resp:
            p.mark("transcript_finality", resp[0], "agent_response carries final text only; no deltas")
            p.mark("turn_signalling", resp[0], "no response start/end marker: agent_response is the only text-level turn boundary")
            answered = True
            break
    if not answered:
        p.note("tool_arguments_and_results", "no agent_response after the tool round trip within the allowed attempts")
    aud = await s1.wait(lambda e: e.get("type") == "audio", 5)
    if aud:
        p.mark("audio_out", aud[0], "audio events carry audio_base_64 (pcm_24000)")
    await asyncio.sleep(1.5)

    if speech:
        from voice_harness.adapters.openai_realtime import synth_caller_pcm

        pcm = synth_caller_pcm("What is the balance for account ACC-001?", trail_ms=200)
        start = len(s1.events)
        first = await s1.speak(pcm)
        ut = await s1.wait(lambda e: e.get("type") == "user_transcript", 15, start)
        if ut:
            text = (ut[1].get("user_transcription_event") or {}).get("user_transcript", "")
            p.mark("audio_in", first, "synthetic speech streamed as user_audio_chunk frames")
            p.mark("audio_in", ut[0], f"user_transcript received: {text.strip()[:80]!r}")
            p.mark("transcript_finality", ut[0], "user_transcript is final text only")
        else:
            p.note("audio_in", "no user_transcript for the synthetic speech")
        await asyncio.sleep(6)  # let any reply settle

        # barge-in: long answer, then speech while the agent is talking
        start = len(s1.events)
        await s1.send({"type": "user_message", "text": "Explain in at least twelve sentences how a current account differs from a savings account."})
        talking = await s1.wait(lambda e: e.get("type") == "audio", 20, start)
        if talking:
            await asyncio.sleep(0.3)
            mark = len(s1.events)
            await s1.speak(synth_caller_pcm("Stop. Please stop talking, I have a question.", trail_ms=200), trail_s=1.0)
            itr = await s1.wait(lambda e: e.get("type") == "interruption", 10, mark)
            if itr:
                p.mark("cancel_truncate", itr[0], "server interruption event on caller speech during agent audio")
                p.mark("turn_signalling", itr[0], "interruption ends the in-flight agent response")
            else:
                p.note("cancel_truncate", "no interruption event within 10 s of barge-in speech")
        await asyncio.sleep(2)
    p.note("cancel_truncate", "no client cancel or truncate message exists; the adapter declares interrupt_truncate unsupported")

    seen = sorted({e.get("type") for _, e, _ in s1.events})
    p.meta["eventTypes"] = seen
    if not any("usage" in t or "cost" in t for t in seen):
        p.note("usage_granularity", f"no usage or cost event among {seen}; usage is not reported on the socket")
    else:
        p.mark("usage_granularity", next(i for i, e, _ in s1.events if "usage" in e.get("type", "") or "cost" in e.get("type", "")), "usage event seen")
    p.note("audio_alignment", "no alignment or timing fields were examined; none are used")
    n = len(s1.events)
    await s1.send({"type": "not_a_real_event"})
    await asyncio.sleep(2)
    errs = [(i, e) for i, e, _ in s1.events[n:] if e.get("type") in ("error", "internal_error")]
    p.note("error_handling", f"unknown client event type -> {errs[0][1].get('type') if errs else 'no reply'}; connection {'open' if s1.ws.state.name == 'OPEN' else 'closed'}")
    await s1.close()

    # reconnect: a fresh connection to the same agent works; resume is not exercised
    s2 = Session(p, "s2")
    m2 = await s2.open()
    if m2:
        p.mark("reconnect_resume", m2[0], "fresh connection after close succeeded; no resume message exists in the documented protocol or was tried")
    await s2.close()

    p.obs_path.write_text(json.dumps({"meta": p.meta, "evidence": p.evidence, "notes": p.notes,
                                      "log_file": str(p.log_path.relative_to(ROOT)),
                                      "flags": {"sdk": f"websockets {websockets.__version__} python {p.meta['python']}"}}, indent=2))
    print("observations:", p.obs_path)
    print({"evidenceRows": {k: len(v) for k, v in p.evidence.items()}})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-speech", action="store_true")
    sys.exit(asyncio.run(main(not ap.parse_args().no_speech)))
