"""Build a CapabilityReport from scripts/probe/xai_probe.py observations (Phase 0)."""

import json
from datetime import UTC, datetime

from .capability_report import (
    DEFAULT_EXPIRY,
    REQUIRED_ROWS,
    CapabilityReport,
    CapabilityRow,
    Evidence,
)

DOC = "https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech"
MODELS_DOC = "https://docs.x.ai/developers/models"
DOC_RETRIEVED = "2026-10-16"  # docs/phase0/DOC_EVIDENCE.md

# row -> (status when probe evidence exists, doc url, note). "verified" needs docs AND probe.
_RULES = {
    "input_codecs_rates": ("observed", None, "audio/pcm 24 kHz echoed in session.updated; other documented codecs and rates untested"),
    "output_codecs_rates": ("observed", None, "audio/pcm 24 kHz echoed in session.updated; audio_duration_ms is reported per delta"),
    "audio_in": ("verified", DOC, "synthetic speech streamed under server_vad was transcribed; a silence-only manual commit produced no committed event"),
    "audio_out": ("verified", DOC, "response.output_audio.delta received (PCM 24 kHz)"),
    "tool_arguments_and_results": ("verified", DOC, "function call then function_call_output then response.create; native function tools, server-side MCP not exercised"),
    "transcript_finality": ("verified", DOC, "output transcript delta/done; input transcript is cumulative (.updated) with repeated .completed events per item"),
    "turn_signalling": ("verified", DOC, "response.done status; manual turns plus server_vad speech_started/speech_stopped"),
    "usage_granularity": ("verified", DOC, "response.done carries a top-level usage object (token details, output_audio_seconds, billable_audio_seconds); response.usage inside it is empty"),
    "cancel_truncate": ("verified", DOC, "response.cancel gives response.done status=cancelled and conversation.item.truncate is acknowledged; server_vad speech ends the in-flight response but reports status=completed with empty output (no distinct cancelled status)"),
    "reconnect_resume": ("unknown", None, "fresh connection works; documented history replay not exercised"),
    "audio_alignment": ("unknown", None, "audio_duration_ms and transcript start_time seen; no verified word/audio alignment"),
    "direct_mcp": ("unknown", None, "session accepts an mcp tool and the server attempts MCP discovery itself, but no reachable MCP server was available to exercise it"),
}


def build_report(obs: dict, report_id: str, attestation: str | None = None) -> CapabilityReport:
    now = datetime.now(UTC)
    ev, notes = obs["evidence"], obs.get("notes", {})
    rows = []
    for name in REQUIRED_ROWS:
        status, doc, rule_note = _RULES[name]
        ids = tuple(dict.fromkeys(ev.get(name, ())))[:20]
        if status != "unknown" and not ids:
            status = "unknown"  # fail closed: a row without probe evidence is never claimed
        rows.append(
            CapabilityRow(
                name=name,
                status=status,
                evidence=Evidence(
                    doc_url=doc, doc_retrieved_at=DOC_RETRIEVED if doc else None, probe_event_ids=ids
                ),
                limitations=(rule_note, *notes.get(name, [])[:6]),
                tool_mode="gateway_bridge" if name == "tool_arguments_and_results" else None,
            )
        )
    meta = obs["meta"]
    created = meta.get("sessionCreated") or {}
    returned = created.get("model")
    alias = meta.get("aliasProbe") or {}
    return CapabilityReport(
        report_id=report_id,
        provider_family="xai_grok_voice",
        api_surface="realtime websocket wss://api.x.ai/v1/realtime",
        region=None,
        requested_model_id=meta["modelId"],
        returned_model_id=returned,
        version_unresolved=returned is None,
        checked_at=now.isoformat(),
        expires_at=(now + DEFAULT_EXPIRY).isoformat(),
        adapter_version="xai_probe.py/0",
        sdk_version=obs["flags"]["sdk"],
        effective_session_config={
            "raw_event_log": obs.get("log_file"),
            "audio": "audio/pcm 24000 in and out",
            "voice": created.get("voice"),
            "turn_detection": "null (manual) and server_vad were both exercised",
            "input_transcription": "grok-transcribe",
            "model_doc": MODELS_DOC,
        },
        operator_terms_attestation=attestation,
        rows=tuple(rows),
        limitations=(
            f"Pinned {meta['modelId']} returned {returned}; alias {alias.get('requested')} resolved to {alias.get('returned')} "
            "on this date (observed once, not guaranteed) and is never used for runs",
            "The server generates audio faster than real time (roughly 7x in one uninterrupted sample); server_vad speech ends a response early",
            "Barge-in was exercised with one synthetic (macOS say) phrase only",
            "Terms, retention, quotas and pricing need operator attestation; the existing operator attestation does not cover xAI",
        ),
    )


def load(path: str, report_id: str, attestation: str | None = None) -> CapabilityReport:
    with open(path) as f:
        return build_report(json.load(f), report_id, attestation)
