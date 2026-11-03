"""Pluggable caller-speech synthesiser for live runs.

Selected by ``VOICE_HARNESS_CALLER_VOICE``: ``macos`` (say/afconvert; the default when present) or ``openai_tts``
(opt-in, OpenAI speech endpoint over httpx, raw 24 kHz s16le mono) or ``elevenlabs_tts`` (opt-in, ElevenLabs library voices, same PCM format). Anything unconfigured fails closed with a
reason code. ``describe`` returns identifiers only (backend, voice, model) - never credentials."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

ENV = "VOICE_HARNESS_CALLER_VOICE"
LIVE_FLAG = "VOICE_HARNESS_LIVE_ASR"  # same switch as the ASR backend
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_TTS_VOICE = "ash"  # male; British accent comes from the TTS instructions
DEFAULT_TTS_INSTRUCTIONS = "Speak as a British male caller, in a natural British English accent. Read only the supplied words."
RATE = 24000
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_KEY_ENV = "ELEVENLABS_API_KEY"
ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_CACHE_ENV = "VOICE_HARNESS_CALLER_TTS_CACHE"  # directory; unset = no cache. Same voice, model and text reuse one take.
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
# ElevenLabs library voices (the "el_" profiles): (voice_id, library listing, library accent tag). The accent is the library's own
# label, not an independently verified accent. These profiles need elevenlabs_tts and never change the OpenAI panel above.
ELEVENLABS_PANEL = {
    "el_scottish_male": ("OnQdvQKEr4UNRtg1mSrF", "Graeme - Professional Scottish Male", "scottish"),
    "el_irish_female": ("cMnMwhoOlFYOQDBWBwU1", "Jordan - Young Irish Woman", "irish"),
    "el_yorkshire_male": ("jIIwKmgS6dN9eWEJEvMJ", "Ashley - Bright, Yorkshire, British Male", "yorkshire"),
    "el_indian_english_female": ("EQajdcMa8fccc3KCqwGO", "Vanishree - Best Casual Indian English", "indian"),
}


def run_environ(environ, caller=None):
    """``environ`` adjusted for one run's CallerConfig: a non-default ``voice_profile`` selects it and forces the openai_tts
    backend (the panel is TTS-only); an explicit ``fixed_backend`` is applied as before. Unchanged for a default config."""
    environ = dict(os.environ if environ is None else environ)
    caller = caller or {}
    if caller.get("fixed_backend", "system") != "system":
        environ[ENV] = caller["fixed_backend"]
    if caller.get("voice_profile", "default") != "default":
        environ[PROFILE_ENV] = caller["voice_profile"]
        environ[ENV] = "elevenlabs_tts" if caller["voice_profile"] in ELEVENLABS_PANEL else "openai_tts"
    return environ


def profile(environ=None):
    """(name, voice, instructions) of the selected panel voice, or None for the original voice."""
    environ = os.environ if environ is None else environ
    name = (environ.get(PROFILE_ENV) or "default").strip().lower()
    return None if PANEL.get(name) is None else (name, *PANEL[name])


def elevenlabs_profile(environ=None):
    """(name, voice_id, listing, library_accent) of the selected ElevenLabs panel voice, or None."""
    environ = os.environ if environ is None else environ
    name = (environ.get(PROFILE_ENV) or "default").strip().lower()
    return (name, *ELEVENLABS_PANEL[name]) if name in ELEVENLABS_PANEL else None


class CallerVoiceUnavailable(RuntimeError):
    pass


def _has_macos():
    return bool(shutil.which("say") and shutil.which("afconvert"))


def backend(environ=None):
    """(backend, reason): backend is None with a reason code when no synthesiser is usable."""
    environ = os.environ if environ is None else environ
    choice = (environ.get(ENV) or "").strip().lower()
    if (environ.get(PROFILE_ENV) or "default").strip().lower() not in {**PANEL, **ELEVENLABS_PANEL}:
        return None, "CALLER_VOICE_UNKNOWN_PROFILE"
    if profile(environ) and choice != "openai_tts":
        return None, "CALLER_VOICE_PROFILE_NEEDS_OPENAI_TTS"
    if elevenlabs_profile(environ) and choice != "elevenlabs_tts":
        return None, "CALLER_VOICE_PROFILE_NEEDS_ELEVENLABS_TTS"
    if choice in ("", "macos"):
        if _has_macos():
            return "macos", None
        return None, "NO_CALLER_VOICE_SYNTH"
    if choice == "openai_tts":
        if environ.get(LIVE_FLAG) != "openai" or not environ.get("OPENAI_API_KEY"):
            return None, "CALLER_VOICE_TTS_NOT_CONFIGURED"
        return "openai_tts", None
    if choice == "elevenlabs_tts":
        if not environ.get(ELEVENLABS_KEY_ENV):
            return None, "CALLER_VOICE_ELEVENLABS_NOT_CONFIGURED"
        if not elevenlabs_profile(environ):
            return None, "CALLER_VOICE_ELEVENLABS_NEEDS_PROFILE"
        return "elevenlabs_tts", None
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
    if name == "elevenlabs_tts":
        chosen = elevenlabs_profile(environ)
        return {"backend": name, "model_id": ELEVENLABS_MODEL, "voice": chosen[1], "sample_rate": RATE, "profile": chosen[0],
                "library_accent": chosen[3]}
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


def elevenlabs_tts_pcm(text, environ=None, client=None):
    """Raw 24 kHz s16le mono PCM (``pcm_24000``) for ``text`` in the selected ElevenLabs panel voice. The text is sent
    exactly as the scenario script has it."""
    environ = os.environ if environ is None else environ
    forced = {**environ, ENV: "elevenlabs_tts"}
    ok, reason = available(forced)
    if not ok:
        raise CallerVoiceUnavailable(reason)
    import httpx

    spec = describe(forced)
    # A cached take means every provider hears the identical waveform and the line is paid for once.
    cache = environ.get(ELEVENLABS_CACHE_ENV)
    path = Path(cache) / (hashlib.sha256("\0".join((spec["voice"], spec["model_id"], text)).encode()).hexdigest() + ".pcm") if cache else None
    if path is not None and path.is_file() and path.stat().st_size >= 2:
        return path.read_bytes()
    try:
        client = client or httpx.Client(timeout=60)
        response = client.post(
            ELEVENLABS_TTS_URL.format(voice_id=spec["voice"]), params={"output_format": "pcm_24000"},
            headers={"xi-api-key": environ[ELEVENLABS_KEY_ENV]}, json={"text": text, "model_id": spec["model_id"]})
    except httpx.HTTPError:
        raise CallerVoiceUnavailable("CALLER_VOICE_TTS_TRANSPORT_ERROR") from None
    if response.status_code != 200:
        raise CallerVoiceUnavailable(f"CALLER_VOICE_TTS_HTTP_{response.status_code}")
    pcm = bytes(response.content)
    if len(pcm) < 2:
        raise CallerVoiceUnavailable("CALLER_VOICE_TTS_EMPTY")
    pcm = pcm[: len(pcm) // 2 * 2]
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".tmp").write_bytes(pcm)
        path.with_suffix(".tmp").replace(path)
    return pcm
