"""xAI Grok Voice realtime adapter for ``grok-voice-3.0``.

Protocol facts below come from the Phase 0 probe (``artifacts/phase0/xai-*``) and
``docs/PHASE4_NOTES_xai.md``. The surface resembles OpenAI Realtime, but compatibility is not assumed:

* ``wss://api.x.ai/v1/realtime?model=...`` with ``Authorization: Bearer`` (key from ``xAI_API_KEY``).
  The server sends ``ping`` events (ignored) and ``session.created`` (``session.model`` is the resolved
  model: the ``grok-voice-latest`` alias resolved to ``grok-voice-3.0``; runs pin the versioned name).
* ``session.update`` has no ``type``/``model`` field. Audio is ``audio/pcm`` 24 kHz in and out, so no
  resampling. ``turn_detection`` null is manual; ``{"type": "server_vad"}`` creates responses itself.
* The server generates audio much faster than real time and ``server_vad`` speech ends the in-flight
  response, reported as ``response.done`` with status ``completed`` and an empty ``output`` (not
  ``cancelled``), usually just before ``input_audio_buffer.speech_started``. The adapter therefore also
  emits ``Interrupted`` when speech starts within ``VAD_END_WINDOW_S`` of such a response end.
* Client ``response.cancel`` yields ``response.done`` status ``cancelled``; ``conversation.item.truncate``
  was acknowledged with ``conversation.item.truncated``.
* Tool calls: ``response.function_call_arguments.done`` (``call_id``/``name``/``arguments``). A tool-calling
  response can also carry a spoken preamble. The result goes back as a ``function_call_output`` item
  followed by ``response.create``.
* Usage is a top-level ``usage`` object on ``response.done`` (``response.usage`` is empty).
* Input transcripts need ``input_audio_transcription.model = grok-transcribe``; ``.updated`` events are
  cumulative and ``.completed`` repeats per item with growing text, so the last one for an item wins.

Audio payloads and the API key are never logged or emitted.
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
    AUDIO_OVERFLOW_CODE,
    SUPPORTED,
    AudioOut,
    Interrupted,
    ProviderError,
    QueueBudget,
    ToolCall,
    TranscriptDelta,
    TranscriptFinal,
    TurnEnd,
    TurnStart,
    Usage,
)
from .openai_realtime import _package_version, redact, synth_caller_pcm

log = logging.getLogger("voice_harness.adapters.xai_voice")

DEFAULT_URL = "wss://api.x.ai/v1/realtime"
DEFAULT_MODEL = "grok-voice-3.0"  # pinned; the grok-voice-latest alias is never used for runs
KEY_ENV = "xAI_API_KEY"
SAMPLE_RATE = 24000
VAD_END_WINDOW_S = 0.5
# Codes the harness treats as unrecoverable; xAI's own error-code list was not found in the docs (see notes).
FATAL_CODES = {"invalid_api_key", "authentication_error", "unauthorized", "insufficient_quota", "session_expired"}
_END = object()


class XaiVoiceAdapter:
    # A tool-calling response ends (response.done) before the response that uses the tool result.
    TOOL_CALL_ENDS_RESPONSE = True

    def __init__(self, *, api_key: str | None = None, url: str | None = None, model: str = DEFAULT_MODEL,
                 ack_timeout: float = 5.0):
        self._api_key = api_key if api_key is not None else os.environ.get(KEY_ENV)
        self._url = url or DEFAULT_URL
        self._model = model
        self._ack_timeout = ack_timeout
        self.text_inputs = 0  # user text items sent: xAI lists '$0.004 / text input' with no unit, so each item is counted
        self.usage_sink = None  # optional Usage -> None, called directly so close-time usage is not lost with the queue
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._budget = QueueBudget()
        self.native_sink = None  # optional (adapter, native_type, payload) -> None; preserves every provider event
        self.session_id: str | None = None
        self.closed = False
        self._response_id: str | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._item_id: str | None = None
        self._item_audio_bytes: dict[str, int] = {}
        self._interrupt_emitted = False
        self._last_end: tuple[float, str | None, bool] | None = None  # (monotonic, item id, ended with empty output)
        self._cut_items: dict[str, int | None] = {}  # item -> played ms at cut, for responses speech detection ended
        self._item_first_audio: dict[str, float] = {}
        self._onset_at: float | None = None  # first loud input chunk sent while a response was generating
        self._user_final: dict[str, str] = {}
        self._acks: dict[str, asyncio.Future] = {}
        self._pending_calls: set[str] = set()

    def __repr__(self) -> str:
        return f"XaiVoiceAdapter(model={self._model!r}, closed={self.closed})"

    def connection_info(self) -> dict[str, Any]:
        """Endpoint host and client package versions for the evidence binding (never the URL path, query, userinfo or key)."""
        from urllib.parse import urlsplit

        host = None
        try:
            host = urlsplit(self._url).hostname
        except ValueError:
            pass
        return {"endpoint_host": host, "region": None, "sdk_versions": {"websockets": _package_version("websockets")}}

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "xai_grok_voice",
            "model_id": self._model,
            "profile": "live",
            "audio_in": True,
            "audio_out": True,
            "input_sample_rate": SAMPLE_RATE,
            "output_sample_rate": SAMPLE_RATE,
            "transcript": "streaming",
            "direct_mcp": False,  # server-side mcp exists but was not exercised; tools use the gateway bridge
            "tool_delivery": "gateway_bridge",
            "interrupt_truncate": SUPPORTED,
            "interrupt_truncate_note": "response.cancel and conversation.item.truncate acknowledged in probes; "
                                       "server_vad speech ends the response with status completed",
            "usage": "response.done",
            "audio_alignment": "unknown",
            "reconnect_resume": "unknown",
            "semantic_safety": "not_measured",
            "capability_report_id": "cap_xai_grok_voice_3_0_20261018",
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
            raise ValueError(f"PROVIDER_HTTP_{status}") from None
        except (OSError, TimeoutError) as e:
            raise ValueError(f"PROVIDER_UNREACHABLE:{type(e).__name__}") from None
        self._reader = asyncio.create_task(self._read_loop())

        created = await self._wait_for("session.created", 15)
        session: dict[str, Any] = {
            "voice": config.get("voice", "ara"),
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}},
                      "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}}},
            "turn_detection": self._turn_detection(config.get("turn_detection", "manual")),
            "input_audio_transcription": {"model": config.get("transcription_model", "grok-transcribe")},
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
        created_session = created.get("session") or {}
        return {
            "session_id": self.session_id,
            "provider_session_id": created_session.get("id"),
            # the server-resolved model (alias resolution is observable here), not the requested one
            "model_id": created_session.get("model") or (updated.get("session") or {}).get("model") or model,
        }

    @staticmethod
    def _turn_detection(vad: Any) -> dict[str, Any] | None:
        if vad in (None, "manual"):
            return None
        return vad if isinstance(vad, dict) else {"type": "server_vad"}

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
        if self._onset_at is None and self._response_id is not None and _loud(data):
            self._onset_at = time.monotonic()
        await self._send({"type": "input_audio_buffer.append", "audio": base64.b64encode(data).decode()})

    async def send_text(self, text: str, *, respond: bool = True) -> None:
        """Add a user text item; ``respond=False`` adds context without asking for a reply."""
        self.text_inputs += 1
        await self._send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
        if respond:
            await self._send({"type": "response.create"})

    async def request_turn(self) -> None:
        """Commit buffered audio (manual turn mode) and ask for a response."""
        await self._send({"type": "input_audio_buffer.commit"})
        await self._send({"type": "response.create"})

    async def configure_turn_detection(self, turn_detection: dict[str, Any] | None) -> None:
        """Change turn detection mid-session (session.update) and wait for session.updated."""
        waiter = asyncio.ensure_future(self._wait_for("session.updated", 15))
        await asyncio.sleep(0)  # register the ack future before sending
        await self._send({"type": "session.update", "session": {"turn_detection": turn_detection}})
        await waiter

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
        await self._send({"type": "response.create"})
        return {"local_send": "accepted", "provider_acknowledgement": ack, "call_id": call_id}

    async def cancel_tool(self, call_id: str, reason: str) -> dict[str, Any]:
        return {"call_id": call_id, "cancelled": False, "reason": "GATEWAY_OWNS_EXECUTION"}

    async def interrupt(self, item_id: str | None, audio_end_ms: int) -> dict[str, Any]:
        """Cancel generation and truncate history to what was actually played.

        ``audio_end_ms`` is clamped to the audio generated for the item (the server generates faster than
        real time). A rejected or unacknowledged truncate is reported as ``history_truncation == "rejected"``.
        """
        item_id = item_id or self._item_id
        cancelled = False
        if self._response_id is not None:
            await self._send({"type": "response.cancel"})
            cancelled = True
        truncation = "not_attempted"
        requested = end_ms = max(0, int(audio_end_ms))
        attempts = 0
        if item_id is not None:
            generated_ms = self._item_audio_bytes.get(item_id, 0) * 1000 // (SAMPLE_RATE * 2)
            requested = end_ms = min(end_ms, generated_ms)
            # An item cut off by server speech detection is silently refused (no error, no ack) when
            # audio_end_ms is beyond what the server counts for it (about the playback position at speech
            # onset, not the audio generated ahead of it). Retry lower: a smaller end never claims more audio
            # was heard than the caller was shown, and the acknowledged value is reported.
            ladder = [end_ms]
            if item_id in self._cut_items:
                cut = self._cut_items[item_id]
                end_ms = min(end_ms, cut) if cut is not None else end_ms
                base = end_ms
                ladder = [base * n // 100 for n in ((100, 85, 70, 50) if cut is not None else (100, 75, 50, 40, 30, 20, 10))]
            ladder = [c for c in ladder if c > 0]
            truncation = "rejected"
            for i, candidate in enumerate(dict.fromkeys(ladder)):
                attempts += 1
                fut = asyncio.get_running_loop().create_future()
                self._acks[f"truncated:{item_id}"] = fut
                await self._send({"type": "conversation.item.truncate", "item_id": item_id,
                                  "content_index": 0, "audio_end_ms": candidate})
                try:
                    await asyncio.wait_for(fut, self._ack_timeout if i == 0 else min(self._ack_timeout, 1.0))
                    truncation, end_ms = "acknowledged", candidate
                    break
                except TimeoutError:
                    continue
                finally:
                    self._acks.pop(f"truncated:{item_id}", None)
        return {"item_id": item_id, "generation_cancelled": cancelled or self._response_id is None,
                "history_truncation": truncation, "audio_end_ms": end_ms, "requested_audio_end_ms": requested,
                "truncate_attempts": attempts}

    async def close(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        raw = {"text_inputs": self.text_inputs}
        if self.usage_sink is not None:
            self.usage_sink(Usage(session_id=self.session_id, raw=raw))
        else:
            self._emit(Usage, raw=raw)
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
            self._budget.released(ev)
            if ev is _END:
                self._queue.put_nowait(_END)
                return
            yield ev

    # ------------------------------------------------------------------ inbound
    def _emit(self, cls, **kw):
        ev = cls(session_id=self.session_id, turn_id=self._response_id,
                 ts_ms=int(time.monotonic() * 1000), **kw)
        if not self._budget.admit(ev, self._queue.qsize()):
            self._audio_overflow()
            return
        self._queue.put_nowait(ev)

    def _audio_overflow(self):
        """Consumer is not draining: drop audio/transcripts, signal once, keep the control path open."""
        self._budget.dropped += 1
        self.dropped_audio_events = self._budget.dropped
        if self._budget.dropped == 1:
            self._queue.put_nowait(ProviderError(session_id=self.session_id, code=AUDIO_OVERFLOW_CODE,
                                                 message="consumer stalled", fatal=False,
                                                 ts_ms=int(time.monotonic() * 1000)))

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    self._emit(ProviderError, code="MALFORMED_PROVIDER_EVENT", message="non-JSON frame")
                    continue
                if not isinstance(ev, dict):
                    self._emit(ProviderError, code="MALFORMED_PROVIDER_EVENT", message="non-object frame")
                    continue
                if ev.get("type") == "ping":
                    continue  # keep-alive, carries no session state
                if self.native_sink is not None:
                    self.native_sink("xai_grok_voice", ev.get("type") or "unknown", ev)
                log.debug("in %s", json.dumps(redact(ev))[:500])
                try:
                    self._handle(ev)
                except (KeyError, TypeError, ValueError):
                    self._emit(ProviderError, code="MALFORMED_PROVIDER_EVENT", message=str(ev.get("type"))[:80])
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

    def _emit_interrupted(self, item_id: str | None, *, server_cut: bool = False) -> None:
        self._interrupt_emitted = True
        ms = self._item_audio_bytes.get(item_id, 0) * 1000 // (SAMPLE_RATE * 2) if item_id else None
        if server_cut and item_id:
            # Generation runs ahead of real time, and the server keeps only what would have played by the time
            # speech began: first audio -> first loud input chunk sent. Unknown when no loud chunk was seen.
            first = self._item_first_audio.get(item_id)
            cut = max(0, int((self._onset_at - first) * 1000)) if first is not None and self._onset_at else None
            self._cut_items[item_id] = cut
            if cut is not None and ms is not None:
                ms = min(ms, cut)
        self._emit(Interrupted, item_id=item_id, audio_end_ms=ms)

    def _handle(self, ev: dict) -> None:
        t = ev.get("type", "")
        self._resolve(t, ev)
        if t == "response.created":
            self._response_id = ev["response"]["id"]
            self._idle.clear()
            self._interrupt_emitted = False
            self._onset_at = None
            self._emit(TurnStart)
        elif t == "response.output_item.added":
            item = ev.get("item", {})
            if item.get("type") == "message" and item.get("role") == "assistant":
                self._item_id = item["id"]
        elif t == "response.output_audio.delta":
            pcm = base64.b64decode(ev["delta"])
            iid = ev.get("item_id")
            self._item_id = iid or self._item_id
            self._item_first_audio.setdefault(iid, time.monotonic())
            self._item_audio_bytes[iid] = self._item_audio_bytes.get(iid, 0) + len(pcm)
            self._emit(AudioOut, item_id=iid, pcm=pcm, sample_rate=SAMPLE_RATE, sample_count=len(pcm) // 2)
        elif t == "response.output_audio_transcript.delta":
            self._emit(TranscriptDelta, role="assistant", item_id=ev.get("item_id"), text=ev.get("delta", ""))
        elif t == "response.output_audio_transcript.done":
            self._emit(TranscriptFinal, role="assistant", item_id=ev.get("item_id"), text=ev.get("transcript", ""))
        elif t == "conversation.item.input_audio_transcription.updated":
            # cumulative text for the item so far, not an increment
            self._emit(TranscriptDelta, role="user", item_id=ev.get("item_id"), text=ev.get("transcript", ""))
        elif t == "conversation.item.input_audio_transcription.completed":
            iid, text = ev.get("item_id"), ev.get("transcript", "")
            if self._user_final.get(iid) != text:  # the provider repeats .completed; emit changes only
                self._user_final[iid] = text
                self._emit(TranscriptFinal, role="user", item_id=iid, text=text)
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
                self._emit_interrupted(self._item_id, server_cut=True)
            elif self._last_end and not self._interrupt_emitted:
                at, iid, empty = self._last_end
                # server_vad ended the response (status completed, empty output) just before this event
                if empty and time.monotonic() - at <= VAD_END_WINDOW_S:
                    self._emit_interrupted(iid, server_cut=True)
            self._last_end = None
        elif t == "conversation.item.truncated":
            self._resolve(f"truncated:{ev.get('item_id')}", ev)
        elif t in ("conversation.item.added", "conversation.item.done"):
            item = ev.get("item", {})
            if item.get("type") == "function_call_output":
                self._resolve(f"output:{item.get('call_id')}", ev)
        elif t == "response.done":
            resp = ev.get("response", {})
            usage = ev.get("usage") or resp.get("usage")  # xAI puts usage beside, not inside, response
            status = resp.get("status")
            had_audio = bool(self._item_id and self._item_audio_bytes.get(self._item_id))
            if status == "cancelled" and not self._interrupt_emitted:
                self._emit_interrupted(self._item_id)
            if usage:
                self._emit(Usage, input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                           raw={**usage, "response_status": status})
            self._emit(TurnEnd)
            self._last_end = (time.monotonic(), self._item_id,
                              status == "completed" and not resp.get("output") and had_audio)
            self._response_id = None
            self._idle.set()
        elif t == "error":
            err = ev.get("error") or {}
            code = err.get("code") or err.get("type") or "provider_error"
            self._emit(ProviderError, code=str(code), message=str(err.get("message", ""))[:300],
                       fatal=code in FATAL_CODES)


def _loud(pcm: bytes, threshold: int = 500) -> bool:
    """True when the s16le chunk's RMS is above a speech-ish floor (silence and room noise stay below it)."""
    import array

    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if not samples:
        return False
    return (sum(x * x for x in samples) / len(samples)) ** 0.5 > threshold


