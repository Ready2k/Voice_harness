"""PCM s16le mono resampler backed by libsoxr (native, via numpy arrays).

Spec section 18 forbids Python per-sample loops for resampling. libsoxr's
high-quality mode is deterministic for identical input and build. Output
length is fixed to ``len(in) * dst // src`` samples. Harness convention:
24 kHz, 20 ms chunks.
"""

from __future__ import annotations

import numpy as np
import soxr

HARNESS_RATE = 24000
CHUNK_MS = 20


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("INVALID_RATE")
    if len(pcm) % 2:
        raise ValueError("PCM_ODD_LENGTH")
    if src_rate == dst_rate or not pcm:
        return bytes(pcm)
    src = np.frombuffer(pcm, dtype="<i2")
    out = soxr.resample(src, src_rate, dst_rate, quality="HQ")
    n_out = len(src) * dst_rate // src_rate
    if len(out) < n_out:
        out = np.pad(out, (0, n_out - len(out)), mode="edge")
    return out[:n_out].astype("<i2").tobytes()


class StreamResampler:
    """Stateful resampler for continuous audio: no seams between chunks."""

    def __init__(self, src_rate: int, dst_rate: int):
        if src_rate <= 0 or dst_rate <= 0:
            raise ValueError("INVALID_RATE")
        self.src_rate, self.dst_rate = src_rate, dst_rate
        self._stream = None if src_rate == dst_rate else soxr.ResampleStream(
            src_rate, dst_rate, 1, dtype="int16", quality="HQ")

    def process(self, pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError("PCM_ODD_LENGTH")
        if self._stream is None:
            return bytes(pcm)
        out = self._stream.resample_chunk(np.frombuffer(pcm, dtype="<i2"))
        return out.astype("<i2").tobytes()


def chunk(pcm: bytes, rate: int = HARNESS_RATE, ms: int = CHUNK_MS) -> list[bytes]:
    """Split into whole ``ms`` chunks; a short final chunk is kept (not padded)."""
    size = rate * ms // 1000 * 2
    return [pcm[i : i + size] for i in range(0, len(pcm), size)]
