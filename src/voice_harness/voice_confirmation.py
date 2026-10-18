"""Observation of a spoken confirmation for the out-of-band cancellation scenario.

The confirmation is authorised only when independent ASR of the caller's confirming audio actually contains an
affirmation.  An unavailable backend, blank transcript, negation, mixed or unclear speech all leave the
cancellation unauthorised (``ambiguous`` or ``deny``), never an affirmation by default.
"""

from __future__ import annotations

import hashlib
import re

from .asr import AsrUnavailable

_AFFIRM = re.compile(r"\b(yes|yeah|yep|yup|confirm(?:ed)?|go ahead|do it|that's right)\b")
_NEGATE = re.compile(r"\b(no|not|don't|dont|do not|never|stop|wait|hold on|actually|cancel that|nope)\b")


def classify_reply(text):
    lowered = (text or "").lower()
    if not lowered.strip():
        return "ambiguous", ["EMPTY_TRANSCRIPT"]
    affirm, negate = bool(_AFFIRM.search(lowered)), bool(_NEGATE.search(lowered))
    if affirm and negate:
        return "ambiguous", ["MIXED_AFFIRM_AND_NEGATION"]
    if negate:
        return "deny", ["NEGATION_OBSERVED"]
    if affirm:
        return "affirm", []
    return "ambiguous", ["NO_AFFIRMATION_OBSERVED"]


def observe(backend, pcm, rate):
    """Independent ASR of the confirming turn: ``observed_choice``, reason codes and hashes (never the audio)."""
    base = {"pcm_sha256": hashlib.sha256(pcm or b"").hexdigest(), "asr_backend": None, "transcript_sha256": None}
    if backend is None or not pcm:
        return {**base, "observed_choice": "ambiguous", "reason_codes": ["INDEPENDENT_ASR_UNAVAILABLE"]}
    try:
        ok, reason = backend.available()
        if not ok:
            raise AsrUnavailable(reason)
        result = backend.transcribe(pcm, rate)
    except AsrUnavailable:
        return {**base, "observed_choice": "ambiguous", "reason_codes": ["INDEPENDENT_ASR_UNAVAILABLE"]}
    choice, reasons = classify_reply(result.text)
    return {**base, "asr_backend": result.backend, "observed_choice": choice, "reason_codes": reasons,
            "transcript_sha256": hashlib.sha256((result.text or "").encode()).hexdigest()}
