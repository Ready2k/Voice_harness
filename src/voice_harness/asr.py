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
import re
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

LIVE_FLAG = "VOICE_HARNESS_LIVE_ASR"
OPENAI_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
# gpt-4o-transcribe and gpt-4o-mini-transcribe shut down on 2027-02-26; gpt-transcribe replaces them.
OPENAI_TRANSCRIBE_MODEL = "gpt-transcribe"
MODEL_ENV = "VOICE_HARNESS_ASR_MODEL"  # optional override of the OpenAI transcription model; unset keeps the default
SECOND_MODEL_ENV = "VOICE_HARNESS_ASR_SECOND_MODEL"  # optional second OpenAI model: cross-check, off unless set
DISAGREEMENT = "ASR_DISAGREEMENT"
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
# Vendor behind each backend name. Event contracts are closed, so the vendor is derived from the recorded
# ``backend`` rather than stored on ``assessment.independent_asr``.
ASR_VENDORS = {"openai_audio_transcription": "openai", "openai_dual_transcription": "openai",
               "scripted_offline": "scripted"}


def asr_vendor(backend: str) -> str:
    return ASR_VENDORS.get(backend, "unknown")


class AsrUnavailable(Exception):
    """Raised with a stable reason code; never carries credentials or response bodies."""


@dataclass(frozen=True)
class AsrResult:
    text: str
    backend: str
    model: str
    live: bool
    usage: dict | None = None  # provider-returned usage, verbatim (None when the provider returned none)
    alternate: str | None = None  # the second transcriber's text when cross-checked, else None
    parts: tuple = ()  # the individual results behind a cross-checked transcript (each carries its own usage)
    dropped_params: tuple = ()  # optional request fields the model rejected and the retry left out
    audio_seconds: float | None = None  # length of the audio sent, measured by the harness (not provider-reported)


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

    def __init__(self, environ=None, client=None, model=None):
        self.environ = os.environ if environ is None else environ
        self.client = client
        self.model = model or self.environ.get(MODEL_ENV) or OPENAI_TRANSCRIBE_MODEL

    def available(self):
        if self.environ.get(LIVE_FLAG) != "openai":
            return False, "LIVE_ASR_NOT_ENABLED"
        if not self.environ.get("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY_MISSING"
        if not _MODEL_NAME.fullmatch(self.model):
            return False, "ASR_MODEL_INVALID"
        return True, None

    def transcribe(self, pcm, rate):
        ok, reason = self.available()
        if not ok:
            raise AsrUnavailable(reason)
        import httpx

        data = {"model": self.model, "response_format": "json", "temperature": "0"}
        dropped = []
        try:
            client = self.client or httpx.Client(timeout=30)
            while True:
                response = client.post(
                    OPENAI_TRANSCRIBE_URL,
                    headers={"Authorization": "Bearer " + self.environ["OPENAI_API_KEY"]},
                    files={"file": ("audio.wav", wav_bytes(pcm, rate), "audio/wav")},
                    data=data,
                )
                # A model may reject an optional field (the docs for gpt-transcribe do not list temperature or
                # response_format): retry without the field the error names, so a rejected option is not an outage.
                named = [k for k in ("temperature", "response_format") if k in data and k in response.text.lower()] \
                    if response.status_code == 400 else []
                if not named:
                    break
                for key in named:
                    data.pop(key)
                    dropped.append(key)
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
        return AsrResult(text, self.name, self.model, True, usage if isinstance(usage, dict) else None,
                         dropped_params=tuple(dropped), audio_seconds=len(pcm) / (2 * rate) if rate else None)


class DualAsr:
    """Two OpenAI transcription models on the same audio; the first supplies the transcript.

    The second is a cross-check only: ``agree`` decides whether the two readings of amounts and dates match, and
    the assessment treats a mismatch as untestable. It is the same vendor, so errors can correlate; it catches
    model-specific mishearings, not systematic ones. Opt-in via ``VOICE_HARNESS_ASR_SECOND_MODEL``."""

    name = "openai_dual_transcription"
    live = True

    def __init__(self, primary: OpenAITranscriptionAsr, secondary: OpenAITranscriptionAsr):
        self.primary, self.secondary = primary, secondary
        self.model = f"{primary.model}+{secondary.model}"

    def available(self):
        if self.primary.model == self.secondary.model:
            return False, "ASR_SECOND_MODEL_SAME_AS_FIRST"
        for backend in (self.primary, self.secondary):
            ok, reason = backend.available()
            if not ok:
                return False, reason
        return True, None

    def transcribe(self, pcm, rate):
        ok, reason = self.available()
        if not ok:
            raise AsrUnavailable(reason)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(self.primary.transcribe, pcm, rate), pool.submit(self.secondary.transcribe, pcm, rate)
            a, b = first.result(), second.result()  # either failure propagates: no unverified cross-check
        return AsrResult(a.text, self.name, self.model, True, None, alternate=b.text, parts=(a, b))


def claims_key(text: str) -> tuple:
    """The amounts, dates and ambiguity codes a transcript yields, in a comparable form."""
    from .semantic_checks import extract_claims

    found = extract_claims(text)
    dates = sorted((d["day"], d["month"], d["year"] or 0) for d in found["dates"])
    return tuple(found["amounts_minor"]), tuple(dates), tuple(found["ambiguities"])


def agree(first: str, second: str) -> bool:
    """True when two transcripts of the same audio carry the same spoken amounts, dates and ambiguities."""
    return claims_key(first) == claims_key(second)


def select_backend(environ=None, client=None) -> AsrBackend | None:
    """Return a live backend only when explicitly enabled; None means strict speech is unavailable."""
    environ = os.environ if environ is None else environ
    if environ.get(LIVE_FLAG) == "openai":
        backend = OpenAITranscriptionAsr(environ, client)
        if environ.get(SECOND_MODEL_ENV):
            backend = DualAsr(backend, OpenAITranscriptionAsr(environ, client, model=environ[SECOND_MODEL_ENV]))
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
