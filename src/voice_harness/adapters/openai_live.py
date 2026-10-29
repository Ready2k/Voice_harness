"""GPT-Live transport with managed Responses delegation and harness-owned tools.

Protocol: OpenAI guides live, voice-websockets and live-delegation (2026-10-27).
Live has no native turn-end or truncate. Harness turns are diagnostic PCM-silence
segments; backend completions never finalize spoken audio. Raw events retain the
original transcript timestamps. This adapter shares queue/transport utilities,
not Realtime wire events, with the Realtime adapter.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Any

from ..audio_activity import RMS_THRESHOLD_PCM16, audible
from ..target_prompts import live_voice_instructions
from . import (
    AudioOut,
    CapabilityUnsupported,
    ProviderError,
    ToolCall,
    TranscriptDelta,
    TranscriptFinal,
    TurnEnd,
    TurnStart,
    Usage,
)
from .openai_realtime import SAMPLE_RATE, OpenAIRealtimeAdapter, synth_caller_pcm

DEFAULT_URL = "wss://api.openai.com/v1/live/sessions"
DEFAULT_MODEL = "gpt-live-1"
DEFAULT_BACKEND = "gpt-6-luna"
FRAME_BYTES = 960  # 20 ms of 24 kHz PCM16
# Reply grouping, measured on the 27 Oct 2026 evaluation ledger (56 replies; derivation in docs/OPENAI_LIVE.md).
REPLY_SILENCE_MS = 800  # longest pause inside a reply with no backend work: 700 ms (p99 600, n=77)
RESULT_VOICE_GRACE_S = 1.5  # backend completion -> its result voiced: p100 1.10 s (p95 0.95, n=28)
CALLER_GRACE_S = 2.5  # caller speech end -> delegation created: p100 2.2 s (n=29)
INPUT_JITTER_S = 0.1  # caller frames still arriving: wait for them rather than splice silence into speech
TURN_BOUNDARY = f"harness_pcm_silence_{REPLY_SILENCE_MS}ms_backend_aware_diagnostic"


class OpenAILiveAdapter(OpenAIRealtimeAdapter):
    NATIVE_NAME = "openai_live"
    TOOL_CALL_ENDS_RESPONSE = False

    def __init__(self, *, api_key=None, url=None, model=DEFAULT_MODEL, ack_timeout=15):
        super().__init__(api_key=api_key, url=url or DEFAULT_URL, model=model, ack_timeout=ack_timeout)
        self._input: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._pump = None
        self._speech_serial = 0
        self._silent_samples = 0
        self._texts = {"user": "", "assistant": ""}
        self._backend_active = False
        self._reply_not_before = 0.0  # caller grace: no reply ends until CALLER_GRACE_S after audible caller audio
        self._completed_at = float("-inf")  # last backend completion
        self._result_voiced = True  # speech has started since that completion
        self._last_input = float("-inf")
        self._last_sent = None
        self._seen_calls = set()
        self._backend_instructions = ""
        self._closing = False
        self._finalized = False
        self.usage_sink = None
        self.backend_model = DEFAULT_BACKEND

    def _emit(self, cls, **kw):
        if cls is Usage and self.usage_sink is not None:
            self.usage_sink(Usage(session_id=self.session_id, turn_id=self._response_id, **kw))
        super()._emit(cls, **kw)

    def capabilities(self):
        return {**super().capabilities(), "provider": "openai_live", "model_id": self._model,
                "interrupt_truncate": "unsupported", "interrupt_truncate_note": "Live has no truncate primitive",
                "usage": "session.closed and nested response.completed", "capability_report_id": None,
                "turn_boundary": TURN_BOUNDARY, "full_duplex": True, "backend_model_id": self.backend_model}

    async def open_session(self, config: dict[str, Any]):
        import websockets
        from websockets.exceptions import InvalidStatus

        if not self._api_key:
            raise ValueError("PROVIDER_AUTH_MISSING")
        self.session_id = config.get("session_id")
        self._model = config.get("model_id", self._model)
        self.backend_model = config.get("backend_model_id", DEFAULT_BACKEND)
        try:
            self._ws = await websockets.connect(self._url,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                max_size=None, open_timeout=config.get("open_timeout", 15))
        except InvalidStatus as exc:
            raise ValueError(f"PROVIDER_HTTP_{exc.response.status_code}") from None
        except (OSError, TimeoutError) as exc:
            raise ValueError(f"PROVIDER_UNREACHABLE:{type(exc).__name__}") from None
        waiter = asyncio.create_task(self._wait_for("session.started", 15))
        await asyncio.sleep(0)
        self._reader = asyncio.create_task(self._read_loop())
        instructions = config.get("instructions", "Be concise and helpful.")
        self._backend_instructions = instructions
        session = {
            "model": self._model,
            "instructions": live_voice_instructions(instructions),  # recorded as target_prompt.delivered
            "audio": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                      "output": {"voice": config.get("voice", "marin")}},
            "delegation": {"type": "responses", "responses": {
                "model": self.backend_model, "instructions": instructions,
                "tools": [{"type": "function", **t} for t in config.get("tools", [])],
                "parallel_tool_calls": False}},
        }
        try:
            await self._send({"type": "session.start", "session": session})
            started = await waiter
        except BaseException:
            waiter.cancel()
            await super().close("setup_failed")
            raise
        self._pump = asyncio.create_task(self._audio_clock())
        resolved = started.get("session", {})
        return {"session_id": self.session_id, "provider_session_id": resolved.get("id"),
                "model_id": resolved.get("model", self._model), "backend_model_id": self.backend_model,
                "turn_boundary": TURN_BOUNDARY}

    def _caller_spoke(self):
        self._reply_not_before = max(self._reply_not_before, time.monotonic() + CALLER_GRACE_S)

    async def _audio_clock(self):
        # Continuous input (silence included) advances the Live session clock. Paced against one monotonic schedule,
        # so per-frame overhead never accumulates into lag. Caller frames go out as the harness supplies them (it
        # paces in real time); silence is sent only when no caller audio is in flight, never spliced into speech.
        due = time.monotonic()
        while not self._closing:
            try:
                frame = self._input.get_nowait()
            except asyncio.QueueEmpty:
                if time.monotonic() - self._last_input < INPUT_JITTER_S:
                    await asyncio.sleep(0.002)
                    due = max(due, time.monotonic())
                    continue
                frame = None
            pcm = frame or bytes(FRAME_BYTES)
            if frame and audible(frame):
                self._caller_spoke()
            await self._send({"type": "session.input_audio.append", "audio": base64.b64encode(pcm).decode()})
            if frame is not None:
                self._last_sent = time.monotonic()
                self._input.task_done()
            due += len(pcm) / (SAMPLE_RATE * 2)
            if (delay := due - time.monotonic()) > 0:
                await asyncio.sleep(delay)

    async def send_audio(self, data, metadata):
        if self.closed or not data or len(data) != metadata["sample_count"] * 2:
            raise ValueError("INVALID_AUDIO")
        if metadata.get("sample_rate", SAMPLE_RATE) != SAMPLE_RATE:
            raise ValueError("INVALID_AUDIO_RATE")
        if audible(data):
            self._caller_spoke()
        self._last_input = time.monotonic()
        for offset in range(0, len(data), FRAME_BYTES):
            await self._input.put(data[offset:offset + FRAME_BYTES])

    async def request_turn(self):
        # Live owns endpointing; never send Realtime commit/response commands. Returns once the final queued caller
        # frame is sent; the marker records that moment, so lag behind the harness's speech_ended_ms is measurable.
        await self._input.join()
        if self.native_sink is not None and self._last_sent is not None:
            self.native_sink(self.NATIVE_NAME, "harness.caller_audio_flushed",
                             {"last_frame_sent_ms": int(self._last_sent * 1000)})

    async def send_text(self, text, *, respond=True):
        if not respond:
            self._backend_instructions += "\n" + text
            await self._send({"type": "session.update", "session": {"delegation": {"type": "responses", "responses": {
                "model": self.backend_model, "instructions": self._backend_instructions}}}})
            await self._send({"type": "session.instructions.append", "event_id": uuid.uuid4().hex,
                              "delegation_id": None, "content": text})
            return
        # Live accepts speech, not runtime user-message injection. Preserve the user
        # trust boundary by using the same caller synthesizer as audio scenarios.
        pcm = await asyncio.to_thread(synth_caller_pcm, text)
        await self.send_audio(pcm, {"sample_count": len(pcm) // 2, "sample_rate": SAMPLE_RATE})

    async def submit_tool_result(self, call_id, result, *, respond=True):
        if call_id != result.get("call_id") or call_id not in self._pending_calls:
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        await self._send({"type": "response.item.create", "item": {
            "type": "function_call_output", "call_id": call_id, "output": json.dumps(result, sort_keys=True)}})
        self._pending_calls.remove(call_id)
        if respond and not self._pending_calls:
            await self._send({"type": "response.create"})
        return {"local_send": "accepted", "provider_acknowledgement": "unconfirmed", "call_id": call_id}

    async def interrupt(self, item_id, audio_end_ms):
        # No truncate or cancel: Live may keep voicing the cut-off reply. Close the local grouping so the cut-in ends
        # that reply here, as a provider turn end would, and later speech starts a new reply.
        if self._response_id is not None:
            self._finish_speech()
        raise CapabilityUnsupported("Live does not support history truncation")

    async def configure_turn_detection(self, turn_detection):
        raise CapabilityUnsupported("Live owns continuous turn detection")

    def _start_speech(self):
        if self._response_id is None:
            self._speech_serial += 1
            self._response_id = f"live_speech_{self._speech_serial}"
            self._item_id = self._response_id
            self._emit(TurnStart)

    def _reply_complete(self):
        now = time.monotonic()
        return (self._silent_samples >= SAMPLE_RATE * REPLY_SILENCE_MS // 1000 and not self._backend_active
                and not self._pending_calls and now >= self._reply_not_before
                and (self._result_voiced or now >= self._completed_at + RESULT_VOICE_GRACE_S))

    def _finish_speech(self):
        for role, text in self._texts.items():
            if text:
                self._emit(TranscriptFinal, role=role, item_id=self._item_id, text=text)
        self._texts = {"user": "", "assistant": ""}
        self._emit(TurnEnd)
        self._response_id = None
        self._silent_samples = 0

    def _handle(self, ev):
        t = ev.get("type", "")
        self._resolve(t, ev)
        if t == "session.output_audio.delta":
            pcm = base64.b64decode(ev["delta"], validate=True)
            if len(pcm) % 2:
                self._emit(ProviderError, code="INVALID_PROVIDER_AUDIO", fatal=True)
                return
            if pcm and audible(pcm):
                if self.native_sink is not None:
                    self.native_sink(self.NATIVE_NAME, "harness.pcm_speech_detected", {
                        "source_event_id": ev.get("event_id"), "rms_threshold_pcm16": RMS_THRESHOLD_PCM16,
                        "sample_count": len(pcm) // 2,
                    })
                if self._response_id is None or self._silent_samples:
                    self._result_voiced = True  # a speech onset: any completed backend result is now being voiced
                self._start_speech()
                self._silent_samples = 0
                self._emit(AudioOut, item_id=self._item_id, pcm=pcm, sample_count=len(pcm) // 2)
            elif self._response_id is not None:
                # The stream is continuous: each pause is passed on in real time up to REPLY_SILENCE_MS, so a backend
                # wait does not fill the reply (and its 20 s audio cap) with silence packet-streaming providers never send.
                kept = max(0, SAMPLE_RATE * REPLY_SILENCE_MS // 1000 - self._silent_samples) * 2
                self._silent_samples += len(pcm) // 2
                if kept and (part := pcm[:kept]):
                    self._emit(AudioOut, item_id=self._item_id, pcm=part, sample_count=len(part) // 2)
                if self._reply_complete():
                    self._finish_speech()
        elif t == "session.delegation.created":
            self._backend_active = True  # arrives just before its response.created
        elif t in ("session.input_transcript.delta", "session.output_transcript.delta"):
            role = "user" if t == "session.input_transcript.delta" else "assistant"
            text = ev.get("delta", "")
            self._texts[role] += text
            self._emit(TranscriptDelta, role=role, item_id=self._item_id, text=text)
        elif t == "response.event":
            nested = ev.get("event", {})
            kind = nested.get("type")
            if kind == "response.created":
                self._backend_active = True
            elif kind == "response.output_item.done" and nested.get("item", {}).get("type") == "function_call":
                item = nested["item"]
                try:
                    args = json.loads(item["arguments"])
                    if not isinstance(args, dict):
                        raise ValueError
                except (ValueError, KeyError):
                    self._emit(ProviderError, code="TOOL_ARGUMENTS_INVALID", fatal=True)
                    return
                if item["call_id"] not in self._seen_calls:
                    self._seen_calls.add(item["call_id"])
                    self._pending_calls.add(item["call_id"])
                    self._emit(ToolCall, call_id=item["call_id"], name=item["name"], arguments=args)
            elif kind in ("response.completed", "response.failed", "response.cancelled", "response.incomplete"):
                # Not a speech boundary: Live may finish an acknowledgement before voicing the delegated result,
                # so the reply stays open until speech resumes or RESULT_VOICE_GRACE_S passes.
                self._backend_active, self._result_voiced, self._completed_at = False, False, time.monotonic()
                response = nested.get("response", {})
                if response.get("usage"):
                    self._emit(Usage, raw={"backend_model_id": self.backend_model, "backend_usage": response["usage"],
                                          "response_id": response.get("id")})
                if kind != "response.completed":
                    self._emit(ProviderError, code="LIVE_BACKEND_FAILED", fatal=True)
        elif t in ("session.usage.updated", "session.closed"):
            self._emit(Usage, raw={"voice_usage": ev.get("usage", {}), "cumulative": True,
                                  "final": t == "session.closed"})
            if t == "session.closed":
                self._closing = True
                self._finalized = True
                if self._response_id:
                    self._finish_speech()
        elif t == "error":
            super()._handle(ev)
            # Startup rejection must not become a generic timeout.
            if "session.started" in self._acks:
                future = self._acks["session.started"]
                if not future.done():
                    future.set_exception(ValueError("PROVIDER_SESSION_REJECTED"))

    async def close(self, reason):
        if self.closed:
            return
        self._closing = True
        if self._pump:
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)
        if self._ws and not self._finalized and reason != "setup_failed":
            waiter = asyncio.create_task(self._wait_for("session.closed", self._ack_timeout))
            await asyncio.sleep(0)
            try:
                await self._send({"type": "session.close"})
                await waiter
            except Exception:
                self._emit(ProviderError, code="LIVE_FINALIZATION_UNCONFIRMED", fatal=False)
            finally:
                if not waiter.done():
                    waiter.cancel()
        await super().close(reason)


def conformance_factory():
    """AC-09 driver: (adapter, prime). Config is baked in; the harness opens with only a session id."""
    tool = {"name": "get_policy_text", "description": "Look up the identity verification policy text.",
            "parameters": {"type": "object", "properties": {"policy_id": {"type": "string"}}, "required": ["policy_id"]}}

    class _Conformance(OpenAILiveAdapter):
        async def open_session(self, config):
            return await super().open_session({
                **config, "tools": [tool],
                "instructions": "Banking test assistant. Keep answers to one short sentence. Use get_policy_text for policy questions."})

    adapter = _Conformance()

    async def prime(dimension: str):
        if dimension == "audio_out":
            await adapter.send_text("Say hello.")
        elif dimension == "tool_result_correlation":
            await asyncio.sleep(3)  # let the greeting finish
            await adapter.send_text("Use your policy tool to look up the identity verification policy and tell me what it says.")
        return None  # interruption is not exercised: Live has no truncate primitive (excluded)

    return adapter, prime
