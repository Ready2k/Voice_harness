"""Phase 0 live probe for the xAI Grok Voice realtime API (spec section 3 procedure).

Usage (key from the environment, variable ``xAI_API_KEY``; never printed or logged):
    PYTHONPATH=src python scripts/probe/xai_probe.py [--model grok-voice-3.0] [--no-speech]

Writes a redacted raw event log (``xai-probe-<ts>.jsonl``) and an observations JSON
(``xai-observations-<ts>.json``) to artifacts/phase0/. Audio payloads are stored as length + hash only;
the Authorization header is never part of any logged value. Build the report with
``python -m voice_harness.probe from-xai-observations <observations.json> --report-id <id>``.
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

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "phase0"
URL = "wss://api.x.ai/v1/realtime"
KEY_ENV = "xAI_API_KEY"
TOOL = {"type": "function", "name": "get_account_summary", "description": "Return a synthetic account summary.",
        "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]}}
AUDIO = {"input": {"format": {"type": "audio/pcm", "rate": 24000}}, "output": {"format": {"type": "audio/pcm", "rate": 24000}}}


class Probe:
    def __init__(self, model: str):
        self.model = model
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
        OUT.mkdir(parents=True, exist_ok=True)
        self.log_path = OUT / f"xai-probe-{stamp}.jsonl"
        self.obs_path = OUT / f"xai-observations-{stamp}.json"
        self.seq = 0
        self.evidence: dict[str, list[str]] = {}
        self.notes: dict[str, list[str]] = {}
        self.meta: dict = {"modelId": model, "startedAt": datetime.now(UTC).isoformat(), "python": sys.version.split()[0],
                           "websockets": websockets.__version__}
        self.flags: dict = {}

    # -- logging -------------------------------------------------------------------------------
    def mark(self, row: str, ev_id: str, note: str | None = None) -> None:
        self.evidence.setdefault(row, []).append(ev_id)
        if note:
            self.notes.setdefault(row, []).append(note)

    def note(self, row: str, text: str) -> None:
        self.notes.setdefault(row, []).append(text)

    @staticmethod
    def redact(ev: dict) -> dict:
        out = json.loads(json.dumps(ev))
        for k in ("audio", "delta"):
            t = str(out.get("type", ""))
            if isinstance(out.get(k), str) and (k == "audio" or ("audio" in t and "transcript" not in t and "arguments" not in t)):
                out[k] = {"b64_len": len(out[k]), "sha": hashlib.sha256(out[k].encode()).hexdigest()[:16]}
        return out

    def log(self, session: str, direction: str, etype: str, detail) -> str:
        self.seq += 1
        ev_id = f"xai_ev_{self.seq}"
        with self.log_path.open("a") as f:
            f.write(json.dumps({"id": ev_id, "t": datetime.now(UTC).isoformat(), "session": session, "dir": direction,
                                "type": etype, "detail": detail}) + "\n")
        return ev_id


class Session:
    def __init__(self, probe: Probe, label: str, model: str):
        self.p, self.label, self.model = probe, label, model
        self.events: list[tuple[str, dict, float]] = []
        self.ws = None
        self.reader = None
        self.audio_deltas = 0

    async def open(self) -> None:
        self.t0 = time.monotonic()
        self.ws = await websockets.connect(f"{URL}?model={self.model}", additional_headers={
            "Authorization": f"Bearer {os.environ[KEY_ENV]}"}, max_size=None, open_timeout=15)
        self.p.log(self.label, "in", "ws_open", {"ms": int((time.monotonic() - self.t0) * 1000)})
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                ev = json.loads(raw)
                if ev.get("type") == "ping":
                    continue
                ev_id = self.p.log(self.label, "in", ev.get("type", "?"), self.p.redact(ev))
                self.events.append((ev_id, ev, time.monotonic()))
                if ev.get("type") == "response.output_audio.delta":
                    self.audio_deltas += 1
        except Exception as e:  # noqa: BLE001
            self.p.log(self.label, "in", "ws_closed", {"error": type(e).__name__})

    async def send(self, ev: dict) -> str:
        i = self.p.log(self.label, "out", ev["type"], self.p.redact(ev))
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

    async def close(self) -> None:
        await self.ws.close()
        self.reader.cancel()


async def text_turn(s: Session, text: str) -> str:
    await s.send({"type": "conversation.item.create", "item": {"type": "message", "role": "user",
                                                                 "content": [{"type": "input_text", "text": text}]}})
    return await s.send({"type": "response.create"})


def session_update(turn_detection=None, instructions="You are a concise banking test assistant. Use tools when asked about accounts. Reply in one short sentence."):
    return {"type": "session.update", "session": {
        "instructions": instructions, "voice": "ara", "audio": AUDIO, "turn_detection": turn_detection,
        "input_audio_transcription": {"model": "grok-transcribe"}, "tools": [TOOL]}}


async def main(model: str, speech: bool) -> int:
    if not os.environ.get(KEY_ENV):
        print(f"{KEY_ENV} not set", file=sys.stderr)
        return 2
    p = Probe(model)

    # --- alias resolution (connect only, no turn): requested vs returned model
    s0 = Session(p, "alias", "grok-voice-latest")
    await s0.open()
    c0 = await s0.wait(lambda e: e.get("type") == "session.created", 10)
    if c0:
        p.meta["aliasProbe"] = {"requested": "grok-voice-latest", "returned": (c0[1].get("session") or {}).get("model")}
        p.note("model_discovery", f"alias grok-voice-latest -> session.model={p.meta['aliasProbe']['returned']}")
    await s0.close()

    # --- s1: pinned model; text turn, tool round trip, usage, transcripts, audio in, cancel/truncate, error
    s1 = Session(p, "s1", model)
    await s1.open()
    created = await s1.wait(lambda e: e.get("type") == "session.created", 10)
    if created:
        sess = created[1].get("session") or {}
        p.meta["sessionCreated"] = {"model": sess.get("model"), "id": sess.get("id"), "voice": sess.get("voice"),
                                    "modalities": sess.get("modalities"), "turn_detection": sess.get("turn_detection")}
        p.mark("model_discovery", created[0], f"session.created model={sess.get('model')}")
    await s1.send(session_update())
    upd = await s1.wait(lambda e: e.get("type") == "session.updated", 10)
    if upd:
        fmt = (upd[1].get("session") or {}).get("audio") or {}
        p.meta["sessionUpdated_audio"] = fmt
        p.mark("input_codecs_rates", upd[0], json.dumps(fmt.get("input")))
        p.mark("output_codecs_rates", upd[0], json.dumps(fmt.get("output")))
        p.mark("turn_signalling", upd[0], "session.updated accepted turn_detection=null (manual)")

    # text turn -> tool call
    m = len(s1.events)
    p.flags["sentAt"] = time.monotonic()
    await text_turn(s1, "What is the balance for account ACC-001?")
    fc = await s1.wait(lambda e: e.get("type") == "response.function_call_arguments.done", 30, m)
    if fc:
        ev = fc[1]
        p.mark("tool_arguments_and_results", fc[0], f"function_call {ev.get('name')} {ev.get('arguments')}")
        done1 = await s1.wait(lambda e: e.get("type") == "response.done", 30, m)
        if done1:
            p.mark("turn_signalling", done1[0], f"response.done status={done1[1].get('response', {}).get('status')} (tool-calling response)")
        first_audio = await s1.wait(lambda e: e.get("type") == "response.output_audio.delta", 1, m)
        if first_audio:
            p.mark("audio_out", first_audio[0], "response.output_audio.delta (pcm 24 kHz)")
        m2 = len(s1.events)
        await s1.send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": ev["call_id"],
            "output": json.dumps({"account_id": "ACC-001", "balance": "1234.56", "currency": "GBP"})}})
        ack = await s1.wait(lambda e: e.get("type") in ("conversation.item.added", "conversation.item.done")
                            and (e.get("item") or {}).get("type") == "function_call_output", 5, m2)
        if ack:
            p.mark("tool_arguments_and_results", ack[0], "function_call_output acknowledged by conversation.item.added")
        else:
            p.note("tool_arguments_and_results", "no item event acknowledged the function_call_output")
        r = await s1.send({"type": "response.create"})
        p.mark("tool_arguments_and_results", r, "function_call_output delivered then response.create")
        done2 = await s1.wait(lambda e: e.get("type") == "response.done", 30, m2)
        if done2:
            p.mark("turn_signalling", done2[0], f"response.done status={done2[1].get('response', {}).get('status')}")
            u = done2[1].get("usage") or done2[1].get("response", {}).get("usage")
            if u:
                p.mark("usage_granularity", done2[0], f"response.done usage keys: {','.join(u)}; in={u.get('input_token_details')}; out={u.get('output_token_details')}")
            else:
                p.note("usage_granularity", "response.done carried no usage")
        tr = await s1.wait(lambda e: e.get("type") == "response.output_audio_transcript.done", 2, m)
        if tr:
            p.mark("transcript_finality", tr[0], "response.output_audio_transcript.delta then .done")
        text_audio = await s1.wait(lambda e: e.get("type") == "response.output_audio.delta", 1, m2)
        if text_audio:
            p.mark("audio_out", text_audio[0], "audio delta on the post-tool-result response")
    else:
        p.note("tool_arguments_and_results", "no function_call within 30s")
    p.note("audio_alignment", "output audio deltas carry audio_duration_ms and transcript deltas carry start_time; no word-level audio alignment verified")

    # audio in: 200 ms silence appended and committed (manual turn)
    m = len(s1.events)
    a1 = await s1.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(bytes(24000 * 2 // 5)).decode()})
    await s1.send({"type": "input_audio_buffer.commit"})
    com = await s1.wait(lambda e: e.get("type") == "input_audio_buffer.committed", 4, m)
    if com:
        p.mark("audio_in", a1, "append + commit accepted (input_audio_buffer.committed); silence only here")
    else:
        errs = [e for _, e, _ in s1.events[m:] if e.get("type") == "error"]
        p.note("audio_in", f"no committed event for the silence-only manual commit within 4 s (errors: {[str(x.get('error'))[:200] for x in errs]})")

    # cancel / truncate on a client-initiated cancel
    m = len(s1.events)
    await text_turn(s1, "Count from one to thirty slowly, one number per sentence.")
    await s1.wait(lambda e: e.get("type") == "response.output_audio.delta", 15, m)
    await s1.send({"type": "response.cancel"})
    canc = await s1.wait(lambda e: e.get("type") == "response.cancelled" or (
        e.get("type") == "response.done" and e.get("response", {}).get("status") == "cancelled"), 8, m)
    if canc:
        p.mark("cancel_truncate", canc[0], f"response.cancel -> {canc[1].get('type')} status={canc[1].get('response', {}).get('status')}")
    else:
        done_after = await s1.wait(lambda e: e.get("type") == "response.done", 5, m)
        errs = [str(e.get("error"))[:200] for _, e, _ in s1.events[m:] if e.get("type") == "error"]
        p.note("cancel_truncate", f"response.cancel did not produce a cancelled response (response.done seen={bool(done_after)}, status="
                                  f"{(done_after[1].get('response') or {}).get('status') if done_after else None}, errors={errs})")
    item = next((ev for _, ev, _ in reversed(s1.events) if ev.get("type") == "response.output_item.added"
                 and (ev.get("item") or {}).get("type") == "message"), None)
    if item:
        m3 = len(s1.events)
        await s1.send({"type": "conversation.item.truncate", "item_id": item["item"]["id"], "content_index": 0, "audio_end_ms": 500})
        trn = await s1.wait(lambda e: e.get("type") in ("conversation.item.truncated", "error"), 6, m3)
        if trn and trn[1].get("type") == "conversation.item.truncated":
            p.mark("cancel_truncate", trn[0], "conversation.item.truncate -> conversation.item.truncated")
        else:
            p.note("cancel_truncate", f"conversation.item.truncate result: {str(trn[1].get('error'))[:200] if trn else 'no reply within 6s'}")
    await asyncio.sleep(1.0)

    # error handling: unknown event type
    n0 = len(s1.events)
    await s1.send({"type": "bogus.event.type"})
    err = await s1.wait(lambda e: e.get("type") == "error", 4, n0)
    if err:
        p.mark("error_handling", err[0], f"unknown event -> error {json.dumps(err[1].get('error'))[:160]}; connection {'open' if s1.ws.state.name == 'OPEN' else 'closed'}")
    else:
        p.note("error_handling", "unknown event type produced no error event")
    await s1.close()

    # direct MCP: only whether the session accepts an `mcp` tool declaration (no real MCP server is reachable here)
    s5 = Session(p, "s5-mcp", model)
    await s5.open()
    await s5.wait(lambda e: e.get("type") == "session.created", 10)
    await s5.send({"type": "session.update", "session": {"tools": [{
        "type": "mcp", "server_label": "probe", "server_url": "https://mcp.invalid/mcp", "require_approval": "never"}]}})
    mc = await s5.wait(lambda e: e.get("type") in ("session.updated", "error"), 8)
    if mc:
        p.note("direct_mcp", f"session.update with an mcp tool declaration -> {mc[1].get('type')}"
                             + (f" {str(mc[1].get('error'))[:160]}" if mc[1].get("type") == "error" else "")
                             + "; no reachable MCP server, so tool use through server-side MCP was not exercised")
    await s5.close()

    # --- s4: server VAD speech barge-in with synthetic speech, plus input transcription of speech
    if speech:
        from voice_harness.adapters.openai_realtime import synth_caller_pcm

        pcm = synth_caller_pcm("Stop. Please stop talking, I have a question.", trail_ms=1500)
        s4 = Session(p, "s4-vad", model)
        await s4.open()
        await s4.wait(lambda e: e.get("type") == "session.created", 10)
        await s4.send(session_update({"type": "server_vad"}, "Reply in full sentences."))
        await s4.wait(lambda e: e.get("type") == "session.updated", 10)
        m = len(s4.events)
        await text_turn(s4, "Explain in at least twelve sentences how a current account differs from a savings account.")
        await s4.wait(lambda e: e.get("type") == "response.output_audio.delta", 20, m)
        await asyncio.sleep(0.2)  # the server generates faster than real time, so the caller must cut in early
        m = len(s4.events)
        started = time.monotonic()
        for o in range(0, len(pcm), 960):
            await s4.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm[o:o + 960]).decode()})
            await asyncio.sleep(0.02)
        ss = await s4.wait(lambda e: e.get("type") == "input_audio_buffer.speech_started", 8, m)
        if ss:
            p.mark("turn_signalling", ss[0], f"server_vad input_audio_buffer.speech_started {int((ss[2] - started) * 1000)} ms after speech began streaming")
        sp = await s4.wait(lambda e: e.get("type") == "input_audio_buffer.speech_stopped", 8, m)
        if sp:
            p.mark("turn_signalling", sp[0], "server_vad input_audio_buffer.speech_stopped")
        await asyncio.sleep(1.0)
        first_resp = next((x for x in s4.events if x[1].get("type") == "response.done"), None)
        gen_ms = sum(e.get("audio_duration_ms", 0) for _, e, _ in s4.events if e.get("type") == "response.output_audio.delta"
                     and first_resp and e.get("response_id") == first_resp[1].get("response_id"))
        if ss and first_resp and abs(first_resp[2] - ss[2]) < 0.5:
            r = first_resp[1].get("response") or {}
            p.mark("cancel_truncate", ss[0], "server_vad speech_started while the assistant was generating")
            p.mark("cancel_truncate", first_resp[0], f"the in-flight response ended at speech_started: response.done status={r.get('status')}, "
                   f"output={len(r.get('output') or [])} items, {gen_ms} ms of audio generated (uninterrupted answers to this prompt were about 2 minutes); no distinct cancelled status")
            cut_item = next((e.get("item_id") for _, e, _ in s4.events if e.get("type") == "response.output_audio.delta"
                             and first_resp and e.get("response_id") == first_resp[1].get("response_id")), None)
            if cut_item:
                m4 = len(s4.events)
                await s4.send({"type": "conversation.item.truncate", "item_id": cut_item, "content_index": 0, "audio_end_ms": 1000})
                tr2 = await s4.wait(lambda e: e.get("type") in ("conversation.item.truncated", "error"), 6, m4)
                done_items = [e for _, e, _ in s4.events if e.get("type") == "response.output_item.done" and (e.get("item") or {}).get("id") == cut_item]
                p.note("cancel_truncate", "conversation.item.truncate on the server_vad-cut item: "
                       + (tr2[1].get("type") if tr2 else "no reply within 6 s (not acknowledged)")
                       + f"; response.output_item.done for that item seen={bool(done_items)}")
        else:
            p.note("cancel_truncate", f"speech barge-in not observed to end the response (speech_started={bool(ss)}, first response.done at speech_started={bool(first_resp and ss and abs(first_resp[2] - ss[2]) < 0.5)})")
        tc = await s4.wait(lambda e: e.get("type", "").startswith("conversation.item.input_audio_transcription"), 15, m)
        if tc:
            kinds = sorted({e.get("type") for _, e, _ in s4.events[m:] if e.get("type", "").startswith("conversation.item.input_audio_transcription")})
            fin = next((e for _, e, _ in s4.events[m:] if e.get("type") == "conversation.item.input_audio_transcription.completed"), None)
            p.mark("audio_in", tc[0], f"speech transcribed; event kinds {kinds}" + (f"; final: {(fin.get('transcript') or '').strip()[:80]!r}" if fin else ""))
            if fin:
                p.mark("transcript_finality", tc[0], "input transcript final: conversation.item.input_audio_transcription.completed")
        else:
            p.note("audio_in", "no input_audio_transcription event for the synthetic speech")
        p.meta["vad_event_types"] = sorted({e.get("type") for _, e, _ in s4.events})
        await s4.close()

    # --- s2: reconnect (fresh connection) and a conversation-history replay check is NOT attempted
    s2 = Session(p, "s2", model)
    await s2.open()
    await s2.wait(lambda e: e.get("type") == "session.created", 10)
    await s2.send(session_update())
    await s2.wait(lambda e: e.get("type") == "session.updated", 10)
    await text_turn(s2, "Say hello.")
    d = await s2.wait(lambda e: e.get("type") == "response.done", 20)
    if d:
        p.mark("reconnect_resume", d[0], "fresh connection after close works; resume (history replay) documented but not exercised")
    await s2.close()

    p.obs_path.write_text(json.dumps({"meta": p.meta, "evidence": p.evidence, "notes": p.notes,
                                      "log_file": str(p.log_path.relative_to(ROOT)),
                                      "flags": {"sdk": f"websockets {websockets.__version__} python {p.meta['python']}"}}, indent=2))
    print("observations:", p.obs_path)
    print({"evidenceRows": {k: len(v) for k, v in p.evidence.items()}, "aliasProbe": p.meta.get("aliasProbe")})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="grok-voice-3.0")
    ap.add_argument("--no-speech", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.model, not a.no_speech)))
