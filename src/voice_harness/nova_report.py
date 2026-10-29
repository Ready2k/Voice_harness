"""Build a CapabilityReport from scripts/probe/nova_probe.mjs observations (Phase 0)."""

import json
from datetime import UTC, datetime

from .capability_report import (
    DEFAULT_EXPIRY,
    REQUIRED_ROWS,
    CapabilityReport,
    CapabilityRow,
    Evidence,
)

DOC = "https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-conversational-speech.html"
CARD = "https://docs.aws.amazon.com/ai/responsible-ai/nova-2-5-sonic/overview.md"

# row -> (status when probe evidence exists, doc url, note). "verified" needs docs AND probe.
_RULES = {
    "input_codecs_rates": ("observed", None, "16 kHz PCM16 mono accepted; other rates untested"),
    "output_codecs_rates": ("observed", None, "24 kHz PCM16 requested and bytes received; rate not measured"),
    "audio_in": ("verified", DOC, "silence accepted; synthetic speech (macOS say) recognised when --speech-wav is used"),
    "audio_out": ("verified", DOC, "first audio about 0.9-1.4 s after stream open"),
    "tool_arguments_and_results": ("verified", DOC, "native toolUse/toolResult; bridged to MCP would be gateway_bridge"),
    "transcript_finality": ("observed", None, "SPECULATIVE and FINAL generationStage seen; not alignment"),
    "turn_signalling": ("observed", None, "contentEnd stopReason (PARTIAL_TURN, END_TURN, TOOL_USE)"),
    "usage_granularity": ("verified", DOC, "usageEvent with speech/text token deltas and totals"),
    "reconnect_resume": ("unknown", None, "fresh session works; resume token not tested"),
    "cancel_truncate": ("observed", None, "barge-in by synthetic speech: INTERRUPTED stopReason and in-band interrupted marker ~0.5 s after speech began; client must flush its own buffered audio; no truncate-at-offset primitive"),
    "audio_alignment": ("unknown", None, "no word/audio timestamps seen"),
    "direct_mcp": ("unknown", None, "docs describe native function calling only"),
}
_ROW_EVIDENCE = {"input_codecs_rates": "audio_in", "output_codecs_rates": "audio_out"}


def build_report(
    obs: dict, report_id: str, log_file: str, attestation: str | None = None
) -> CapabilityReport:
    now = datetime.now(UTC)
    ev = obs["evidence"]
    notes = obs.get("notes", {})
    rows = []
    for name in REQUIRED_ROWS:
        status, doc, rule_note = _RULES[name]
        ids = tuple(dict.fromkeys(ev.get(_ROW_EVIDENCE.get(name, name), ())))[:20]
        if status != "unknown" and not ids:
            status = "unknown"  # no probe evidence, claim nothing
        limits = [rule_note, *notes.get(name, [])[:2]]
        rows.append(
            CapabilityRow(
                name=name,
                status=status,
                evidence=Evidence(
                    doc_url=doc,
                    doc_retrieved_at="2026-10-16" if doc else None,
                    probe_event_ids=ids,
                ),
                limitations=tuple(limits),
                tool_mode="gateway_bridge" if name == "tool_arguments_and_results" else None,
            )
        )
    meta = obs["meta"]
    return CapabilityReport(
        report_id=report_id,
        provider_family="aws_nova_sonic",
        api_surface="bedrock-runtime InvokeModelWithBidirectionalStream",
        region=meta["region"],
        requested_model_id=meta["modelId"],
        returned_model_id=None,
        version_unresolved=True,
        checked_at=now.isoformat(),
        expires_at=(now + DEFAULT_EXPIRY).isoformat(),
        adapter_version="nova_probe.mjs/0",
        sdk_version=obs["flags"]["sdk"],
        effective_session_config={
            "raw_event_log": log_file,
            "inference": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.2},
            "endpointingSensitivity": "MEDIUM",
            "input": "audio/lpcm 16000 s16le mono",
            "output": "audio/lpcm 24000 s16le mono, voice amy",
            "service_card": CARD,
        },
        operator_terms_attestation=attestation,
        rows=tuple(rows),
        limitations=(
            "No returned model version in stream; alias treated as version_unresolved",
            "Audio inputs were synthetic silence; text turns drove the conversation",
            "Nova requires an open audio input content for the whole prompt",
            "Terms/retention/quotas/pricing not established by probe; need operator attestation",
            "Bogus toolUseId produces ValidationException, ending the stream",
        ),
    )


def load(path: str, report_id: str, attestation: str | None = None) -> CapabilityReport:
    with open(path) as f:
        obs = json.load(f)
    return build_report(obs, report_id, obs.get("log_file", "see artifacts/phase0/"), attestation)
