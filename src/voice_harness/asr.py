"""Independent ASR assessment source (spec section 6; AC-14 live assessment).

Backends sit behind a small interface. ``ScriptedAsr`` is offline and deterministic for
tests and is never ``live``: it can prove enforcement mechanics only, never ASR
accuracy. ``OpenAITranscriptionAsr`` is opt-in (``VOICE_HARNESS_LIVE_ASR=openai`` plus
``OPENAI_API_KEY`` in the environment), for synthetic audio only. Nothing is selected
by default, so strict speech approval is ``unavailable`` unless a backend is configured.
"""

from __future__ import annotations

import hashlib
import io
import os
import wave
from dataclasses import dataclass
from typing import Protocol

LIVE_FLAG = "VOICE_HARNESS_LIVE_ASR"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"


class AsrUnavailable(Exception):
    """Raised with a stable reason code; never carries credentials or response bodies."""


@dataclass(frozen=True)
class AsrResult:
    text: str
    backend: str
    model: str
    live: bool
    usage: dict | None = None  # provider-returned usage, verbatim (None when the provider returned none)


class AsrBackend(Protocol):
    name: str
    model: str
    live: bool

    def available(self) -> tuple[bool, str | None]: ...
    def transcribe(self, pcm: bytes, rate: int) -> AsrResult: ...


class ScriptedAsr:
    """Offline backend: transcripts keyed by audio SHA-256, else a fixed queue."""

    name = "scripted_offline"
    model = "scripted-v1"
    live = False

    def __init__(self, by_audio_sha256: dict[str, str] | None = None, queue: list[str] | None = None):
        self.by_audio = dict(by_audio_sha256 or {})
        self.queue = list(queue or [])

    def available(self):
        return True, None

    def transcribe(self, pcm, rate):
        key = hashlib.sha256(pcm).hexdigest()
        if key in self.by_audio:
            return AsrResult(self.by_audio[key], self.name, self.model, False)
        if self.queue:
            return AsrResult(self.queue.pop(0), self.name, self.model, False)
        raise AsrUnavailable("SCRIPTED_ASR_HAS_NO_TRANSCRIPT")


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()


class OpenAITranscriptionAsr:
    """Live backend. The key is read from the environment and only ever sent as a bearer header."""

    name = "openai_audio_transcription"
    live = True

    def __init__(self, environ=None, client=None, model=OPENAI_TRANSCRIBE_MODEL):
        self.environ = os.environ if environ is None else environ
        self.client = client
        self.model = model

    def available(self):
        if self.environ.get(LIVE_FLAG) != "openai":
            return False, "LIVE_ASR_NOT_ENABLED"
        if not self.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY_MISSING"
        return True, None

    def transcribe(self, pcm, rate):
        ok, reason = self.available()
        if not ok:
            raise AsrUnavailable(reason)
        import httpx

        try:
            client = self.client or httpx.Client(timeout=30)
            response = client.post(
                OPENAI_TRANSCRIBE_URL,
                headers={"Authorization": "Bearer " + self.environ["OPENAI_API_KEY"]},
                files={"file": ("audio.wav", wav_bytes(pcm, rate), "audio/wav")},
                data={"model": self.model, "response_format": "json", "temperature": "0"},
            )
        except httpx.HTTPError:
            raise AsrUnavailable("ASR_TRANSPORT_ERROR") from None
        if response.status_code != 200:
            raise AsrUnavailable(f"ASR_HTTP_{response.status_code}")
        try:
            body = response.json()
            text = str(body.get("text", ""))
        except ValueError:
            raise AsrUnavailable("ASR_MALFORMED_RESPONSE") from None
        usage = body.get("usage")
        return AsrResult(text, self.name, self.model, True, usage if isinstance(usage, dict) else None)


def select_backend(environ=None, client=None) -> AsrBackend | None:
    """Return a live backend only when explicitly enabled; None means strict speech is unavailable."""
    environ = os.environ if environ is None else environ
    if environ.get(LIVE_FLAG) == "openai":
        backend = OpenAITranscriptionAsr(environ, client)
        return backend if backend.available()[0] else None
    return None


def strict_speech_status(backend: AsrBackend | None) -> dict:
    """Strict speech approval is available only with a configured backend that can run."""
    if backend is None:
        return {"state": "unavailable", "reason": "NO_ASR_BACKEND_CONFIGURED", "live": False}
    ok, reason = backend.available()
    if not ok:
        return {"state": "unavailable", "reason": reason, "live": backend.live}
    if not backend.live:
        return {"state": "offline_fixture_only", "reason": "NOT_LIVE_ASSESSMENT", "live": False}
    return {"state": "available_live", "reason": None, "live": True}
