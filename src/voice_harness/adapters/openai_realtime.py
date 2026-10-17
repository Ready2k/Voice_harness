"""OpenAI Realtime (GA) adapter for ``gpt-realtime-2.1``.

Protocol facts below are taken from the Phase 0 probe (``artifacts/phase0/openai-*``):

* ``wss://api.openai.com/v1/realtime?model=...`` with ``Authorization: Bearer`` header.
* ``session.update`` with ``session.type = "realtime"``; audio is ``audio/pcm`` at
  24 kHz in and out, so no resampling is needed (20 ms chunk = 480 samples = 960 bytes).
* Tool calls: ``response.function_call_arguments.done`` carries ``call_id``/``name``/
  ``arguments``; the result goes back as ``conversation.item.create`` with a
  ``function_call_output`` item followed by ``response.create``.
* Interruption: with server VAD, ``input_audio_buffer.speech_started`` makes the server
  cancel the in-flight response itself. ``response.cancel`` + ``conversation.item.truncate``
  are acknowledged on that path (``conversation.item.truncated``). Truncating an item
  after a *client-initiated* ``response.cancel`` was acknowledged in one probe run and
  rejected (``invalid_value``) in another, so truncate is only treated as reliable on the
  VAD path; failures surface as non-fatal ``ProviderError`` events and ``interrupt()``
  reports ``history_truncation`` accordingly.
* Usage comes from ``response.done.response.usage``.

Audio payloads and the API key are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from ..mock_provider import CapabilityUnsupported  # noqa: F401  (re-exported contract)
from . import (
    SUPPORTED,
    AudioOut,
    Interrupted,
    ProviderError,
    ToolCall,
    TranscriptDelta,
    TranscriptFinal,
    TurnEnd,
    TurnStart,
    Usage,
)

log = logging.getLogger("voice_harness.adapters.openai_realtime")

DEFAULT_URL = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime-2.1"
SAMPLE_RATE = 24000
FATAL_CODES = {"invalid_api_key", "authentication_error", "insufficient_quota", "session_expired"}
_END = object()


def redact(obj: Any) -> Any:
    """Return a log-safe copy: audio payloads are reduced to their length."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("audio", "delta") and isinstance(v, str) and (
                k == "audio" or "audio" in str(obj.get("type", "")) and "transcript" not in str(obj.get("type", ""))
            ):
                out[k] = f"<b64 len={len(v)}>"
            elif k.lower() in ("authorization", "api_key", "client_secret"):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


