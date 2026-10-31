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
DEFAULT_TTS_VOICE = "ash"  # male; British accent comes from the TTS instructions
DEFAULT_TTS_INSTRUCTIONS = "Speak as a British male caller, in a natural British English accent. Read only the supplied words."
RATE = 24000
PROFILE_ENV = "VOICE_HARNESS_CALLER_PROFILE"
# Caller-voice panel (config only; the OpenAI speech path). "default" is the original voice and sends exactly the original request.
# A named profile fixes the TTS voice and accent instruction; it is selected per run (CallerConfig.voice_profile) and needs openai_tts.
PANEL = {
    "default": None,
    "scottish_male": ("onyx", "Speak as a Scottish male caller, in a natural Scottish English accent. Read only the supplied words."),
    "irish_female": ("coral", "Speak as an Irish female caller, in a natural Irish English accent. Read only the supplied words."),
    "indian_english_female": ("shimmer", "Speak as an Indian female caller, in a natural Indian English accent. Read only the supplied words."),
    "northern_english_male": ("verse", "Speak as a Northern English male caller, in a natural Yorkshire accent. Read only the supplied words."),
}


def run_environ(environ, caller=None):
    """``environ`` adjusted for one run's CallerConfig: a non-default ``voice_profile`` selects it and forces the openai_tts
    backend (the panel is TTS-only); an explicit ``fixed_backend`` is applied as before. Unchanged for a default config."""
    environ = dict(os.environ if environ is None else environ)
    caller = caller or {}
    if caller.get("fixed_backend", "system") != "system":
        environ[ENV] = caller["fixed_backend"]
    if caller.get("voice_profile", "default") != "default":
        environ[PROFILE_ENV], environ[ENV] = caller["voice_profile"], "openai_tts"
    return environ


def profile(environ=None):
    """(name, voice, instructions) of the selected panel voice, or None for the original voice."""
    environ = os.environ if environ is None else environ
    name = (environ.get(PROFILE_ENV) or "default").strip().lower()
    return None if PANEL.get(name) is None else (name, *PANEL[name])


class CallerVoiceUnavailable(RuntimeError):
    pass


def _has_macos():
    return bool(shutil.which("say") and shutil.which("afconvert"))


def backend(environ=None):
    """(backend, reason): backend is None with a reason code when no synthesiser is usable."""
    environ = os.environ if environ is None else environ
    choice = (environ.get(ENV) or "").strip().lower()
    if (environ.get(PROFILE_ENV) or "default").strip().lower() not in PANEL:
        return None, "CALLER_VOICE_UNKNOWN_PROFILE"
    if profile(environ) and choice != "openai_tts":
        return None, "CALLER_VOICE_PROFILE_NEEDS_OPENAI_TTS"
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
        chosen = profile(environ)
        return {"backend": name, "model_id": environ.get("VOICE_HARNESS_CALLER_TTS_MODEL") or DEFAULT_TTS_MODEL,
                "voice": chosen[1] if chosen else environ.get("VOICE_HARNESS_CALLER_TTS_VOICE") or DEFAULT_TTS_VOICE,
                "sample_rate": RATE, **({"profile": chosen[0]} if chosen else {})}
    if name == "macos":
        return {"backend": name, "model_id": "macos-say", "voice": "Daniel", "sample_rate": RATE}
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
        chosen = profile(forced)
        body["instructions"] = (chosen[2] if chosen else environ.get("VOICE_HARNESS_CALLER_TTS_INSTRUCTIONS")
                                or DEFAULT_TTS_INSTRUCTIONS)
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
