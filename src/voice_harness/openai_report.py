"""Build a CapabilityReport from scripts/probe/openai_probe.mjs observations (Phase 0)."""

import json
from datetime import UTC, datetime

from .capability_report import (
    DEFAULT_EXPIRY,
    REQUIRED_ROWS,
    CapabilityReport,
    CapabilityRow,
    Evidence,
)

DOC = "https://developers.openai.com/api/docs/guides/realtime-conversations"
MODEL_DOC = "https://developers.openai.com/api/docs/models/gpt-realtime-2.1"

# row -> (status when probe evidence exists, doc url, note). "verified" needs docs AND probe.
_RULES = {
    "input_codecs_rates": ("observed", None, "audio/pcm 24 kHz accepted in session.updated; other formats untested"),
    "output_codecs_rates": ("observed", None, "audio/pcm 24 kHz accepted in session.updated; rate of bytes not measured"),
    "audio_in": ("verified", DOC, "silence append + commit only; recognition not exercised"),
    "audio_out": ("verified", DOC, "response.output_audio.delta received"),
    "tool_arguments_and_results": ("verified", DOC, "function_call then function_call_output; native, MCP not exercised"),
    "transcript_finality": ("verified", DOC, "output transcript delta/done seen; input transcript not exercised"),
    "turn_signalling": ("verified", DOC, "response.done status; manual turns (turn_detection=null) only, VAD untested"),
    "usage_granularity": ("verified", DOC, "response.done usage with input/output token details"),
    "cancel_truncate": ("observed", None, "response.cancel observed; truncate ack not seen on a cancelled item"),
    "reconnect_resume": ("unknown", None, "fresh connection works; resume not documented or tested"),
    "audio_alignment": ("unknown", None, "no word/audio timestamps seen"),
    "direct_mcp": ("unknown", None, "MCP tool type documented but not exercised"),
}


def build_report(obs: dict, report_id: str) -> CapabilityReport:
    now = datetime.now(UTC)
    ev, notes = obs["evidence"], obs.get("notes", {})
    rows = []
    for name in REQUIRED_ROWS:
        status, doc, rule_note = _RULES[name]
        ids = tuple(dict.fromkeys(ev.get(name, ())))[:20]
        if status != "unknown" and not ids:
            status = "unknown"
        rows.append(
            CapabilityRow(
                name=name,
                status=status,
                evidence=Evidence(
                    doc_url=doc, doc_retrieved_at="2026-10-16" if doc else None, probe_event_ids=ids
                ),
                limitations=(rule_note, *notes.get(name, [])[:2]),
                tool_mode="gateway_bridge" if name == "tool_arguments_and_results" else None,
            )
        )
    meta = obs["meta"]
    returned = (meta.get("sessionCreated") or {}).get("model")
    return CapabilityReport(
        report_id=report_id,
        provider_family="openai",
        api_surface="realtime websocket wss://api.openai.com/v1/realtime",
        region=None,
        requested_model_id=meta["modelId"],
        returned_model_id=returned,
        version_unresolved=returned is None,
        checked_at=now.isoformat(),
        expires_at=(now + DEFAULT_EXPIRY).isoformat(),
        adapter_version="openai_probe.mjs/0",
        sdk_version=obs["flags"]["sdk"],
        effective_session_config={
            "raw_event_log": obs.get("log_file"),
            "audio": "audio/pcm 24000 in and out, voice marin",
            "turn_detection": None,
            "model_doc": MODEL_DOC,
        },
        operator_terms_attestation=None,
        rows=tuple(rows),
        limitations=(
            "Returned model equals the requested ID; docs list a single snapshot, no alias resolution observed",
            "Audio inputs were synthetic silence; text turns drove the conversation",
            "Server VAD, interruption by speech and truncation outcome were not exercised",
            "Terms, retention, quotas and pricing need operator attestation",
        ),
    )


def load(path: str, report_id: str) -> CapabilityReport:
    with open(path) as f:
        return build_report(json.load(f), report_id)
