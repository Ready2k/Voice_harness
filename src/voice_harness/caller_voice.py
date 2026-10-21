"""Pluggable caller-speech synthesiser for live runs.

Selected by ``VOICE_HARNESS_CALLER_VOICE``: ``macos`` (say/afconvert; the default when present) or ``openai_tts``
(opt-in, OpenAI speech endpoint over httpx, raw 24 kHz s16le mono). Anything unconfigured fails closed with a
reason code. ``describe`` returns identifiers only (backend, voice, model) - never credentials."""
from __future__ import annotations

import os
import shutil

ENV = "VOICE_HARNESS_CALLER_VOICE"
LIVE_FLAG = "VOICE_HARNESS_LIVE_ASR"  # same switch as the ASR backend
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_TTS_VOICE = "alloy"
RATE = 24000


class CallerVoiceUnavailable(RuntimeError):
    pass


def _has_macos():
    return bool(shutil.which("say") and shutil.which("afconvert"))


def backend(environ=None):
    """(backend, reason): backend is None with a reason code when no synthesiser is usable."""
    environ = os.environ if environ is None else environ
    choice = (environ.get(ENV) or "").strip().lower()
    if choice in ("", "macos"):
        if _has_macos():
            return "macos", None
        return None, "NO_CALLER_VOICE_SYNTH"
    if choice == "openai_tts":
        if environ.get(LIVE_FLAG) != "openai" or not environ.get("OPENAI_API_KEY"):
            return None, "CALLER_VOICE_TTS_NOT_CONFIGURED"
        return "openai_tts", None
    return None, "CALLER_VOICE_UNKNOWN_BACKEND"


def available(environ=None):
    name, reason = backend(environ)
    return (True, None) if name else (False, reason)


def describe(environ=None):
    """Identifiers of the synthesiser in use, for the run.live_binding provider metadata."""
    environ = os.environ if environ is None else environ
    name, _ = backend(environ)
    if name == "openai_tts":
        return {"backend": name, "model_id": environ.get("VOICE_HARNESS_CALLER_TTS_MODEL") or DEFAULT_TTS_MODEL,
                "voice": environ.get("VOICE_HARNESS_CALLER_TTS_VOICE") or DEFAULT_TTS_VOICE, "sample_rate": RATE}
    if name == "macos":
        return {"backend": name, "model_id": "macos-say", "voice": "system_default", "sample_rate": RATE}
    return {"backend": None, "model_id": None, "voice": None, "sample_rate": RATE}


def openai_tts_pcm(text, environ=None, client=None):
    """Raw 24 kHz s16le mono PCM (the endpoint's ``pcm`` format) for ``text``."""
    environ = os.environ if environ is None else environ
    forced = {**environ, ENV: "openai_tts"}
    ok, reason = available(forced)
    if not ok:
        raise CallerVoiceUnavailable(reason)
    import httpx

    spec = describe(forced)
    try:
        client = client or httpx.Client(timeout=60)
        body = {"model": spec["model_id"], "voice": spec["voice"], "input": text, "response_format": "pcm"}
        if environ.get("VOICE_HARNESS_CALLER_TTS_INSTRUCTIONS"):
            body["instructions"] = environ["VOICE_HARNESS_CALLER_TTS_INSTRUCTIONS"]
        response = client.post(
            OPENAI_TTS_URL, headers={"Authorization": "Bearer " + environ["OPENAI_API_KEY"]},
            json=body)
    except httpx.HTTPError:
        raise CallerVoiceUnavailable("CALLER_VOICE_TTS_TRANSPORT_ERROR") from None
    if response.status_code != 200:
        raise CallerVoiceUnavailable(f"CALLER_VOICE_TTS_HTTP_{response.status_code}")
    pcm = bytes(response.content)
    if len(pcm) < 2:
        raise CallerVoiceUnavailable("CALLER_VOICE_TTS_EMPTY")
    return pcm[: len(pcm) // 2 * 2]
