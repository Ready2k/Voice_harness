"""Phase 0 probe for OpenAI GPT-Live (``wss://api.openai.com/v1/live/sessions``, ``gpt-live-1`` with a ``gpt-6-luna`` backend).

Unlike the other probes this one drives the harness adapter and records its native provider events through
``native_sink``, so every observation is of the provider's wire events but passes through the adapter's session setup.
Usage (key from ``OPENAI_API_KEY``; never printed or logged):
    PYTHONPATH=src python scripts/probe/openai_live_probe.py [--no-speech]
Writes ``openai_live-probe-<ts>.jsonl`` and ``openai_live-observations-<ts>.json`` to artifacts/phase0/ (audio = length + hash).
Build the report with ``python -m voice_harness.probe from-openai-live-observations <observations.json> --report-id <id>``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from voice_harness.adapters.openai_live import DEFAULT_BACKEND, DEFAULT_MODEL, OpenAILiveAdapter
from voice_harness.adapters.openai_realtime import SAMPLE_RATE, synth_caller_pcm

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "phase0"
TOOL = {"name": "get_policy_text", "description": "Look up the identity verification policy text.",
        "parameters": {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]}}
INSTRUCTIONS = "Banking test assistant. Keep answers to one short sentence. Use get_policy_text for policy questions."


def redact(obj):
    if isinstance(obj, dict):
        return {k: ({"b64_len": len(v), "sha": hashlib.sha256(v.encode()).hexdigest()[:16]}
                    if k in ("audio", "delta") and isinstance(v, str) and len(v) > 200 else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class Run:
    def __init__(self):
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
        OUT.mkdir(parents=True, exist_ok=True)
        self.log_path, self.obs_path = OUT / f"openai_live-probe-{stamp}.jsonl", OUT / f"openai_live-observations-{stamp}.json"
        self.seq, self.evidence, self.notes, self.meta, self.types = 0, {}, {}, {"startedAt": datetime.now(UTC).isoformat(), "python": sys.version.split()[0]}, []
        self.session = "s1"

    def native(self, _adapter, kind, payload):
        self.seq += 1
        ev_id = f"olv_ev_{self.seq}"
        self.types.append(kind)
        with self.log_path.open("a") as f:
            f.write(json.dumps({"id": ev_id, "t": datetime.now(UTC).isoformat(), "session": self.session, "type": kind,
                                "detail": redact(payload)}) + "\n")
        self.last = (ev_id, kind, payload)
        return ev_id

    def mark(self, row, ev_id, note=None):
        self.evidence.setdefault(row, []).append(ev_id)
        if note:
            self.notes.setdefault(row, []).append(note)

    def note(self, row, text):
        self.notes.setdefault(row, []).append(text)


async def consume(adapter, run, handle, timeout):
    async def go():
        async for ev in adapter.receive():
            if await handle(ev):
                return
    try:
        await asyncio.wait_for(go(), timeout)
    except TimeoutError:
        return False
    return True


async def main(speech):
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    run = Run()
    adapter = OpenAILiveAdapter()
    adapter.native_sink = run.native
    info = await adapter.open_session({"session_id": "probe-1", "instructions": INSTRUCTIONS, "tools": [TOOL]})
    run.meta["sessionStarted"] = {"model_id": info.get("model_id"), "backend_model_id": info.get("backend_model_id")}
    sid = next((f"olv_ev_{i + 1}" for i, k in enumerate(run.types) if k == "session.started"), None)
    if sid:
        run.mark("input_codecs_rates", sid, "session.start declared audio/pcm 24 kHz and session.started accepted it")
        run.mark("output_codecs_rates", sid, "session.start declared audio/pcm 24 kHz and session.started accepted it")
        run.note("model_discovery", f"session.started model={info.get('model_id')}")
    seen = {"tool": False, "audio": False}
    counts = {}

    async def handle(ev):
        kind = ev.type
        counts[kind] = counts.get(kind, 0) + 1
        eid = f"olv_ev_{run.seq}"
        if kind == "audio_out" and not seen["audio"]:
            seen["audio"] = True
            run.mark("audio_out", eid, "session.output_audio.delta (pcm 24 kHz)")
        if kind == "tool_call":
            seen["tool"] = True
            run.mark("tool_arguments_and_results", eid, f"tool_call {ev.name} {json.dumps(ev.arguments)[:100]} (delegated function call)")
            r = await adapter.submit_tool_result(ev.call_id, {"call_id": ev.call_id, "policy_id": "identity", "version": 1,
                                                               "text": "Callers must pass identity verification before account details are shared."})
            run.mark("tool_arguments_and_results", eid, f"tool result submitted: {json.dumps(r)[:100]}")
        if kind == "transcript_final":
            run.mark("transcript_finality", eid, "session.*_transcript.delta events assembled into a final transcript by the adapter")
        if kind == "usage":
            run.mark("usage_granularity", eid, "session.usage.updated / session.closed usage")
        if kind == "turn_end":
            run.mark("turn_signalling", eid, "response.completed (nested in response.event) ends a reply; no turn-start marker")
        return seen["tool"] and seen["audio"] and kind == "turn_end"

    await adapter.send_text("Use your policy tool to look up the identity verification policy and tell me what it says.")
    if not await consume(adapter, run, handle, 45):
        run.note("tool_arguments_and_results", "tool round trip incomplete within 45 s")

    if speech:
        pcm = synth_caller_pcm("What does your policy say about identity verification?", trail_ms=1500)
        await adapter.send_audio(pcm, {"sample_count": len(pcm) // 2, "sample_rate": SAMPLE_RATE})
        await adapter.request_turn()
        got = {"in": False}

        async def speech_handle(ev):
            eid = f"olv_ev_{run.seq}"
            if ev.type == "transcript_final" and getattr(ev, "role", "") == "user" and not got["in"]:
                got["in"] = True
                run.mark("audio_in", eid, f"synthetic speech transcribed: {ev.text.strip()[:80]!r}")
            if ev.type == "tool_call":
                await adapter.submit_tool_result(ev.call_id, {"call_id": ev.call_id, "policy_id": "identity", "version": 1, "text": "Verify first."})
            return ev.type == "turn_end" and got["in"]
        if not await consume(adapter, run, speech_handle, 45):
            run.note("audio_in", "no user transcript for the synthetic speech within 45 s")
    run.note("cancel_truncate", "Live has no truncate primitive and no client cancel was exercised; the adapter declares interrupt_truncate unsupported")
    run.note("audio_alignment", "no alignment fields were examined")
    run.note("direct_mcp", "not exercised; tools are delegated function calls bridged by the harness gateway")
    await adapter.close("probe_done")
    run.meta["eventTypes"] = sorted(set(run.types))

    run.session = "s2"
    a2 = OpenAILiveAdapter()
    a2.native_sink = run.native
    i2 = await a2.open_session({"session_id": "probe-2", "instructions": INSTRUCTIONS, "tools": [TOOL]})
    if i2.get("provider_session_id"):
        run.mark("reconnect_resume", f"olv_ev_{run.seq}", "fresh session after close succeeded; no resume message was tried")
    await a2.close("probe_done")

    run.obs_path.write_text(json.dumps({"meta": run.meta, "evidence": run.evidence, "notes": run.notes,
                                        "log_file": str(run.log_path.relative_to(ROOT)),
                                        "flags": {"sdk": f"harness adapter openai_live python {run.meta['python']}",
                                                  "model": DEFAULT_MODEL, "backend": DEFAULT_BACKEND}}, indent=2))
    print("observations:", run.obs_path)
    print({"evidenceRows": {k: len(v) for k, v in run.evidence.items()}, "events": counts})
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-speech", action="store_true")
    sys.exit(asyncio.run(main(not ap.parse_args().no_speech)))
