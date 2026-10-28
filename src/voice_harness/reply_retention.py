"""Bounded retention of provider reply PCM so a disputed verdict can be audited by ear (BAKEOFF_SAFEGUARDS.md).

A fixed- or AI-caller live run otherwise keeps only reply hashes. Retained here, per run and up to a byte cap:

* ``assessment_unsafe`` / ``assessment_untestable``: the independent assessment could not approve the reply
  (ambiguous amounts, ASR disagreement, empty transcript, mismatched claim);
* ``claim_ambiguous``: the ASR transcript's claim extraction flagged ambiguity (FACT-01 v2 treats it as untestable);
* ``transcript_amount_mismatch``: the provider's own transcript and the independent ASR hear different amounts;
* ``spot_check_sample``: a deterministic sample of passing replies (rate configurable, default 5%).

Objects are hash-addressed through ``Evidence.put`` (so bundles, manifests, ``verify_bundle``, holds and the TTL
purge apply unchanged) and each decision, including a skip at the cap, is an ``assessment.reply_audio`` event.
The caller is synthetic and the fixtures are synthetic, so the audio is classed as synthetic (7-day TTL).
"""

from __future__ import annotations

import base64
import hashlib
import os

from .semantic_checks import extract_claims

KIND = "provider_reply_audio"
CLASSIFICATION = "synthetic_session_provider_reply"
TTL_SECONDS = 7 * 86400
RUN_CAP_BYTES = 12 * 1024 * 1024  # about 4 minutes of 24 kHz PCM16 per run
SAMPLE_RATE = 0.05
REASONS = ("assessment_unsafe", "assessment_untestable", "claim_ambiguous", "transcript_amount_mismatch",
           "spot_check_sample")


def dispute_reason(check, provider_transcript):
    """Why a reply is disputed (None when it is not): the first matching reason in ``REASONS`` order."""
    if check["disposition"] in {"unsafe", "untestable"}:
        return "assessment_" + check["disposition"]
    claims = check.get("claims") or {}
    if claims.get("ambiguities"):
        return "claim_ambiguous"
    heard = extract_claims(provider_transcript or "")
    if heard["ambiguities"] or heard["amounts_minor"] != sorted(claims.get("amounts_minor") or []):
        return "transcript_amount_mismatch"
    return None


def sampled(run_id, audio_sha256, rate):
    """Deterministic spot-check draw: the same reply in the same run is always (or never) sampled."""
    draw = int(hashlib.sha256(f"{run_id}:{audio_sha256}".encode()).hexdigest()[:8], 16) / 2**32
    return draw < rate


class ReplyRetention:
    def __init__(self, evidence, *, cap_bytes=RUN_CAP_BYTES, sample_rate=SAMPLE_RATE, ttl=TTL_SECONDS):
        self.evidence, self.cap_bytes, self.sample_rate, self.ttl = evidence, cap_bytes, sample_rate, ttl

    @classmethod
    def from_env(cls, evidence, environ=None):
        """``VOICE_HARNESS_REPLY_RETENTION=off`` disables; ``..._BYTES`` and ``..._SAMPLE_RATE`` tune the caps."""
        env = os.environ if environ is None else environ
        if evidence is None or env.get("VOICE_HARNESS_REPLY_RETENTION", "on").lower() in {"off", "0", "false"}:
            return None
        return cls(evidence, cap_bytes=int(env.get("VOICE_HARNESS_REPLY_RETENTION_BYTES", RUN_CAP_BYTES)),
                   sample_rate=min(1.0, max(0.0, float(env.get("VOICE_HARNESS_REPLY_SAMPLE_RATE", SAMPLE_RATE)))))

    def consider(self, run_id, unit, check, replacement_unit_id=None):
        """Retain ``unit``'s held PCM if disputed or sampled; returns the recorded content (None if not considered)."""
        reason = dispute_reason(check, unit.get("transcript"))
        if reason is None and sampled(run_id, unit["audio_sha256"], self.sample_rate):
            reason = "spot_check_sample"
        if reason is None:
            return None
        pcm = base64.b64decode(unit["pcm"])
        database = self.evidence.database
        artifacts = database.get(run_id)["artifacts"]
        used = sum(a["size"] for a in artifacts.values() if a.get("kind") == KIND)
        sha = hashlib.sha256(pcm).hexdigest()
        content = {"unit_id": unit["unit_id"], "replacement_unit_id": replacement_unit_id,
                   "audio_sha256": unit["audio_sha256"], "artifact_sha256": None, "retention_reason": reason,
                   "disposition": check["disposition"], "size_bytes": len(pcm), "sample_rate": unit["rate"],
                   "data_classification": CLASSIFICATION, "ttl_seconds": self.ttl,
                   "run_cap_bytes": self.cap_bytes, "retained": False, "omission_reason": None}
        if sha not in artifacts and used + len(pcm) > self.cap_bytes:
            content["omission_reason"] = "RUN_RETENTION_CAP_REACHED"
        else:
            content.update(artifact_sha256=self.evidence.put(run_id, pcm, KIND, ttl=self.ttl), retained=True)
        with database.transaction(run_id) as tx:
            tx.event("orchestrator", "assessment.reply_audio", content)
        return content


def summary(events):
    """Manifest/report view of the retention decisions recorded in a ledger."""
    rows = [e["content"] for e in events if e.get("type") == "assessment.reply_audio" and "tombstone" not in e]
    reasons = {}
    for r in rows:
        if r["retained"]:
            reasons[r["retention_reason"]] = reasons.get(r["retention_reason"], 0) + 1
    return {"retained_units": sum(r["retained"] for r in rows), "retained_by_reason": reasons,
            "skipped_at_cap": sum(r["omission_reason"] == "RUN_RETENTION_CAP_REACHED" for r in rows),
            "data_classification": CLASSIFICATION, "ttl_seconds": TTL_SECONDS}


def for_units(events, unit_ids=None):
    """Retained reply-audio references (artifact id, unit, reason), optionally limited to ``unit_ids``."""
    return [{"artifact_sha256": c["artifact_sha256"], "unit_id": c["unit_id"], "retention_reason": c["retention_reason"],
             "disposition": c["disposition"], "event_id": e["event_id"]}
            for e in events if e.get("type") == "assessment.reply_audio" and "tombstone" not in e
            for c in [e["content"]] if c["retained"] and (unit_ids is None or c["unit_id"] in unit_ids)]
