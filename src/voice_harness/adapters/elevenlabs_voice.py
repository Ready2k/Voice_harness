"""ElevenLabs Agents realtime adapter (``wss://api.elevenlabs.io/v1/convai/conversation``).

Protocol, from the ElevenLabs Agents WebSocket API (not yet probed against the live service; see
``docs/PHASE4_NOTES_elevenlabs.md`` for what is still unverified):

* The conversation runs against a stored agent (``elevenlabs_agent.py`` creates it). Tools, ASR and TTS formats live
  on the agent; the prompt, first message, language and voice are per-session overrides sent in
  ``conversation_initiation_client_data``. The server answers ``conversation_initiation_metadata``.
* Audio is PCM s16le at 24 kHz both ways (the agent is configured for ``pcm_24000``), so no resampling. Caller audio
  is ``{"user_audio_chunk": <base64>}``; agent audio is ``audio`` events carrying ``audio_base_64``.
* Turn detection is always the server's VAD: there is no manual commit, so ``request_turn`` is a no-op and the
  caller's trailing silence ends the turn. ``interruption`` is a server-detected barge-in; there is no client
  cancel or truncate message, so ``interrupt`` raises ``CapabilityUnsupported``.
* ``ping`` events must be answered with ``pong`` (same ``event_id``) or the server drops the session.
* Client tools: ``client_tool_call`` (``tool_call_id``/``tool_name``/``parameters``), answered with
  ``client_tool_result``. ``contextual_update`` adds context without a reply; ``user_message`` asks for one.
* There is no response start/end marker and no usage event on the socket, and ``agent_response`` (the text) arrives
  *before* its audio has finished streaming (observed live, 2026-10-25). A turn is therefore opened by the first
  audio, agent text or tool call and closed once the agent has answered (``agent_response`` or a tool call) and its
  audio has been quiet for ``IDLE_S``; an interruption closes it at once. Cost is not reported.

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

from ..mock_provider import CapabilityUnsupported
from . import (
    AUDIO_OVERFLOW_CODE,
    UNSUPPORTED,
    AudioOut,
    Interrupted,
    ProviderError,
    QueueBudget,
    ToolCall,
    TranscriptFinal,
    TurnEnd,
    TurnStart,
)
from .elevenlabs_agent import (
    AGENT_ENV,
    AUDIO_FORMAT,
    DEFAULT_LLM,
    DEFAULT_VOICE,
    KEY_ENV,
    agent_id,
    fingerprint,
    load_state,
)
from .openai_realtime import _package_version

log = logging.getLogger("voice_harness.adapters.elevenlabs_voice")

DEFAULT_URL = "wss://api.elevenlabs.io/v1/convai/conversation"
DEFAULT_MODEL = "elevenlabs-agent"  # the platform has no single model id: the agent's LLM is recorded as returned
SAMPLE_RATE = 24000
IDLE_S = 0.7  # audio silence that ends a response the agent has already answered
NO_AUDIO_S = 3.0  # an answer that produced no audio at all
SILENCE_MAX_S = 6  # longest the adapter keeps feeding silence while it waits for the server to hear the caller stop
FATAL_CODES = {"invalid_api_key", "authentication_error", "unauthorized", "quota_exceeded", "agent_not_found"}
_END = object()
_SECRET_KEYS = {"xi-api-key", "authorization", "api_key"}
_AUDIO_KEYS = {"audio_base_64", "user_audio_chunk"}


def redact(obj: Any) -> Any:
    """Log-safe copy: audio payloads reduced to their length, credentials removed."""
    if isinstance(obj, dict):
        return {k: (f"<b64 len={len(v)}>" if k in _AUDIO_KEYS and isinstance(v, str)
                    else "<redacted>" if k.lower() in _SECRET_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def credentials_available(environ=None) -> tuple[bool, str | None]:
    """(ok, reason): an API key and a provisioned agent whose tools match the live tool list."""
    environ = os.environ if environ is None else environ
    if not environ.get(KEY_ENV):
        return False, "NO_ELEVENLABS_API_KEY"
    if not agent_id(environ):
        return False, "NO_ELEVENLABS_AGENT"
    return True, None


class ElevenLabsVoiceAdapter:
    # A tool-calling response is closed at the tool call; the reply that uses the result is a new turn.
    TOOL_CALL_ENDS_RESPONSE = True

    def __init__(self, *, api_key: str | None = None, url: str | None = None, agent: str | None = None,
                 model: str = DEFAULT_MODEL, open_timeout: float = 15.0, idle_s: float = IDLE_S):
        self._api_key = api_key if api_key is not None else os.environ.get(KEY_ENV)
        self._agent = agent
        self._url = url or DEFAULT_URL
        self._model = model
        self._open_timeout = open_timeout
        self._idle_s = idle_s
        self._answered = False  # agent_response or tool call seen for the open turn: only audio silence is awaited
        self._close_timer: asyncio.TimerHandle | None = None
        self._silence: asyncio.Task | None = None
        self._turn_no_at_request = self._user_at_request = 0
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._budget = QueueBudget()
        self.native_sink = None  # optional (adapter, native_type, payload) -> None; preserves every provider event
        self.session_id: str | None = None
        self.conversation_id: str | None = None
        self.closed = False
        self._ready: asyncio.Future | None = None
        self._turn_no = 0
        self._turn_id: str | None = None  # set while a response is open
        self._turn_audio_bytes: dict[str, int] = {}
        self._pending_calls: dict[str, str] = {}  # call id -> tool name
        self._user_item = 0

    def __repr__(self) -> str:
        return f"ElevenLabsVoiceAdapter(model={self._model!r}, closed={self.closed})"

    def connection_info(self) -> dict[str, Any]:
        """Endpoint host and client package versions for the evidence binding (never the URL path, query or key)."""
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
            "provider": "elevenlabs_agents",
            "model_id": self._model,
            "profile": "live",
            "audio_in": True,
            "audio_out": True,
            "input_sample_rate": SAMPLE_RATE,
            "output_sample_rate": SAMPLE_RATE,
            "transcript": "final_only",
            "direct_mcp": False,
            "tool_delivery": "gateway_bridge",
            # No cancel or truncate client message exists; the server reports a barge-in (interruption).
            "interrupt_truncate": UNSUPPORTED,
            "interrupt_detection": "supported",
            "interrupt_signals": ["interruption"],
            "usage": "not_reported",
            "audio_alignment": "unknown",
            "reconnect_resume": "unknown",
            "semantic_safety": "not_measured",
            "capability_report_id": "cap_elevenlabs_agents_20261024",
        }

    # ------------------------------------------------------------------ session
    async def open_session(self, config: dict[str, Any]) -> dict[str, Any]:
        import websockets
        from websockets.exceptions import InvalidStatus

        if not self._api_key:
            raise ValueError("PROVIDER_AUTH_MISSING")
        agent = self._agent or config.get("agent_id") or agent_id()
        if not agent:
            raise ValueError("PROVIDER_AGENT_MISSING")
        tools = config.get("tools", [])
        stored = load_state().get("tools_fingerprint")
        if tools and not os.environ.get(AGENT_ENV) and stored and stored != fingerprint(tools):
            raise ValueError("PROVIDER_AGENT_TOOLS_STALE")  # re-run: python -m voice_harness.adapters.elevenlabs_agent setup
        self.session_id = config.get("session_id")
        sep = "&" if "?" in self._url else "?"
        try:
            self._ws = await websockets.connect(
                f"{self._url}{sep}agent_id={agent}", additional_headers={"xi-api-key": self._api_key},
                max_size=None, open_timeout=config.get("open_timeout", self._open_timeout))
        except InvalidStatus as e:
            raise ValueError(f"PROVIDER_HTTP_{e.response.status_code}") from None
        except (OSError, TimeoutError) as e:
            raise ValueError(f"PROVIDER_UNREACHABLE:{type(e).__name__}") from None
        self._ready = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read_loop())
        override: dict[str, Any] = {"agent": {"first_message": "", "language": config.get("language", "en")},
                                    "tts": {"voice_id": config.get("voice") or DEFAULT_VOICE}}
        prompt: dict[str, Any] = {}
        if config.get("instructions"):
            prompt["prompt"] = config["instructions"]
        if config.get("llm"):
            prompt["llm"] = config["llm"]
        if prompt:
            override["agent"]["prompt"] = prompt
        await self._send({"type": "conversation_initiation_client_data", "conversation_config_override": override})
        try:
            meta = await asyncio.wait_for(self._ready, 15)
        except TimeoutError:
            raise ValueError("PROVIDER_TIMEOUT:conversation_initiation_metadata") from None
        self.conversation_id = meta.get("conversation_id")
        for key in ("agent_output_audio_format", "user_input_audio_format"):
            if meta.get(key) not in (None, AUDIO_FORMAT):
                raise ValueError(f"PROVIDER_AUDIO_FORMAT:{key}={meta.get(key)}")
        return {
            "session_id": self.session_id,
            "provider_session_id": self.conversation_id,
            # The metadata event carries no model or LLM name: the agent's configured LLM is not observable here.
            "model_id": None,
        }

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
        await self._send({"user_audio_chunk": base64.b64encode(data).decode()})

    async def send_text(self, text: str, *, respond: bool = True) -> None:
        """Add a user text turn; ``respond=False`` adds context without asking for a reply."""
        await self._send({"type": "user_message", "text": text} if respond else {"type": "contextual_update", "text": text})

    async def request_turn(self) -> None:
        """Server VAD owns turn detection and there is no commit message. It only decides once it hears silence, and
        the caller's stream stops at its last frame, so keep real-time silence flowing until the server answers."""
        self._turn_no_at_request, self._user_at_request = self._turn_no, self._user_item
        if self._silence is None or self._silence.done():
            self._silence = asyncio.get_running_loop().create_task(self._feed_silence())

    async def _feed_silence(self) -> None:
        frame = base64.b64encode(bytes(SAMPLE_RATE // 50 * 2)).decode()  # 20 ms
        try:
            for _ in range(SILENCE_MAX_S * 50):
                if self.closed or self._turn_no > self._turn_no_at_request or self._user_item > self._user_at_request:
                    return
                await self._send({"user_audio_chunk": frame})
                await asyncio.sleep(0.02)
        except Exception:  # the connection closing ends the feed; the reader reports the loss
            return

    async def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if call_id != result.get("call_id") or call_id not in self._pending_calls:
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        self._pending_calls.pop(call_id)
        self._close_turn()  # the tool-calling response ends when its result goes back, so the reply is its own turn
        await self._send({"type": "client_tool_result", "tool_call_id": call_id,
                          "result": json.dumps(result, sort_keys=True), "is_error": False})
        return {"local_send": "accepted", "provider_acknowledgement": "not_reported", "call_id": call_id}

    async def cancel_tool(self, call_id: str, reason: str) -> dict[str, Any]:
        return {"call_id": call_id, "cancelled": False, "reason": "GATEWAY_OWNS_EXECUTION"}

    async def interrupt(self, item_id: str | None, audio_end_ms: int) -> dict[str, Any]:
        raise CapabilityUnsupported(
            "ElevenLabs Agents has no cancel or truncate client message: a barge-in is detected by the server "
            "(interruption) and the client must flush its own audio")

    async def close(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        self._cancel_timer()
        if self._silence is not None:
            self._silence.cancel()
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
        ev = cls(session_id=self.session_id, turn_id=self._turn_id, ts_ms=int(time.monotonic() * 1000), **kw)
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
                kind = ev.get("type") or "unknown"
                if kind == "ping":
                    event_id = (ev.get("ping_event") or {}).get("event_id")
                    with contextlib.suppress(Exception):
                        await self._send({"type": "pong", "event_id": event_id})
                    continue  # keep-alive, carries no session state
                if self.native_sink is not None:
                    self.native_sink("elevenlabs_agents", kind, ev)
                log.debug("in %s", json.dumps(redact(ev))[:500])
                try:
                    self._handle(kind, ev)
                except (KeyError, TypeError, ValueError):
                    self._emit(ProviderError, code="MALFORMED_PROVIDER_EVENT", message=str(kind)[:80])
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
            if self._ready is not None and not self._ready.done():
                self._ready.cancel()
            if not self.closed:
                self._queue.put_nowait(_END)

    def _open_turn(self) -> None:
        if self._turn_id is None:
            self._turn_no += 1
            self._turn_id = f"turn_{self._turn_no}"
            self._emit(TurnStart)

    def _cancel_timer(self) -> None:
        if self._close_timer is not None:
            self._close_timer.cancel()
            self._close_timer = None

    def _arm_close(self, delay: float) -> None:
        self._cancel_timer()
        self._close_timer = asyncio.get_running_loop().call_later(delay, self._close_turn)

    def _close_turn(self) -> None:
        self._cancel_timer()
        self._answered = False
        if self._turn_id is not None:
            self._emit(TurnEnd)
            self._turn_id = None

    def _handle(self, kind: str, ev: dict) -> None:
        if kind == "conversation_initiation_metadata":
            if self._ready is not None and not self._ready.done():
                self._ready.set_result(ev.get("conversation_initiation_metadata_event") or {})
        elif kind == "audio":
            audio = ev["audio_event"]
            pcm = base64.b64decode(audio["audio_base_64"])
            self._open_turn()
            self._turn_audio_bytes[self._turn_id] = self._turn_audio_bytes.get(self._turn_id, 0) + len(pcm)
            self._emit(AudioOut, item_id=self._turn_id, pcm=pcm, sample_rate=SAMPLE_RATE, sample_count=len(pcm) // 2)
            if self._answered:
                self._arm_close(self._idle_s)  # more audio for a response already answered: restart the quiet window
        elif kind == "user_transcript":
            text = (ev.get("user_transcription_event") or {}).get("user_transcript", "")
            if text:
                self._user_item += 1
                self._emit(TranscriptFinal, role="user", item_id=f"user_{self._user_item}", text=text)
        elif kind == "agent_response":
            self._open_turn()
            text = (ev.get("agent_response_event") or {}).get("agent_response", "")
            self._emit(TranscriptFinal, role="assistant", item_id=self._turn_id, text=text)
            self._answered = True
            self._arm_close(self._idle_s if self._turn_audio_bytes.get(self._turn_id) else NO_AUDIO_S)
        elif kind == "interruption":
            if not self._turn_audio_bytes:  # nothing was ever spoken, so nothing was cut off: the server's VAD blipped on line noise
                return
            turn = self._turn_id
            ms = self._turn_audio_bytes.get(turn, 0) * 1000 // (SAMPLE_RATE * 2) if turn else None
            self._emit(Interrupted, item_id=turn, audio_end_ms=ms)
            self._close_turn()
        elif kind == "client_tool_call":
            call = ev["client_tool_call"]
            params = call.get("parameters") or {}
            if not isinstance(params, dict) or not call.get("tool_call_id"):
                self._emit(ProviderError, code="TOOL_ARGUMENTS_INVALID", message=f"call_id={call.get('tool_call_id')}")
                return
            self._open_turn()
            self._pending_calls[call["tool_call_id"]] = call["tool_name"]
            self._emit(ToolCall, call_id=call["tool_call_id"], name=call["tool_name"], arguments=params)
            # The tool-calling response ends once any preamble audio has played; the reply that uses the result is a new turn.
            self._answered = True
            self._arm_close(self._idle_s)
        elif kind == "error":
            err = ev.get("error") or ev.get("message") or {}
            err = err if isinstance(err, dict) else {"message": str(err)}
            code = err.get("code") or err.get("type") or "provider_error"
            self._emit(ProviderError, code=str(code), message=str(err.get("message", ""))[:300], fatal=code in FATAL_CODES)


def conformance_factory():
    """AC-09 driver: (adapter, prime). Config is baked in; the harness opens with only a session id."""

    class _Conformance(ElevenLabsVoiceAdapter):
        async def open_session(self, config):
            return await super().open_session({
                **config, "tools": [],  # tools live on the agent; the conformance run uses the provisioned harness agent
                "instructions": "Banking test assistant. Keep answers to one short sentence unless asked for more."
                                " Use tools for accounts."})

    adapter = _Conformance()

    async def prime(dimension: str) -> dict[str, Any] | None:
        if dimension == "audio_out":
            await adapter.send_text("Say hello.")
        elif dimension == "tool_result_correlation":
            await asyncio.sleep(3)  # let the greeting finish
            await adapter.send_text("Use your policy tool to look up the identity verification policy and tell me what it says.")
        return None  # interruption is not exercised through the AC-09 driver: truncation is unsupported (excluded)

    return adapter, prime


__all__ = ["DEFAULT_LLM", "DEFAULT_MODEL", "ElevenLabsVoiceAdapter", "conformance_factory", "credentials_available"]
