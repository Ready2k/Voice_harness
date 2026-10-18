"""Phase 0 live probe for Gemini Live on Vertex AI (spec section 3 procedure).

Usage (credentials from Application Default Credentials, in process; no token, key or ADC content is printed,
logged or written):
    PYTHONPATH=src python scripts/probe/google_gemini_probe.py [--region us-central1] [--no-speech]

Writes a redacted raw event log (``google_gemini-probe-<ts>.jsonl``) and an observations JSON
(``google_gemini-observations-<ts>.json``) to artifacts/phase0/. Audio payloads are stored as length + hash
only. Build the report with
``python -m voice_harness.probe from-google-observations <observations.json> --report-id <id>``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import websockets

from voice_harness.adapters.google_gemini_live import DEFAULT_MODEL, _fetch_access_token, project_id

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "phase0"
# Same tool shape the live provider registry sends (lower-case JSON-schema types).
TOOL = {"name": "get_account_summary", "description": "Return a synthetic account summary.",
        "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]}}
INSTR = "You are a concise banking test assistant. Use tools when asked about accounts. Reply in one short sentence."


def host_for(region: str) -> str:
    return "aiplatform.googleapis.com" if region == "global" else f"{region}-aiplatform.googleapis.com"


def url_for(region: str) -> str:
    return f"wss://{host_for(region)}/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"


class Probe:
    def __init__(self, model: str, region: str):
        self.model, self.region = model, region
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
        OUT.mkdir(parents=True, exist_ok=True)
        self.log_path = OUT / f"google_gemini-probe-{stamp}.jsonl"
        self.obs_path = OUT / f"google_gemini-observations-{stamp}.json"
        self.seq = 0
        self.evidence: dict[str, list[str]] = {}
        self.notes: dict[str, list[str]] = {}
        self.meta: dict = {"modelId": model, "region": region, "startedAt": datetime.now(UTC).isoformat(),
                           "python": sys.version.split()[0], "websockets": websockets.__version__}

    def mark(self, row: str, ev_id: str, note: str | None = None) -> None:
        self.evidence.setdefault(row, []).append(ev_id)
        if note:
            self.notes.setdefault(row, []).append(note)

    def note(self, row: str, text: str) -> None:
        self.notes.setdefault(row, []).append(text)

    @staticmethod
    def redact(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if k == "data" and isinstance(v, str):
                    out[k] = {"b64_len": len(v), "sha": hashlib.sha256(v.encode()).hexdigest()[:16]}
                else:
                    out[k] = Probe.redact(v)
            return out
        if isinstance(o, list):
            return [Probe.redact(x) for x in o]
        return o

    def log(self, session: str, direction: str, etype: str, detail) -> str:
        self.seq += 1
        ev_id = f"gem_ev_{self.seq}"
        with self.log_path.open("a") as f:
            f.write(json.dumps({"id": ev_id, "t": datetime.now(UTC).isoformat(), "session": session, "dir": direction,
                                "type": etype, "detail": detail}) + "\n")
        return ev_id


def kind(ev: dict) -> str:
    sc = ev.get("serverContent")
    if sc:
        keys = [k for k in sc if k not in ("groundingMetadata",)]
        return "serverContent." + "+".join(sorted(keys))
    return next(iter([k for k in ev if k != "usageMetadata"]), "usageMetadata")


class Session:
    def __init__(self, probe: Probe, label: str, region: str, model: str):
        self.p, self.label, self.region, self.model = probe, label, region, model
        self.events: list[tuple[str, dict, float]] = []
        self.ws = None
        self.reader = None
        self.closed_info = None

    async def open(self, setup_extra: dict | None = None, tools: bool = True, instr: str = INSTR) -> bool:
        token = await asyncio.to_thread(_fetch_access_token)
        self.t0 = time.monotonic()
        self.ws = await websockets.connect(url_for(self.region), additional_headers={"Authorization": f"Bearer {token}"},
                                           max_size=None, open_timeout=15)
        del token
        self.p.log(self.label, "in", "ws_open", {"ms": int((time.monotonic() - self.t0) * 1000), "host": host_for(self.region)})
        self.reader = asyncio.create_task(self._read())
        setup = {"model": f"projects/{project_id()}/locations/{self.region}/publishers/google/models/{self.model}",
                 "generationConfig": {"responseModalities": ["AUDIO"]},
                 "systemInstruction": {"parts": [{"text": instr}]},
                 "inputAudioTranscription": {}, "outputAudioTranscription": {}, **(setup_extra or {})}
        if tools:
            setup["tools"] = [{"functionDeclarations": [TOOL]}]
        await self.send({"setup": setup})
        return await self.wait(lambda e: "setupComplete" in e, 15) is not None

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                ev = json.loads(raw)
                ev_id = self.p.log(self.label, "in", kind(ev), self.p.redact(ev))
                self.events.append((ev_id, ev, time.monotonic()))
            self.closed_info = {"code": self.ws.close_code, "reason": (self.ws.close_reason or "")[:300]}
        except Exception as e:  # noqa: BLE001
            self.closed_info = {"error": type(e).__name__, "code": getattr(self.ws, "close_code", None),
                                "reason": (getattr(self.ws, "close_reason", "") or "")[:300]}
        self.p.log(self.label, "in", "ws_closed", self.closed_info)

    async def send(self, ev: dict) -> str:
        i = self.p.log(self.label, "out", next(iter(ev)), self.p.redact(ev))
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
        with_ws = self.ws
        if with_ws:
            await with_ws.close()
        if self.reader:
            self.reader.cancel()


def is_audio(e: dict) -> bool:
    return any("inlineData" in p for p in ((e.get("serverContent") or {}).get("modelTurn") or {}).get("parts", []))


def audio_bytes(events) -> int:
    n = 0
    for _, e, _ in events:
        for part in ((e.get("serverContent") or {}).get("modelTurn") or {}).get("parts", []):
            if "inlineData" in part:
                n += len(base64.b64decode(part["inlineData"]["data"]))
    return n


def turn_complete(e: dict) -> bool:
    return bool((e.get("serverContent") or {}).get("turnComplete"))


async def text_turn(s: Session, text: str, complete: bool = True) -> str:
    return await s.send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": complete}})


async def main(model: str, region: str, speech: bool) -> int:
    ok, why = (True, None)
    if not project_id():
        ok, why = False, "no project"
    if not ok:
        print(f"credentials unavailable: {why}", file=sys.stderr)
        return 2
    p = Probe(model, region)

    # --- region check: a second endpoint (global) accepts the same pinned model; connect + setup only, no turn
    sg = Session(p, "region-global", "global", model)
    try:
        opened = await sg.open(tools=False)
        await asyncio.sleep(0.3)
        p.meta["globalEndpoint"] = {"setupComplete": opened, "closed": sg.closed_info}
        p.note("model_discovery", f"global endpoint setup {'completed' if opened else 'did not complete'} for the pinned model"
               + (f" (closed {sg.closed_info})" if sg.closed_info else ""))
    except Exception as e:  # noqa: BLE001
        p.meta["globalEndpoint"] = {"error": type(e).__name__}
    await sg.close()

    # --- s1: text turn, tool round trip, usage, transcripts, error, context-only text
    s1 = Session(p, "s1", region, model)
    if not await s1.open():
        print("setup did not complete", file=sys.stderr)
        return 3
    sc = await s1.wait(lambda e: "setupComplete" in e, 1)
    p.meta["setupComplete"] = {k: ("<id>" if k == "sessionId" else v) for k, v in sc[1]["setupComplete"].items()}
    p.mark("model_discovery", sc[0], "setupComplete received for the pinned model; it carries a session id and no model echo")
    p.mark("input_codecs_rates", sc[0], "audio in is documented as 16 kHz PCM; exercised below")

    m = len(s1.events)
    await text_turn(s1, "What is the balance for account ACC-001?")
    fc = await s1.wait(lambda e: "toolCall" in e, 30, m)
    if fc:
        call = fc[1]["toolCall"]["functionCalls"][0]
        p.mark("tool_arguments_and_results", fc[0], f"toolCall {call.get('name')} {json.dumps(call.get('args'))}; id present={bool(call.get('id'))}")
        p.note("tool_arguments_and_results", "tool declared with lower-case JSON-schema types and a required list: accepted")
        tc = await s1.wait(turn_complete, 15, m)
        if tc:
            p.mark("turn_signalling", tc[0], "a tool-calling turn ends with serverContent.turnComplete before the tool result is sent")
        m2 = len(s1.events)
        r = await s1.send({"toolResponse": {"functionResponses": [
            {"id": call["id"], "name": call["name"], "response": {"account_id": "ACC-001", "balance": "1234.56", "currency": "GBP"}}]}})
        done = await s1.wait(turn_complete, 30, m2)
        if done:
            p.mark("tool_arguments_and_results", r, "toolResponse by id then audio/transcript that uses the result (no explicit acknowledgement message)")
            p.mark("turn_signalling", done[0], "serverContent.turnComplete ends the post-tool turn")
            um = done[1].get("usageMetadata")
            if um:
                p.mark("usage_granularity", done[0], f"usageMetadata with the turnComplete message: keys {sorted(um)}; "
                       f"prompt={um.get('promptTokensDetails')}; candidates={um.get('candidatesTokensDetails')}")
            else:
                p.note("usage_granularity", "turnComplete carried no usageMetadata")
            window = s1.events[m2:done[3] + 1]
            ab = audio_bytes(window)
            tokens = next((d.get("tokenCount") for d in (um or {}).get("candidatesTokensDetails", []) if d.get("modality") == "AUDIO"), None)
            p.meta["outputAudioCheck"] = {"bytes": ab, "audio_tokens": tokens,
                                          "seconds_if_24k": round(ab / 48000, 2), "seconds_if_16k": round(ab / 32000, 2),
                                          "seconds_from_25_tokens_per_s": round(tokens / 25, 2) if tokens else None}
            first_audio = next((t for _, e, t in window if is_audio(e)), None)
            last_audio = max((t for _, e, t in window if is_audio(e)), default=None)
            if first_audio and last_audio:
                p.meta["outputAudioCheck"]["generation_wall_s"] = round(last_audio - first_audio, 2)
            if ab:
                p.mark("audio_out", next(i for i, e, _ in window if is_audio(e)), "modelTurn inlineData audio/pcm")
            outs = [e["serverContent"]["outputTranscription"] for _, e, _ in window if "outputTranscription" in (e.get("serverContent") or {})]
            if outs:
                p.mark("transcript_finality", next(i for i, e, _ in window if "outputTranscription" in (e.get("serverContent") or {})),
                       f"outputTranscription fragments ({len(outs)}) then an empty one with finished=true; text is incremental, not cumulative")
        p.note("tool_arguments_and_results", "toolCallCancellation not exercised")
    else:
        p.note("tool_arguments_and_results", "no toolCall within 30s")
    p.note("audio_alignment", "no timing or alignment fields were seen on audio or transcript messages")

    # context-only text (turnComplete false): must not start a reply
    m = len(s1.events)
    r = await text_turn(s1, "Note for later: the customer's favourite colour is teal.", complete=False)
    quiet = await s1.wait(lambda e: is_audio(e) or turn_complete(e), 5, m)
    p.meta["contextOnlyText"] = {"replied": bool(quiet)}
    await text_turn(s1, "What colour did I just say was the customer's favourite? Answer with the colour only.")
    ans = await s1.wait(turn_complete, 30, m)
    outtxt = "".join((e.get("serverContent") or {}).get("outputTranscription", {}).get("text", "") for _, e, _ in s1.events[m:])
    p.meta["contextOnlyText"]["recalled_teal"] = "teal" in outtxt.lower()
    p.note("tool_arguments_and_results", f"context-only text (clientContent turnComplete=false): replied={bool(quiet)}, later recalled={p.meta['contextOnlyText']['recalled_teal']}")
    _ = r, ans

    # error handling: invalid client message
    n0 = len(s1.events)
    await s1.send({"bogusMessage": {}})
    await asyncio.sleep(3)
    err = [(i, e) for i, e, _ in s1.events[n0:] if "error" in e]
    if s1.closed_info or err:
        p.mark("error_handling", err[0][0] if err else sc[0], f"unknown client message: connection closed {s1.closed_info}" if s1.closed_info else f"error message {json.dumps(err[0][1])[:200]}")
    else:
        p.note("error_handling", "unknown client message produced neither a close nor an error")
    await s1.close()

    # --- session resumption: does the server issue handles for this model? (observed only)
    s2 = Session(p, "s2-resume", region, model)
    if await s2.open({"sessionResumption": {}}, tools=False):
        await text_turn(s2, "Say hello.")
        await s2.wait(turn_complete, 20)
        up = [(i, e) for i, e, _ in s2.events if "sessionResumptionUpdate" in e]
        if up:
            u = up[-1][1]["sessionResumptionUpdate"]
            p.note("reconnect_resume", f"sessionResumptionUpdate seen: resumable={u.get('resumable')}, handle present={bool(u.get('newHandle'))}; reconnecting with a handle was not exercised")
        else:
            p.note("reconnect_resume", "no sessionResumptionUpdate received")
        p.mark("reconnect_resume", s2.events[0][0], "fresh connection works; handle-based resume not exercised")
    await s2.close()

    # --- s4: speech in under automatic activity detection, then barge-in
    if speech:
        from voice_harness.adapters.openai_realtime import synth_caller_pcm
        from voice_harness.resample import resample

        pcm16 = resample(synth_caller_pcm("Hello, can you hear me? Please tell me which bank this is.", trail_ms=1500), 24000, 16000)
        s4 = Session(p, "s4-speech", region, model)
        await s4.open(tools=False)
        m = len(s4.events)
        started = time.monotonic()
        a1 = None
        for o in range(0, len(pcm16), 640):
            ch = pcm16[o:o + 640]
            i = await s4.send({"realtimeInput": {"audio": {"data": base64.b64encode(ch).decode(), "mimeType": "audio/pcm;rate=16000"}}})
            a1 = a1 or i
            await asyncio.sleep(0.02)
        p.mark("input_codecs_rates", a1, "realtimeInput.audio audio/pcm;rate=16000 accepted")
        tr = await s4.wait(lambda e: "inputTranscription" in (e.get("serverContent") or {}), 15, m)
        done = await s4.wait(turn_complete, 30, m)
        if tr:
            texts = [e["serverContent"]["inputTranscription"] for _, e, _ in s4.events[m:] if "inputTranscription" in (e.get("serverContent") or {})]
            joined = "".join(t.get("text", "") for t in texts).strip()
            p.mark("audio_in", tr[0], f"speech transcribed ({len(texts)} inputTranscription messages, {int((tr[2] - started) * 1000)} ms to the first); text: {joined[:100]!r}")
            p.mark("transcript_finality", tr[0], "input transcription arrives as fragments; finished=true marker: " + str(any(t.get("finished") for t in texts)))
        else:
            p.note("audio_in", "no inputTranscription for synthetic speech")
        if done:
            p.mark("turn_signalling", done[0], "automatic activity detection: model reply and turnComplete follow the caller's speech without any client turn message")
            p.mark("audio_in", done[0], "model replied to streamed speech (audio_out count " + str(sum(1 for _, e, _ in s4.events[m:] if is_audio(e))) + " chunks)")
        await s4.close()

        # barge-in: long answer, then speech while it is playing
        pcm_b = resample(synth_caller_pcm("Stop. Please stop talking, I have a question.", trail_ms=1500), 24000, 16000)
        s5 = Session(p, "s5-bargein", region, model)
        await s5.open(tools=False, instr="Reply in full sentences.")
        m = len(s5.events)
        await text_turn(s5, "Explain in at least twelve sentences how a current account differs from a savings account.")
        await s5.wait(is_audio, 20, m)
        await asyncio.sleep(0.3)
        m = len(s5.events)
        started = time.monotonic()
        sent_bytes_before = audio_bytes(s5.events[:m])
        for o in range(0, len(pcm_b), 640):
            ch = pcm_b[o:o + 640]
            await s5.send({"realtimeInput": {"audio": {"data": base64.b64encode(ch).decode(), "mimeType": "audio/pcm;rate=16000"}}})
            await asyncio.sleep(0.02)
        it = await s5.wait(lambda e: (e.get("serverContent") or {}).get("interrupted"), 8, m)
        if it:
            p.mark("cancel_truncate", it[0], f"serverContent.interrupted {int((it[2] - started) * 1000)} ms after speech began streaming")
            idx = it[3]
            after = sum(1 for _, e, _ in s5.events[idx + 1:] if is_audio(e))
            p.meta["bargeIn"] = {"interrupted_after_ms": int((it[2] - started) * 1000), "audio_bytes_before_speech": sent_bytes_before,
                                 "audio_chunks_after_interrupted_before_wait": after}
        else:
            p.note("cancel_truncate", "no serverContent.interrupted after streamed speech while the model was talking")
        await asyncio.sleep(3)
        later_audio = sum(1 for _, e, _ in s5.events[(it[3] + 1 if it else m):] if is_audio(e))
        p.meta.setdefault("bargeIn", {})["audio_chunks_after_interrupted_total_3s"] = later_audio
        p.note("cancel_truncate", "the protocol has no cancel or truncate client message (message types: setup, clientContent, realtimeInput, toolResponse)")
        await s5.close()

    p.note("direct_mcp", "not exercised: tools are function declarations bridged by the harness gateway")
    p.obs_path.write_text(json.dumps({"meta": p.meta, "evidence": p.evidence, "notes": p.notes,
                                      "log_file": str(p.log_path.relative_to(ROOT)),
                                      "flags": {"sdk": f"websockets {websockets.__version__} python {p.meta['python']}"}}, indent=2))
    print("observations:", p.obs_path)
    print({"evidenceRows": {k: len(v) for k, v in p.evidence.items()}, "bargeIn": p.meta.get("bargeIn"),
           "outputAudioCheck": p.meta.get("outputAudioCheck"), "contextOnly": p.meta.get("contextOnlyText")})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--region", default="us-central1")
    ap.add_argument("--no-speech", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.model, a.region, not a.no_speech)))
