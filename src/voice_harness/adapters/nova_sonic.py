"""Amazon Nova Sonic (amazon.nova-2-5-sonic, Bedrock InvokeModelWithBidirectionalStream) adapter.

Protocol facts (all observed in artifacts/phase0, see nova-capability-report.json):
* sessionStart -> promptStart -> SYSTEM text content -> one always-open interactive AUDIO
  content for the whole prompt. Text-only prompts are rejected ("must have at least one audio
  content") and a >55 s gap without audio bytes ends the stream, so the adapter pumps 20 ms
  of 16 kHz PCM16 silence whenever no caller audio is queued.
* Input is 16 kHz s16le mono (caller audio, 24 kHz by default, is resampled); output is
  24 kHz s16le mono.
* toolUse carries a provider ``toolUseId``; the toolResult must echo it exactly, otherwise the
  stream ends with a ValidationException ("Not expecting a tool result with toolInvocationId=").
* Barge-in is reported as contentEnd stopReason=INTERRUPTED and/or an in-band
  ``{"interrupted": true}`` text output. There is NO truncate-at-offset primitive: the client
  must flush its own buffered audio; ``interrupt()`` therefore raises CapabilityUnsupported.

Transport: the Python smithy SDK (aws_sdk_bedrock_runtime) is not a dependency of this repo, so
the default transport drives ``scripts/probe/nova_bridge.mjs`` (the SDK proven by the Phase 0
probe) over JSON lines. Any object with the ``NovaTransport`` shape can be injected via
``config["transport"]`` (used by the offline tests).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from ..resample import StreamResampler
from . import (
    UNSUPPORTED,
    AdapterEvent,
    AudioOut,
    CapabilityUnsupported,
    Interrupted,
    ProviderError,
    ToolCall,
    TranscriptDelta,
    TranscriptFinal,
    TurnEnd,
    TurnStart,
    Usage,
)

MODEL_ID = "amazon.nova-2-5-sonic"
REGION = "us-east-1"
INPUT_RATE = 16000
OUTPUT_RATE = 24000
CHUNK_MS = 20
SILENCE_CHUNK = b"\x00" * (INPUT_RATE * CHUNK_MS // 1000 * 2)  # 640 bytes
AUDIO_TIMEOUT_S = 55

_REPO = Path(__file__).resolve().parents[3]
# Overridable for relocated installs: HARNESS_NOVA_BRIDGE (script; node_modules resolves beside it), HARNESS_NODE_BIN.
BRIDGE_SCRIPT = Path(os.environ.get("HARNESS_NOVA_BRIDGE") or _REPO / "scripts" / "probe" / "nova_bridge.mjs")
NODE_BIN = os.environ.get("HARNESS_NODE_BIN") or "node"
SESSION_ENV = Path(os.environ.get("HARNESS_AWS_SESSION_ENV") or _REPO / ".local" / "aws-session.env")
CAPABILITY_REPORT = _REPO / "artifacts" / "phase0" / "nova-capability-report.json"


def runtime_status() -> list[str]:
    """Cheap preflight: reason codes (empty = adapter can run here). No network, no credentials."""
    import shutil

    reasons = []
    if not shutil.which(NODE_BIN):
        reasons.append("NODE_MISSING")
    if not BRIDGE_SCRIPT.is_file():
        reasons.append("BRIDGE_MISSING")
    elif not (BRIDGE_SCRIPT.parent / "node_modules" / "@aws-sdk" / "client-bedrock-runtime").is_dir():
        reasons.append("BRIDGE_DEPS_MISSING")
    return reasons


def runtime_available() -> bool:
    return not runtime_status()


def map_error(name: str, message: str) -> tuple[str, bool]:
    """Map a Bedrock exception to (normalised code, fatal)."""
    m = message or ""
    if name == "ValidationException":
        if "Not expecting a tool result" in m:
            return "TOOL_RESULT_UNKNOWN_ID", True
        if "Timed out waiting for audio" in m:
            return "AUDIO_TIMEOUT", True
        if "at least one audio content" in m:
            return "TEXT_ONLY_PROMPT_REJECTED", True
        return "VALIDATION_ERROR", True
    table = {
        "ThrottlingException": "RATE_LIMITED",
        "ServiceQuotaExceededException": "QUOTA_EXCEEDED",
        "ModelTimeoutException": "MODEL_TIMEOUT",
        "ModelStreamErrorException": "MODEL_STREAM_ERROR",
        "ModelNotReadyException": "MODEL_NOT_READY",
        "InternalServerException": "PROVIDER_INTERNAL_ERROR",
        "ServiceUnavailableException": "PROVIDER_UNAVAILABLE",
        "AccessDeniedException": "AUTH_DENIED",
        "ExpiredTokenException": "AUTH_EXPIRED",
        "UnrecognizedClientException": "AUTH_INVALID",
        "ResourceNotFoundException": "MODEL_NOT_FOUND",
    }
    return table.get(name, f"PROVIDER_{name or 'UNKNOWN'}".upper()), True


class NodeBridgeTransport:
    """JSON-lines bridge to scripts/probe/nova_bridge.mjs (Node + @aws-sdk/client-bedrock-runtime)."""

    def __init__(self, region: str = REGION, model_id: str = MODEL_ID, env: dict | None = None):
        self.region, self.model_id, self.env = region, model_id, env
        self.proc: asyncio.subprocess.Process | None = None
        self.request_id: str | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            NODE_BIN, str(BRIDGE_SCRIPT),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=str(BRIDGE_SCRIPT.parent),
            env={**os.environ, **(self.env or {})}, limit=1 << 24,
        )
        await self.send({"_open": {"region": self.region, "modelId": self.model_id}})

    async def send(self, msg: dict) -> None:
        if not (self.proc and self.proc.stdin):
            raise RuntimeError("NOVA_BRIDGE_NOT_STARTED")
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def recv(self) -> dict | None:
        if not (self.proc and self.proc.stdout):
            raise RuntimeError("NOVA_BRIDGE_NOT_STARTED")
        line = await self.proc.stdout.readline()
        return json.loads(line) if line else None

    async def close(self) -> None:
        if not self.proc:
            return
        with contextlib.suppress(Exception):
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), 5)
        except TimeoutError:
            self.proc.kill()
            await self.proc.wait()


class NovaSonicAdapter:
    def __init__(self, pump_interval_s: float = CHUNK_MS / 1000):
        self.pump_interval_s = pump_interval_s
        self.session_id: str | None = None
        self.closed = False
        self.transport: Any = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._audio_buf = bytearray()
        self._input_resampler: StreamResampler | None = None
        self._pump: asyncio.Task | None = None
        self._reader: asyncio.Task | None = None
        self._prompt = ""
        self._audio_content = ""
        self._contents: dict[str, dict] = {}
        self._interrupted_contents: set[str] = set()
        self._tool_ids: set[str] = set()
        self._turn_open = False
        self.stats = {"silence_chunks": 0, "audio_chunks": 0, "request_id": None}

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> dict[str, Any]:
        rows: dict[str, Any] = {}
        report_id = None
        try:
            rep = json.loads(CAPABILITY_REPORT.read_text())
            report_id = rep.get("report_id")
            rows = {r["name"]: {"status": r["status"], "tool_mode": r.get("tool_mode")} for r in rep["rows"]}
        except (OSError, ValueError, KeyError):
            pass
        return {
            "provider": "aws_nova_sonic",
            "model_id": MODEL_ID,
            "region": REGION,
            "profile": "live_provider",
            "audio_in": True,
            "audio_out": True,
            "input_sample_rate": INPUT_RATE,
            "output_sample_rate": OUTPUT_RATE,
            "transcript": "streaming_speculative_final",
            "direct_mcp": False,
            "tool_delivery": "gateway_bridge",
            "tool_result_correlation": "toolUseId",
            "semantic_safety": "not_measured",
            # No truncate-at-offset primitive; interruption is detected, client flushes its own audio.
            "interrupt_truncate": UNSUPPORTED,
            "interrupt_detection": "supported",
            "interrupt_signals": ["contentEnd.stopReason=INTERRUPTED", "textOutput {\"interrupted\": true}"],
            "cancel_truncate_status": "observed",
            "cancel_truncate_note": "barge-in detected ~0.5 s after speech start; client must flush buffered audio; "
                                    "no truncate-at-offset primitive",
            "audio_alignment": "unsupported",
            "requires_open_audio_stream": True,
            "audio_idle_timeout_s": AUDIO_TIMEOUT_S,
            "text_input": "interactive_text_content_while_audio_open",
            "usage": "usageEvent",
            "reconnect_resume": "unsupported",
            "version_unresolved": True,
            "capability_report_id": report_id,
            "capability_rows": rows,
        }

    # ------------------------------------------------------------------ session
    async def open_session(self, config: dict[str, Any]) -> dict[str, Any]:
        self.session_id = config.get("session_id") or str(uuid.uuid4())
        self.transport = config.get("transport") or NodeBridgeTransport(
            config.get("region", REGION), config.get("model_id", MODEL_ID), config.get("env"))
        await self.transport.start()
        self._prompt, self._audio_content = str(uuid.uuid4()), str(uuid.uuid4())
        tools = config.get("tools") or []
        prompt_start: dict[str, Any] = {
            "promptName": self._prompt,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": OUTPUT_RATE, "sampleSizeBits": 16,
                "channelCount": 1, "voiceId": config.get("voice", "matthew"),
                "encoding": "base64", "audioType": "SPEECH"},
        }
        if tools:
            prompt_start["toolUseOutputConfiguration"] = {"mediaType": "application/json"}
            prompt_start["toolConfiguration"] = {"tools": [
                {"toolSpec": {"name": t["name"], "description": t.get("description", ""),
                              "inputSchema": {"json": json.dumps(t.get("input_schema") or t.get("parameters")
                                                                 or {"type": "object", "properties": {}})}}}
                for t in tools]}
        inf = {"maxTokens": 1024, "topP": 0.9, "temperature": 0.2, **(config.get("inference") or {})}
        await self._send({"sessionStart": {
            "inferenceConfiguration": inf,
            "turnDetectionConfiguration": {"endpointingSensitivity": config.get("endpointing", "MEDIUM")}}})
        await self._send({"promptStart": prompt_start})
        sys_c = str(uuid.uuid4())
        await self._send({"contentStart": {"promptName": self._prompt, "contentName": sys_c, "type": "TEXT",
                                           "interactive": False, "role": "SYSTEM",
                                           "textInputConfiguration": {"mediaType": "text/plain"}}})
        await self._send({"textInput": {"promptName": self._prompt, "contentName": sys_c,
                                        "content": config.get("system_prompt", "You are a helpful assistant.")}})
        await self._send({"contentEnd": {"promptName": self._prompt, "contentName": sys_c}})
        await self._send({"contentStart": {
            "promptName": self._prompt, "contentName": self._audio_content, "type": "AUDIO",
            "interactive": True, "role": "USER",
            "audioInputConfiguration": {"mediaType": "audio/lpcm", "sampleRateHertz": INPUT_RATE,
                                        "sampleSizeBits": 16, "channelCount": 1, "audioType": "SPEECH",
                                        "encoding": "base64"}}})
        self._reader = asyncio.create_task(self._read_loop())
        self._pump = asyncio.create_task(self._pump_loop())
        return {"session_id": self.session_id, "model_id": MODEL_ID, "prompt_name": self._prompt}

    async def _send(self, event: dict) -> None:
        await self.transport.send({"event": event})

    async def _pump_loop(self) -> None:
        """Always-open audio: real audio when queued, otherwise 20 ms of silence."""
        try:
            while not self.closed:
                chunk = bytes(self._audio_buf[: len(SILENCE_CHUNK)])
                del self._audio_buf[: len(SILENCE_CHUNK)]
                if chunk:
                    chunk = chunk.ljust(len(SILENCE_CHUNK), b"\x00")
                    self.stats["audio_chunks"] += 1
                else:
                    chunk = SILENCE_CHUNK
                    self.stats["silence_chunks"] += 1
                await self._send({"audioInput": {"promptName": self._prompt, "contentName": self._audio_content,
                                                 "content": base64.b64encode(chunk).decode()}})
                await asyncio.sleep(self.pump_interval_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # transport died
            await self._emit(ProviderError(session_id=self.session_id, code="TRANSPORT_FAILED",
                                           message=str(exc), fatal=True))

    # ------------------------------------------------------------------ inputs
    async def send_audio(self, data: bytes, metadata: dict[str, Any]) -> None:
        if self.closed or len(data) % 2:
            raise ValueError("INVALID_NOVA_AUDIO")
        if "sample_count" in metadata and len(data) != metadata["sample_count"] * 2:
            raise ValueError("INVALID_NOVA_AUDIO")
        rate = metadata.get("sample_rate", OUTPUT_RATE)  # harness canonical rate is 24 kHz
        if self._input_resampler is None or self._input_resampler.src_rate != rate:
            self._input_resampler = StreamResampler(rate, INPUT_RATE)
        self._audio_buf += self._input_resampler.process(data)

    async def send_text(self, text: str) -> None:
        if self.closed:
            raise ValueError("SESSION_CLOSED")
        c = str(uuid.uuid4())
        await self._send({"contentStart": {"promptName": self._prompt, "contentName": c, "type": "TEXT",
                                           "interactive": True, "role": "USER",
                                           "textInputConfiguration": {"mediaType": "text/plain"}}})
        await self._send({"textInput": {"promptName": self._prompt, "contentName": c, "content": text}})
        await self._send({"contentEnd": {"promptName": self._prompt, "contentName": c}})

    async def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> dict[str, Any]:
        if self.closed:
            raise ValueError("SESSION_CLOSED")
        if result.get("call_id", call_id) != call_id:
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        # The exact provider toolUseId is echoed; an unknown id is rejected by the provider
        # (ValidationException -> ProviderError TOOL_RESULT_UNKNOWN_ID), deliberately not pre-filtered.
        c = str(uuid.uuid4())
        await self._send({"contentStart": {
            "promptName": self._prompt, "contentName": c, "type": "TOOL", "interactive": False, "role": "TOOL",
            "toolResultInputConfiguration": {"toolUseId": call_id, "type": "TEXT",
                                             "textInputConfiguration": {"mediaType": "text/plain"}}}})
        await self._send({"toolResult": {"promptName": self._prompt, "contentName": c,
                                         "content": json.dumps(result, default=str)}})
        await self._send({"contentEnd": {"promptName": self._prompt, "contentName": c}})
        return {"local_send": "accepted", "provider_acknowledgement": "not_reported", "call_id": call_id}

    async def cancel_tool(self, call_id: str, reason: str) -> dict[str, Any]:
        return {"call_id": call_id, "cancelled": False, "reason": "GATEWAY_OWNS_EXECUTION"}

    async def interrupt(self, item_id: str | None, audio_end_ms: int) -> dict[str, Any]:
        raise CapabilityUnsupported(
            "Nova Sonic has no truncate-at-offset primitive (cancel_truncate observed: interruption is "
            "detected via INTERRUPTED/in-band marker; client must flush its own audio)")

    async def request_turn(self) -> None:
        if self.closed:
            raise ValueError("SESSION_CLOSED")  # turn-taking is provider VAD driven

    # ------------------------------------------------------------------ outputs
    async def _emit(self, ev: AdapterEvent) -> None:
        await self._queue.put(ev)

    def _base(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "ts_ms": int(time.time() * 1000)}

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await self.transport.recv()
                if msg is None or msg.get("_closed"):
                    break
                if "_open_ok" in msg:
                    self.stats["request_id"] = msg["_open_ok"].get("requestId")
                    continue
                if "_error" in msg:
                    code, fatal = map_error(msg["_error"].get("name", ""), msg["_error"].get("message", ""))
                    await self._emit(ProviderError(**self._base(), code=code,
                                                   message=msg["_error"].get("message", ""), fatal=fatal))
                    continue
                for ev in self._translate(msg.get("event") or {}):
                    await self._emit(ev)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._emit(ProviderError(**self._base(), code="TRANSPORT_FAILED", message=str(exc), fatal=True))
        finally:
            await self._queue.put(None)

    def _translate(self, ev: dict) -> list[AdapterEvent]:
        if not ev:
            return []
        kind, body = next(iter(ev.items()))
        base = self._base()
        out: list[AdapterEvent] = []
        if kind == "completionStart":
            self._turn_open = True
            out.append(TurnStart(**base, turn_id=body.get("completionId")))
        elif kind == "completionEnd":
            self._turn_open = False
            out.append(TurnEnd(**base, turn_id=body.get("completionId")))
        elif kind == "contentStart":
            stage = None
            with contextlib.suppress(ValueError, TypeError):
                stage = json.loads(body.get("additionalModelFields") or "{}").get("generationStage")
            self._contents[body["contentId"]] = {"role": body.get("role"), "type": body.get("type"), "stage": stage}
        elif kind == "textOutput":
            info = self._contents.get(body.get("contentId"), {})
            text = body.get("content", "")
            if self._is_interrupt_marker(text):
                out += self._interrupted(body.get("contentId"), body.get("completionId"))
            else:
                role = "user" if (body.get("role") or info.get("role")) == "USER" else "assistant"
                cls = TranscriptFinal if info.get("stage") == "FINAL" else TranscriptDelta
                out.append(cls(**base, turn_id=body.get("completionId"), item_id=body.get("contentId"),
                               role=role, text=text))
        elif kind == "audioOutput":
            pcm = base64.b64decode(body.get("content", ""))
            out.append(AudioOut(**base, turn_id=body.get("completionId"), item_id=body.get("contentId"),
                                pcm=pcm, sample_rate=OUTPUT_RATE, sample_count=len(pcm) // 2))
        elif kind == "toolUse":
            raw = body.get("content") or "{}"
            try:
                args = json.loads(raw)
            except ValueError:
                args = {"_raw": raw}
            self._tool_ids.add(body["toolUseId"])
            out.append(ToolCall(**base, turn_id=body.get("completionId"), call_id=body["toolUseId"],
                                name=body["toolName"], arguments=args if isinstance(args, dict) else {"_value": args}))
        elif kind == "contentEnd":
            if body.get("stopReason") == "INTERRUPTED":
                out += self._interrupted(body.get("contentId"), body.get("completionId"))
        elif kind == "usageEvent":
            out.append(Usage(**base, turn_id=body.get("completionId"),
                             input_tokens=body.get("totalInputTokens"), output_tokens=body.get("totalOutputTokens"),
                             raw={"totalTokens": body.get("totalTokens"), "details": body.get("details", {}),
                                  "cumulative": True,
                                  "provider_session_id": body.get("sessionId")}))
        return out

    @staticmethod
    def _is_interrupt_marker(text: str) -> bool:
        try:
            v = json.loads(text)
            return isinstance(v, dict) and v.get("interrupted") is True
        except ValueError:
            return False

    def _interrupted(self, content_id: str | None, completion_id: str | None) -> list[AdapterEvent]:
        key = content_id or ""
        if key in self._interrupted_contents:  # marker + INTERRUPTED stopReason describe the same event
            return []
        self._interrupted_contents.add(key)
        return [Interrupted(**self._base(), turn_id=completion_id, item_id=content_id, audio_end_ms=None)]

    async def receive(self) -> AsyncIterator[AdapterEvent]:
        """Yield normalised events until the stream ends or the adapter is closed."""
        while True:
            ev = await self._queue.get()
            if ev is None:
                self._queue.put_nowait(None)
                return
            yield ev

    async def close(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        if self._pump:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
        with contextlib.suppress(Exception):
            await self._send({"contentEnd": {"promptName": self._prompt, "contentName": self._audio_content}})
            await self._send({"promptEnd": {"promptName": self._prompt}})
            await self._send({"sessionEnd": {}})
        if self.transport:
            with contextlib.suppress(Exception):
                await self.transport.close()
        if self._reader:
            try:
                await asyncio.wait_for(self._reader, 5)
            except (TimeoutError, asyncio.CancelledError, Exception):
                self._reader.cancel()

    def estimate_cost(self, usage: dict[str, Any]) -> dict[str, Any]:
        return {"amount": None, "provenance": "unknown", "unknown_reason": "BEDROCK_PRICING_NOT_ESTABLISHED"}


def conformance_factory():
    """AC-09 driver: (adapter, prime). Needs unexpired credentials in .local/aws-session.env."""
    env = {}
    path = SESSION_ENV
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v.strip("'\"")
    tool = {"name": "get_account_summary", "description": "Account summary",
            "input_schema": {"type": "object", "properties": {"account_id": {"type": "string"}},
                             "required": ["account_id"]}}

    class _Conformance(NovaSonicAdapter):
        async def open_session(self, config):
            return await super().open_session({
                **config, "env": env, "tools": [tool],
                "system_prompt": "Be brief. Use tools for accounts."})

    adapter = _Conformance()

    async def prime(dimension: str) -> None:
        if dimension == "audio_out":
            await adapter.send_text("Say hello.")
        elif dimension == "tool_result_correlation":
            await asyncio.sleep(4)
            await adapter.send_text("What is the balance for account ACC-001?")

    return adapter, prime
