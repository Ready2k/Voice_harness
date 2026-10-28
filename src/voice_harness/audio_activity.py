"""One "first audible audio" definition for every provider: PCM16 RMS above a fixed threshold per audio chunk.

GPT-Live streams output continuously (silence included), so its first audio is necessarily its first audible chunk;
packet-streaming providers can send near-silent leading packets. Timing every provider at its first audible chunk
removes that asymmetry. ``audible_payload`` reads the same native event shapes as ``live_scenario.audio_bytes``.
"""

from __future__ import annotations

import base64
import binascii

import numpy as np

RMS_THRESHOLD_PCM16 = 100  # about -50 dBFS: above codec/dither noise floors, far below any speech


def rms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2")
    return float(np.sqrt(np.mean(samples.astype(float) ** 2))) if samples.size else 0.0


def audible(pcm: bytes, threshold: float = RMS_THRESHOLD_PCM16) -> bool:
    return rms(pcm) > threshold


def _decode(value):
    try:
        return base64.b64decode(value, validate=True) if isinstance(value, str) else b""
    except (binascii.Error, ValueError):
        return b""


def audio_chunks(payload) -> list[bytes]:
    """Decoded output-audio PCM carried by one native event, by shape (OpenAI/xAI/GPT-Live deltas, ElevenLabs
    ``audio``, Nova ``audioOutput``, Gemini ``serverContent.modelTurn`` inline audio)."""
    if not isinstance(payload, dict):
        return []
    if payload.get("type") in ("response.output_audio.delta", "response.audio.delta", "session.output_audio.delta"):
        return [_decode(payload.get("delta"))]
    if payload.get("type") == "audio":
        return [_decode((payload.get("audio_event") or {}).get("audio_base_64"))]
    if isinstance(payload.get("audioOutput"), dict):
        return [_decode(payload["audioOutput"].get("content"))]
    content = payload.get("serverContent")
    turn = content.get("modelTurn") if isinstance(content, dict) else None
    parts = turn.get("parts") if isinstance(turn, dict) else None
    return [_decode(p["inlineData"].get("data")) for p in parts or () if isinstance(p, dict)
            and isinstance(p.get("inlineData"), dict) and str(p["inlineData"].get("mimeType", "audio")).startswith("audio")]


def audible_payload(payload, threshold: float = RMS_THRESHOLD_PCM16) -> bool | None:
    """True if the event carries audible audio, False if only inaudible audio, None if it carries no audio."""
    chunks = [c for c in audio_chunks(payload) if c]
    return any(audible(c, threshold) for c in chunks) if chunks else None