def conformance_factory():
    """AC-09 driver: (adapter, prime). Config is baked in; the harness opens with only a session id."""
    tool = {"name": "get_account_summary", "description": "Account summary",
            "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}}}

    class _Conformance(XaiVoiceAdapter):
        async def open_session(self, config):
            return await super().open_session({
                **config, "tools": [tool], "turn_detection": "manual",
                "instructions": "Banking test assistant. Keep answers to one short sentence unless asked for more."
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


async def _barge_in(adapter: XaiVoiceAdapter) -> dict[str, Any]:
    """Real barge-in: a long answer, then synthetic caller speech streamed in 20 ms chunks while it generates.

    The server generates faster than real time, so the caller cuts in as soon as audio flows. Turn detection is
    manual: speech still ends the in-flight response (observed), but the server does not answer the barge-in."""
    pcm = synth_caller_pcm()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(adapter._idle.wait(), 30)  # prior turn (tool result follow-up) must finish
    earlier = set(adapter._item_audio_bytes)  # audio of earlier replies must not satisfy the wait below
    await adapter.send_text("Explain in at least twelve sentences how a current account differs from a savings account.")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if any(n >= SAMPLE_RATE * 2 // 4 for i, n in adapter._item_audio_bytes.items() if i not in earlier):
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("no assistant audio before barge-in")
    started = time.monotonic()
    for o in range(0, len(pcm), 960):
        chunk = pcm[o:o + 960]
        await adapter.send_audio(chunk, {"sample_count": len(chunk) // 2, "sample_rate": SAMPLE_RATE})
        await asyncio.sleep(0.02)
    return {"barge_in": "synthetic_speech", "path": "provider_speech_detection", "turn_detection": "manual",
            "speech_ms": len(pcm) * 1000 // (SAMPLE_RATE * 2), "stream_s": round(time.monotonic() - started, 2)}
