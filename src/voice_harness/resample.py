"""Deterministic PCM s16le mono resampler (stdlib only).

Linear interpolation in integer arithmetic (round-half-up), so output is
bit-identical across platforms. Harness convention: 24 kHz, 20 ms chunks.
"""

from __future__ import annotations

import array
import sys

HARNESS_RATE = 24000
CHUNK_MS = 20


def _to_samples(pcm: bytes) -> array.array:
    if len(pcm) % 2:
        raise ValueError("PCM_ODD_LENGTH")
    a = array.array("h")
    a.frombytes(pcm)
    if sys.byteorder == "big":
        a.byteswap()
    return a


def _to_bytes(samples: list[int]) -> bytes:
    a = array.array("h", samples)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("INVALID_RATE")
    src = _to_samples(pcm)
    if src_rate == dst_rate or not src:
        return bytes(pcm)
    n_in = len(src)
    n_out = (n_in * dst_rate) // src_rate
    out: list[int] = []
    last = n_in - 1
    for j in range(n_out):
        num = j * src_rate  # position = num / dst_rate
        i, rem = divmod(num, dst_rate)
        a = src[min(i, last)]
        b = src[min(i + 1, last)]
        # round half up on exact integer arithmetic
        v = (a * (dst_rate - rem) + b * rem + dst_rate // 2) // dst_rate
        out.append(max(-32768, min(32767, v)))
    return _to_bytes(out)


def chunk(pcm: bytes, rate: int = HARNESS_RATE, ms: int = CHUNK_MS) -> list[bytes]:
    """Split into whole ``ms`` chunks; a short final chunk is kept (not padded)."""
    size = rate * ms // 1000 * 2
    return [pcm[i : i + size] for i in range(0, len(pcm), size)]
