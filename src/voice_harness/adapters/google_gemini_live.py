"""Google Gemini Live adapter (Vertex AI, ``gemini-3.8-live``).

Protocol facts come from the Phase 0 probe (``artifacts/phase0/google_gemini-*``) and
``docs/PHASE4_NOTES_google.md``:

* ``wss://<region>-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent``
  (``aiplatform.googleapis.com`` for ``global``) with ``Authorization: Bearer <OAuth access token>``. The token
  comes from Application Default Credentials, in process, and is never logged, stored or emitted. The Gemini
  Developer API (API keys) is not used.
* First client message ``{"setup": {...}}`` with the full model resource path; the server answers
  ``setupComplete`` (carrying a session id, no model echo, so the returned model version is unresolved).
* Client messages carry exactly one of ``clientContent`` (text), ``realtimeInput`` (``audio`` as
  ``audio/pcm;rate=16000``, ``activityStart/End``, ``audioStreamEnd``) or ``toolResponse``.
* Server messages: ``serverContent`` (``modelTurn.parts[].inlineData`` audio PCM 24 kHz, ``inputTranscription``,
  ``outputTranscription`` as text fragments, ``interrupted``, ``generationComplete``, ``turnComplete``),
  ``toolCall.functionCalls[{id,name,args}]`` and ``usageMetadata`` (token counts by modality) next to
  ``turnComplete``. There is no turn-start message and no cancel or truncate client message: an
  interruption is detected by the server (``interrupted``) and the client flushes its own audio.
* A tool-calling turn ends (``turnComplete``) before the turn that uses the tool result.
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
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed

from ..mock_provider import CapabilityUnsupported
from ..resample import StreamResampler
from . import (
    AUDIO_OVERFLOW_CODE,
    UNSUPPORTED,
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

log = logging.getLogger("voice_harness.adapters.google_gemini_live")

DEFAULT_MODEL = "gemini-3.8-live"  # pinned model for new live sessions
DEFAULT_REGION = "us-central1"
REGION_ENV = "VOICE_HARNESS_GOOGLE_REGION"
PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
CREDENTIALS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
INPUT_RATE = 16000
OUTPUT_RATE = 24000
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105 (public endpoint, not a secret)
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_END = object()


def adc_path() -> Path:
    explicit = os.environ.get(CREDENTIALS_ENV)
    return Path(explicit) if explicit else Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


def _adc_info() -> dict[str, Any]:
    try:
        data = json.loads(adc_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def project_id(environ=None) -> str | None:
    """Project id for the model path: GOOGLE_CLOUD_PROJECT, else the ADC quota project. An identifier, not a secret."""
    environ = os.environ if environ is None else environ
    return environ.get(PROJECT_ENV) or _adc_info().get("quota_project_id") or None


def credentials_available(environ=None) -> tuple[bool, str | None]:
    """(ok, reason). Checks only that ADC and a project exist; never reads or returns credential material."""
    if not _adc_info():
        return False, "NO_GOOGLE_ADC"
    if not project_id(environ):
        return False, "NO_GOOGLE_PROJECT"
    return True, None


def _fetch_access_token() -> str:
    """OAuth access token from Application Default Credentials (authorized_user refresh, else google-auth if installed)."""
    import httpx

    info = _adc_info()
    if info.get("type") == "authorized_user":
        try:
            resp = httpx.post(TOKEN_URL, timeout=15, data={
                "grant_type": "refresh_token", "client_id": info["client_id"],
                "client_secret": info["client_secret"], "refresh_token": info["refresh_token"]})
        except (httpx.HTTPError, KeyError):
            raise ValueError("PROVIDER_AUTH_UNREACHABLE") from None
        token = resp.json().get("access_token") if resp.status_code == 200 else None
        if not token:
            raise ValueError("PROVIDER_AUTH_FAILED")
        return token
    try:  # other ADC types (service account, metadata server) need google-auth, which is not a pinned dependency
        import google.auth
        import google.auth.transport.requests
    except ImportError:
        raise ValueError("PROVIDER_AUTH_UNSUPPORTED_CREDENTIAL_TYPE") from None
    creds, _ = google.auth.default(scopes=[SCOPE])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


class GeminiLiveAdapter:
    # A tool-calling turn ends (turnComplete) before the turn that uses the tool result.
    TOOL_CALL_ENDS_RESPONSE = True

    def __init__(self, *, token_provider=None, url: str | None = None, model: str = DEFAULT_MODEL,
                 region: str | None = None, project: str | None = None, ack_timeout: float = 5.0):
        self._token_provider = token_provider or _fetch_access_token
        self._model = model
        self.region = region or os.environ.get(REGION_ENV) or DEFAULT_REGION
        self._project = project
        host = "aiplatform.googleapis.com" if self.region == "global" else f"{self.region}-aiplatform.googleapis.com"
        self._url = url or f"wss://{host}/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent"
        self._ack_timeout = ack_timeout
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._budget = QueueBudget()
        self.native_sink = None  # optional (adapter, native_type, payload) -> None; preserves every provider event
        self.session_id: str | None = None
        self.closed = False
        self._setup: asyncio.Future | None = None
        self._turn_no = 0
        self._turn_id: str | None = None  # set while a model turn is open
        self._idle = asyncio.Event()
        self._idle.set()
        self._turn_audio_bytes: dict[str, int] = {}
        self._assistant_text = ""
        self._user_text = ""
        self._user_item = 0
        self._pending_calls: dict[str, str] = {}  # call id -> function name
        self._resampler: StreamResampler | None = None
        self._manual = False

    def __repr__(self) -> str:
        return f"GeminiLiveAdapter(model={self._model!r}, region={self.region!r}, closed={self.closed})"

    def connection_info(self) -> dict[str, Any]:
        """Endpoint host, region and package versions for the evidence binding (never the URL path, token or project)."""
        from urllib.parse import urlsplit

        host = None
        try:
            host = urlsplit(self._url).hostname
        except ValueError:
            pass
        return {"endpoint_host": host, "region": self.region,
                "sdk_versions": {"websockets": _package_version("websockets")}}

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": "google_gemini_live",
            "model_id": self._model,
            "region": self.region,
            "profile": "live",
            "audio_in": True,
            "audio_out": True,
            "input_sample_rate": INPUT_RATE,
            "output_sample_rate": OUTPUT_RATE,
            "transcript": "streaming",
            "direct_mcp": False,
            "tool_delivery": "gateway_bridge",
            # No cancel or truncate client message exists; the server reports a barge-in (serverContent.interrupted)
            # and the client flushes its own audio.
            "interrupt_truncate": UNSUPPORTED,
            "interrupt_detection": "supported",
            "interrupt_signals": ["serverContent.interrupted"],
            "usage": "usageMetadata",
            "audio_alignment": "unknown",
            "reconnect_resume": "unknown",
            "semantic_safety": "not_measured",
            "capability_report_id": "cap_google_gemini_3_8_live_20261019",
        }

    # ------------------------------------------------------------------ session
    async def open_session(self, config: dict[str, Any]) -> dict[str, Any]:
        import websockets
        from websockets.exceptions import InvalidStatus

        self.session_id = config.get("session_id")
        model = config.get("model_id", self._model)
        self._model = model
        project = self._project or config.get("project") or project_id()
        if not project:
            raise ValueError("PROVIDER_AUTH_MISSING")
        token = await asyncio.to_thread(self._token_provider)
        try:
            self._ws = await websockets.connect(
                self._url, additional_headers={"Authorization": f"Bearer {token}"},
                max_size=None, open_timeout=config.get("open_timeout", 15))
        except InvalidStatus as e:
            status = e.response.status_code
            raise ValueError("PROVIDER_AUTH_FAILED" if status in (401, 403) else f"PROVIDER_HTTP_{status}") from None
        except (OSError, TimeoutError) as e:
            raise ValueError(f"PROVIDER_UNREACHABLE:{type(e).__name__}") from None
        finally:
            del token
        self._manual = config.get("turn_detection", "server_vad") == "manual"
        setup: dict[str, Any] = {
            "model": f"projects/{project}/locations/{self.region}/publishers/google/models/{model}",
            "generationConfig": {"responseModalities": ["AUDIO"]},
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
        }
        if config.get("voice"):
            setup["generationConfig"]["speechConfig"] = {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config["voice"]}}}
        if config.get("instructions"):
            setup["systemInstruction"] = {"parts": [{"text": config["instructions"]}]}
        if self._manual:
            setup["realtimeInputConfig"] = {"automaticActivityDetection": {"disabled": True}}
        decls = [{"name": t["name"], "description": t.get("description", ""),
                  "parameters": t.get("parameters", {"type": "object", "properties": {}})}
                 for t in config.get("tools", [])]
        if decls:
            setup["tools"] = [{"functionDeclarations": decls}]
        self._setup = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read_loop())
        await self._send({"setup": setup})
        try:
            complete = await asyncio.wait_for(self._setup, 15)
        except TimeoutError:
            raise ValueError("PROVIDER_TIMEOUT:setupComplete") from None
        return {
            "session_id": self.session_id,
            "provider_session_id": (complete or {}).get("sessionId"),
            "model_id": None,  # setupComplete does not echo the model: the returned version is unresolved
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
        rate = metadata.get("sample_rate", INPUT_RATE)
        if rate != INPUT_RATE:
            if self._resampler is None or self._resampler.src_rate != rate:
                self._resampler = StreamResampler(rate, INPUT_RATE)
            data = self._resampler.process(data)
            if not data:
                return
        await self._send({"realtimeInput": {"audio": {
            "data": base64.b64encode(data).decode(), "mimeType": f"audio/pcm;rate={INPUT_RATE}"}}})

    async def send_text(self, text: str, *, respond: bool = True) -> None:
        """Add a user text turn; ``respond=False`` adds context without asking for a reply."""
        await self._send({"clientContent": {
            "turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": respond}})

    async def request_turn(self) -> None:
        """Manual turn mode: end the caller's activity. With automatic activity detection the server decides."""
        if self._manual:
            await self._send({"realtimeInput": {"activityEnd": {}}})

    async def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if call_id != result.get("call_id") or call_id not in self._pending_calls:
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        if self._turn_id is not None:  # the tool-calling turn must finish first
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._idle.wait(), self._ack_timeout)
        name = self._pending_calls.pop(call_id)
        await self._send({"toolResponse": {"functionResponses": [
            {"id": call_id, "name": name, "response": result}]}})
        return {"local_send": "accepted", "provider_acknowledgement": "not_reported", "call_id": call_id}

    async def cancel_tool(self, call_id: str, reason: str) -> dict[str, Any]:
        return {"call_id": call_id, "cancelled": False, "reason": "GATEWAY_OWNS_EXECUTION"}

    async def interrupt(self, item_id: str | None, audio_end_ms: int) -> dict[str, Any]:
        raise CapabilityUnsupported(
            "Gemini Live has no cancel or truncate client message: a barge-in is detected by the server "
            "(serverContent.interrupted) and the client must flush its own audio")

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
                if self.native_sink is not None:
                    self.native_sink("google_gemini_live", next(iter(k for k in ev if k != "usageMetadata"), "unknown"), ev)
                log.debug("in %s", json.dumps(redact(ev))[:500])
                try:
                    self._handle(ev)
                except (KeyError, TypeError, ValueError):
                    self._emit(ProviderError, code="MALFORMED_PROVIDER_EVENT", message=",".join(sorted(ev))[:80])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # connection dropped
            if not self.closed:
                if isinstance(e, ConnectionClosed):
                    self._emit_closed()
                else:
                    self._emit(ProviderError, code="CONNECTION_LOST", message=type(e).__name__, fatal=True)
        else:
            if not self.closed:
                self._emit_closed()
        finally:
            if self._setup is not None and not self._setup.done():
                self._setup.cancel()
            if not self.closed:
                self._queue.put_nowait(_END)

    def _emit_closed(self) -> None:
        """Close 1008 (policy violation) is how Vertex refused a model it would not serve and rejects bad requests;
        1007 an invalid message. The reason text is provider-authored and carries no credentials."""
        code = getattr(self._ws, "close_code", None)
        reason = str(getattr(self._ws, "close_reason", "") or "")[:200]
        label = {1008: "PROVIDER_REFUSED", 1007: "PROVIDER_INVALID_MESSAGE"}.get(code, "CONNECTION_CLOSED")
        self._emit(ProviderError, code=label, message=f"close {code}: {reason}".strip(), fatal=True)

    def _open_turn(self) -> None:
        if self._turn_id is None:
            self._turn_no += 1
            self._turn_id = f"turn_{self._turn_no}"
            self._idle.clear()
            self._assistant_text = ""
            self._emit(TurnStart)

    def _flush_user(self) -> None:
        if self._user_text:
            self._emit(TranscriptFinal, role="user", item_id=f"user_{self._user_item}", text=self._user_text.strip())
            self._user_text = ""
            self._user_item += 1

    def _flush_assistant(self) -> None:
        if self._assistant_text:
            self._emit(TranscriptFinal, role="assistant", item_id=self._turn_id, text=self._assistant_text.strip())
            self._assistant_text = ""

    def _handle(self, ev: dict) -> None:
        if "setupComplete" in ev:
            if self._setup is not None and not self._setup.done():
                self._setup.set_result(ev["setupComplete"])
            return
        sc = ev.get("serverContent")
        if sc:
            self._handle_content(sc)
        tc = ev.get("toolCall")
        if tc:
            self._open_turn()
            for call in tc.get("functionCalls", []):
                args = call.get("args") or {}
                if not isinstance(args, dict) or not call.get("id"):
                    self._emit(ProviderError, code="TOOL_ARGUMENTS_INVALID", message=f"call_id={call.get('id')}")
                    continue
                self._pending_calls[call["id"]] = call["name"]
                self._emit(ToolCall, call_id=call["id"], name=call["name"], arguments=args)
        usage = ev.get("usageMetadata")
        if usage:
            self._emit(Usage, input_tokens=usage.get("promptTokenCount"), output_tokens=usage.get("candidatesTokenCount"),
                       raw=usage)
        if "goAway" in ev:
            self._emit(ProviderError, code="GO_AWAY", message=str(ev["goAway"])[:200], fatal=False)
        if sc and sc.get("turnComplete"):
            self._flush_user()
            self._flush_assistant()
            if self._turn_id is not None:
                self._emit(TurnEnd)
            self._turn_id = None
            self._idle.set()

    def _handle_content(self, sc: dict) -> None:
        if "inputTranscription" in sc:
            text = sc["inputTranscription"].get("text", "")
            if text:
                self._user_text += text
                self._emit(TranscriptDelta, role="user", item_id=f"user_{self._user_item}", text=text)
            if sc["inputTranscription"].get("finished"):
                self._flush_user()
        if sc.get("interrupted"):
            tid = self._turn_id
            ms = self._turn_audio_bytes.get(tid, 0) * 1000 // (OUTPUT_RATE * 2) if tid else None
            self._emit(Interrupted, item_id=tid, audio_end_ms=ms)
            self._flush_assistant()
            if tid is not None:
                self._emit(TurnEnd)
            self._turn_id = None
            self._idle.set()
            return
        for part in (sc.get("modelTurn") or {}).get("parts", []):
            blob = part.get("inlineData")
            if not blob:
                continue
            self._open_turn()
            pcm = base64.b64decode(blob["data"])
            self._turn_audio_bytes[self._turn_id] = self._turn_audio_bytes.get(self._turn_id, 0) + len(pcm)
            self._emit(AudioOut, item_id=self._turn_id, pcm=pcm, sample_rate=OUTPUT_RATE, sample_count=len(pcm) // 2)
        if "outputTranscription" in sc:
            ot = sc["outputTranscription"]
            if ot.get("text"):
                self._open_turn()
                self._assistant_text += ot["text"]
                self._emit(TranscriptDelta, role="assistant", item_id=self._turn_id, text=ot["text"])
            if ot.get("finished") and self._turn_id is not None:
                self._flush_assistant()


def conformance_factory():
    """AC-09 driver: (adapter, prime). Config is baked in; the harness opens with only a session id."""
    tool = {"name": "get_account_summary", "description": "Account summary",
            "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}}}

    class _Conformance(GeminiLiveAdapter):
        async def open_session(self, config):
            return await super().open_session({
                **config, "tools": [tool], "turn_detection": "server_vad",
                "instructions": "Banking test assistant. Keep answers to one short sentence unless asked for more."
                                " Use tools for accounts."})

    adapter = _Conformance()

    async def prime(dimension: str) -> dict[str, Any] | None:
        if dimension == "audio_out":
            # The model may call the tool for a bare greeting prompt, so say outright that no tool is wanted.
            await adapter.send_text("Just greet me out loud in one short sentence. This is not about an account; do not call any tool.")
        elif dimension == "tool_result_correlation":
            await asyncio.sleep(3)  # let the greeting finish
            await adapter.send_text("What is the balance for account ACC-001?")
        return None  # interruption is not exercised through the AC-09 driver: truncation is unsupported (excluded)

    return adapter, prime


__all__ = ["DEFAULT_MODEL", "GeminiLiveAdapter", "conformance_factory", "credentials_available", "project_id",
           "synth_caller_pcm"]
