"""Observation of a spoken confirmation for the out-of-band cancellation scenario.

The confirmation is authorised only when independent ASR of the caller's confirming audio actually contains an
affirmation.  An unavailable backend, blank transcript, negation, mixed or unclear speech all leave the
cancellation unauthorised (``ambiguous`` or ``deny``), never an affirmation by default.
"""

from __future__ import annotations

import hashlib
import re

from .asr import AsrUnavailable

# Consent is recognised only from an explicit affirmative that opens the reply. A word such as "confirm" elsewhere
# in the sentence is not consent ("I cannot confirm", "the word confirm"), and anything negated, hedged,
# conditional or interrogative is refused (deny or ambiguous), never inferred as acceptance.
_AFFIRM = re.compile(r"^\W*(yes|yeah|yep|yup|please do|go ahead|do it|that's right|correct|i confirm|confirmed)\b")
_NEGATE = re.compile(
    r"\b(no|not|never|nope|nothing|cannot|stop|wait|hold on|actually|cancel that|unsure|maybe|perhaps|unless|if|"
    r"but|yet|later|first|\w+n't|dont|cant|wont|do not)\b")


def _normalise(text):
    return re.sub(r"\s+", " ", (text or "").lower().replace("\u2019", "'").replace("\u2018", "'").strip())


def classify_reply(text):
    lowered = _normalise(text)
    if not lowered:
        return "ambiguous", ["EMPTY_TRANSCRIPT"]
    affirm = bool(_AFFIRM.search(lowered))
    negate = bool(_NEGATE.search(lowered))
    if "?" in lowered:
        return "ambiguous", ["QUESTION_NOT_CONSENT"]
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