class OpenAIRealtimeAdapter:
    def __init__(self, *, api_key: str | None = None, url: str | None = None, model: str = DEFAULT_MODEL,
                 ack_timeout: float = 5.0):
        self._api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        self._url = url or DEFAULT_URL
        self._model = model
        self._ack_timeout = ack_timeout
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self.session_id: str | None = None
        self.closed = False
        self._response_id: str | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._item_id: str | None = None
        self._item_audio_bytes: dict[str, int] = {}
        self._cancel_requested = False
        self._interrupt_emitted = False
        self._acks: dict[str, asyncio.Future] = {}
        self._pending_calls: set[str] = set()

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "model_id": self._model,
            "profile": "live",
            "audio_in": True,
            "audio_out": True,
            "input_sample_rate": SAMPLE_RATE,
            "output_sample_rate": SAMPLE_RATE,
            "transcript": "streaming",
            "direct_mcp": False,
            "tool_delivery": "gateway_bridge",
            "interrupt_truncate": SUPPORTED,  # reliable on server-VAD path only; see module doc
            "interrupt_truncate_note": "reliable after server-VAD barge-in; after client response.cancel it was inconsistent in probes",
            "usage": "response.done",
            "audio_alignment": "unknown",
            "reconnect_resume": "unknown",
            "semantic_safety": "not_measured",
            "capability_report_id": "cap_openai_gpt_realtime_2_1_20261016",
        }

    # ------------------------------------------------------------------ session
    async def open_session(self, config: dict[str, Any]) -> dict[str, Any]:
        import websockets
        from websockets.exceptions import InvalidStatus

        if not self._api_key:
            raise ValueError("PROVIDER_AUTH_MISSING")
        self.session_id = config.get("session_id")
        model = config.get("model_id", self._model)
        self._model = model
        sep = "&" if "?" in self._url else "?"
        try:
            self._ws = await websockets.connect(
                f"{self._url}{sep}model={model}",
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                max_size=None,
                open_timeout=config.get("open_timeout", 15),
            )
        except InvalidStatus as e:
            status = e.response.status_code
            raise ValueError("PROVIDER_AUTH_FAILED" if status in (401, 403) else f"PROVIDER_HTTP_{status}") from None
        except (OSError, TimeoutError) as e:
            raise ValueError(f"PROVIDER_UNREACHABLE:{type(e).__name__}") from None
        self._reader = asyncio.create_task(self._read_loop())

        created = await self._wait_for("session.created", 15)
        vad = config.get("turn_detection", "manual")
        td = None if vad in (None, "manual") else (
            vad if isinstance(vad, dict)
            else {"type": "server_vad", "interrupt_response": True, "create_response": True})
        session: dict[str, Any] = {
            "type": "realtime",
            "model": model,
            "output_modalities": ["audio"],
            "audio": {
                "input": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "turn_detection": td,
                          "transcription": {"model": config.get("transcription_model", "gpt-4o-transcribe")}},
                "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                           "voice": config.get("voice", "marin")},
            },
        }
        if config.get("instructions"):
            session["instructions"] = config["instructions"]
        tools = [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                  "parameters": t.get("parameters", {"type": "object", "properties": {}})}
                 for t in config.get("tools", [])]
        if tools:
            session["tools"] = tools
            session["tool_choice"] = config.get("tool_choice", "auto")
        await self._send({"type": "session.update", "session": session})
        updated = await self._wait_for("session.updated", 15)
        return {
            "session_id": self.session_id,
            "provider_session_id": (updated.get("session") or created.get("session") or {}).get("id"),
            "model_id": (updated.get("session") or {}).get("model", model),
        }

    async def _wait_for(self, etype: str, timeout: float) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._acks[etype] = fut
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            raise ValueError(f"PROVIDER_TIMEOUT:{etype}") from None
        finally:
            self._acks.pop(etype, None)

    async def _send(self, ev: dict) -> None:
        if self.closed or self._ws is None:
            raise ValueError("SESSION_CLOSED")
        log.debug("out %s", json.dumps(redact(ev))[:500])
        await self._ws.send(json.dumps(ev))

    # ------------------------------------------------------------------ operations
    async def send_audio(self, data: bytes, metadata: dict[str, Any]) -> None:
        if self.closed or len(data) != metadata["sample_count"] * 2 or len(data) == 0:
            raise ValueError("INVALID_AUDIO")
        if metadata.get("sample_rate", SAMPLE_RATE) != SAMPLE_RATE:
            raise ValueError("INVALID_AUDIO_RATE")
        await self._send({"type": "input_audio_buffer.append", "audio": base64.b64encode(data).decode()})

    async def send_text(self, text: str) -> None:
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
        await self._create_response()

    async def request_turn(self) -> None:
        """Commit buffered audio (manual turn mode) and ask for a response."""
        await self._send({"type": "input_audio_buffer.commit"})
        await self._create_response()

    async def configure_turn_detection(self, turn_detection: dict[str, Any] | None) -> None:
        """Change turn detection mid-session (session.update) and wait for session.updated."""
        waiter = asyncio.ensure_future(self._wait_for("session.updated", 15))
        await asyncio.sleep(0)  # register the ack future before sending
        await self._send({"type": "session.update", "session": {
            "type": "realtime", "audio": {"input": {"turn_detection": turn_detection}}}})
        await waiter

    async def _create_response(self) -> None:
        await self._send({"type": "response.create"})

    async def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if call_id != result.get("call_id"):
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        if self._response_id is not None:  # the tool-calling response must finish first
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._idle.wait(), self._ack_timeout)
        fut = asyncio.get_running_loop().create_future()
        self._acks[f"output:{call_id}"] = fut
        await self._send({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id, "output": json.dumps(result, sort_keys=True)}})
        try:
            await asyncio.wait_for(fut, self._ack_timeout)
            ack = "acknowledged"
        except TimeoutError:
            ack = "unconfirmed"
        finally:
            self._acks.pop(f"output:{call_id}", None)
        self._pending_calls.discard(call_id)
        await self._create_response()
        return {"local_send": "accepted", "provider_acknowledgement": ack, "call_id": call_id}

    async def cancel_tool(self, call_id: str, reason: str) -> dict[str, Any]:
        return {"call_id": call_id, "cancelled": False, "reason": "GATEWAY_OWNS_EXECUTION"}

    async def interrupt(self, item_id: str | None, audio_end_ms: int) -> dict[str, Any]:
        """Cancel generation and truncate history to what was actually played.

        ``audio_end_ms`` is the playback position within the assistant item. Truncation is
        reliable on the server-VAD path (the server has already cancelled the response);
        after a client-initiated cancel the provider may reject it, in which case the
        result says ``history_truncation == "rejected"`` and a ProviderError is emitted.
        """
        item_id = item_id or self._item_id
        cancelled = False
        if self._response_id is not None:
            self._cancel_requested = True
            await self._send({"type": "response.cancel"})
            cancelled = True
        truncation = "not_attempted"
        if item_id is not None:
            fut = asyncio.get_running_loop().create_future()
            self._acks[f"truncated:{item_id}"] = fut
            await self._send({"type": "conversation.item.truncate", "item_id": item_id,
                              "content_index": 0, "audio_end_ms": max(0, int(audio_end_ms))})
            try:
                await asyncio.wait_for(fut, self._ack_timeout)
                truncation = "acknowledged"
            except TimeoutError:
                truncation = "rejected"
            finally:
                self._acks.pop(f"truncated:{item_id}", None)
        return {"item_id": item_id, "generation_cancelled": cancelled or self._response_id is None,
                "history_truncation": truncation, "audio_end_ms": audio_end_ms}

    async def close(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        await self._queue.put(_END)

    async def receive(self) -> AsyncIterator[Any]:
        while True:
            ev = await self._queue.get()
            if ev is _END:
                self._queue.put_nowait(_END)
                return
            yield ev

    # ------------------------------------------------------------------ inbound
    def _emit(self, cls, **kw):
        self._queue.put_nowait(cls(session_id=self.session_id, turn_id=self._response_id,
                                   ts_ms=int(time.monotonic() * 1000), **kw))

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    self._emit(ProviderError, code="MALFORMED_PROVIDER_EVENT", message="non-JSON frame")
                    continue
                log.debug("in %s", json.dumps(redact(ev))[:500])
                self._handle(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # connection dropped
            if not self.closed:
                self._emit(ProviderError, code="CONNECTION_LOST", message=type(e).__name__, fatal=True)
        else:
            if not self.closed:
                code = getattr(getattr(self._ws, "close_code", None), "real", None) or "closed"
                self._emit(ProviderError, code="CONNECTION_CLOSED", message=str(code), fatal=True)
        finally:
            for f in self._acks.values():
                if not f.done():
                    f.cancel()
            if not self.closed:
                self._queue.put_nowait(_END)

    def _resolve(self, key: str, ev: dict) -> None:
        f = self._acks.get(key)
        if f and not f.done():
            f.set_result(ev)

    def _handle(self, ev: dict) -> None:
        t = ev.get("type", "")
        self._resolve(t, ev)
        if t == "response.created":
            self._response_id = ev["response"]["id"]
            self._idle.clear()
            self._cancel_requested = False
            self._interrupt_emitted = False
            self._emit(TurnStart)
        elif t == "response.output_item.added":
            item = ev.get("item", {})
            if item.get("type") == "message" and item.get("role") == "assistant":
                self._item_id = item["id"]
        elif t == "response.output_audio.delta":
            pcm = base64.b64decode(ev["delta"])
            iid = ev.get("item_id")
            self._item_id = iid or self._item_id
            self._item_audio_bytes[iid] = self._item_audio_bytes.get(iid, 0) + len(pcm)
            self._emit(AudioOut, item_id=iid, pcm=pcm, sample_rate=SAMPLE_RATE, sample_count=len(pcm) // 2)
        elif t == "response.output_audio_transcript.delta":
            self._emit(TranscriptDelta, role="assistant", item_id=ev.get("item_id"), text=ev.get("delta", ""))
        elif t == "response.output_audio_transcript.done":
            self._emit(TranscriptFinal, role="assistant", item_id=ev.get("item_id"), text=ev.get("transcript", ""))
        elif t == "conversation.item.input_audio_transcription.delta":
            self._emit(TranscriptDelta, role="user", item_id=ev.get("item_id"), text=ev.get("delta", ""))
        elif t == "conversation.item.input_audio_transcription.completed":
            self._emit(TranscriptFinal, role="user", item_id=ev.get("item_id"), text=ev.get("transcript", ""))
        elif t == "response.function_call_arguments.done":
            try:
                args = json.loads(ev.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                self._emit(ProviderError, code="TOOL_ARGUMENTS_INVALID",
                           message=f"call_id={ev.get('call_id')}")
                return
            self._pending_calls.add(ev["call_id"])
            self._emit(ToolCall, call_id=ev["call_id"], name=ev["name"], arguments=args)
        elif t == "input_audio_buffer.speech_started":
            if self._response_id is not None and not self._interrupt_emitted:
                self._interrupt_emitted = True
                iid = self._item_id
                ms = self._item_audio_bytes.get(iid, 0) * 1000 // (SAMPLE_RATE * 2) if iid else None
                self._emit(Interrupted, item_id=iid, audio_end_ms=ms)
        elif t == "conversation.item.truncated":
            self._resolve(f"truncated:{ev.get('item_id')}", ev)
        elif t in ("conversation.item.added", "conversation.item.done"):
            item = ev.get("item", {})
            if item.get("type") == "function_call_output":
                self._resolve(f"output:{item.get('call_id')}", ev)
        elif t == "response.done":
            resp = ev.get("response", {})
            usage = resp.get("usage")
            if resp.get("status") == "cancelled" and not self._interrupt_emitted:
                self._interrupt_emitted = True
                iid = self._item_id
                ms = self._item_audio_bytes.get(iid, 0) * 1000 // (SAMPLE_RATE * 2) if iid else None
                self._emit(Interrupted, item_id=iid, audio_end_ms=ms)
            if usage:
                self._emit(Usage, input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                           raw={**usage, "response_status": resp.get("status")})
            self._emit(TurnEnd)
            self._response_id = None
            self._idle.set()
        elif t == "error":
            err = ev.get("error") or {}
            code = err.get("code") or err.get("type") or "provider_error"
            self._emit(ProviderError, code=str(code), message=str(err.get("message", ""))[:300],
                       fatal=code in FATAL_CODES)


def wav_data(buf: bytes) -> bytes:
    """Return the PCM payload of a RIFF/WAVE file by walking chunks to ``data`` (no fixed 44-byte header)."""
    o = 12
    while o + 8 <= len(buf):
        cid, size = buf[o:o + 4], int.from_bytes(buf[o + 4:o + 8], "little")
        if cid == b"data":
            return buf[o + 8:o + 8 + size]
        o += 8 + size + (size & 1)
    raise ValueError("no data chunk in WAV")


def synth_caller_pcm(text: str = "Stop. Please stop talking, I have a question.", *, lead_ms: int = 200,
                     trail_ms: int = 1000) -> bytes:
    """Synthetic caller utterance (macOS ``say`` + ``afconvert``) as 24 kHz s16le mono with silence padding.
    Raises RuntimeError where those tools are unavailable."""
    import shutil
    import subprocess  # noqa: S404  # nosec B404
    import tempfile

    say, afconvert = shutil.which("say"), shutil.which("afconvert")
    if not (say and afconvert):
        raise RuntimeError("say/afconvert unavailable (macOS only)")
    with tempfile.TemporaryDirectory() as d:
        aiff, wav = f"{d}/u.aiff", f"{d}/u.wav"
        subprocess.run([say, "-o", aiff, text], check=True, timeout=60)  # noqa: S603  # nosec B603
        subprocess.run([afconvert, "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", aiff, wav], check=True, timeout=60)  # noqa: S603  # nosec B603
        with open(wav, "rb") as f:
            pcm = wav_data(f.read())
    return bytes(SAMPLE_RATE * 2 * lead_ms // 1000) + pcm + bytes(SAMPLE_RATE * 2 * trail_ms // 1000)


def conformance_factory():
    """AC-09 driver: (adapter, prime). Config is baked in; the harness opens with only a session id."""
    tool = {"name": "get_account_summary", "description": "Account summary",
            "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}}}

    class _Conformance(OpenAIRealtimeAdapter):
        async def open_session(self, config):
            return await super().open_session({
                **config, "tools": [tool], "turn_detection": "manual",
                "instructions": "Banking test assistant. Keep answers to one short sentence unless asked to count."
                                " Use tools for accounts."})

    adapter = _Conformance()

    async def prime(dimension: str) -> dict[str, Any] | None:
        if dimension == "audio_out":
            await adapter.send_text("Say hello.")
        elif dimension == "tool_result_correlation":
            await asyncio.sleep(3)  # let the greeting finish
            await adapter.send_text("What is the balance for account ACC-001?")
        elif dimension == "interruption":
            return await _barge_in(adapter)
        return None

    return adapter, prime


async def _barge_in(adapter: OpenAIRealtimeAdapter) -> dict[str, Any]:
    """Real barge-in: long answer, then synthetic caller speech streamed in 20 ms chunks mid-response."""
    pcm = synth_caller_pcm()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(adapter._idle.wait(), 30)  # prior turn (tool result follow-up) must finish
    # interrupt_response: server cancels on speech; create_response off so no new answer pollutes the check
    await adapter.configure_turn_detection(
        {"type": "server_vad", "interrupt_response": True, "create_response": False})
    await adapter.send_text("Count from one to thirty slowly, one number per sentence.")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:  # at least ~1.5 s of generated audio before the caller speaks
        if adapter._item_id and adapter._item_audio_bytes.get(adapter._item_id, 0) >= SAMPLE_RATE * 2 * 3 // 2:
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("no assistant audio before barge-in")
    started = time.monotonic()
    for o in range(0, len(pcm), 960):
        chunk = pcm[o:o + 960]
        await adapter.send_audio(chunk, {"sample_count": len(chunk) // 2, "sample_rate": SAMPLE_RATE})
        await asyncio.sleep(0.02)
    return {"barge_in": "synthetic_speech", "speech_ms": len(pcm) * 1000 // (SAMPLE_RATE * 2),
            "stream_s": round(time.monotonic() - started, 2)}
