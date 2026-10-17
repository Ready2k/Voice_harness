"""InterruptionReceipt classification (spec section 5, EN-06).

Three things are reported separately: generation cancellation, local playback stop
and provider history truncation. If the provider's conversation history cannot be
reconciled with what was actually played, the session is labelled
``history_diverged`` and strict conversational comparisons must exclude later
turns or restart in a new session.
"""

from __future__ import annotations

# Truncation outcomes an adapter may report; only "confirmed" reconciles history.
TRUNCATION_STATES = frozenset({"confirmed", "scripted", "unsupported", "unknown"})


def receipt(offsets, provider_receipt=None):
    provider_receipt = provider_receipt or {}
    truncation = provider_receipt.get("history_truncation", "unknown")
    if truncation not in TRUNCATION_STATES:
        truncation = "unknown"
    # Audio the provider generated but the user did not fully hear: never played
    # (pending/approved) or cut short (residual samples). If the provider still
    # remembers it as spoken, its history no longer matches the call.
    unheard = any(
        item["prior_state"] in {"pending", "approved"} or item["residual_samples"] > 0
        for item in offsets
    )
    diverged = unheard and truncation != "confirmed"
    return {
        "generation_cancelled": provider_receipt.get("generation_cancelled", "requested"),
        "local_playback_stopped": True,
        "history_truncation": truncation,
        "history_state": "history_diverged" if diverged else "reconciled",
        "strict_comparison": "exclude_subsequent_turns" if diverged else "unaffected",
    }
