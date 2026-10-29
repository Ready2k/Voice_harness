"""Build a CapabilityReport from scripts/probe/elevenlabs_probe.py observations (Phase 0)."""

import json
from datetime import UTC, datetime

from .capability_report import (
    DEFAULT_EXPIRY,
    REQUIRED_ROWS,
    CapabilityReport,
    CapabilityRow,
    Evidence,
)

# No row cites documentation: nothing here is "verified"; probe-backed rows are "observed" (docs/PHASE4_NOTES_elevenlabs.md).
_RULES = {
    "input_codecs_rates": ("observed", "pcm_24000 echoed in conversation_initiation_metadata; other formats untested"),
    "output_codecs_rates": ("observed", "pcm_24000 echoed in conversation_initiation_metadata; other formats untested"),
    "audio_in": ("observed", "synthetic speech streamed as user_audio_chunk frames was transcribed; the server needs trailing silence to detect the end of speech"),
    "audio_out": ("observed", "audio events with audio_base_64 (pcm_24000)"),
    "tool_arguments_and_results": ("observed", "client_tool_call by tool_call_id, client_tool_result by the same id, spoken reply used the result; tools are stored on the agent, not per session"),
    "transcript_finality": ("observed", "agent_response and user_transcript are final text only; agent_response can arrive before its audio has finished"),
    "turn_signalling": ("observed", "no response start or end marker; turn boundaries are inferred from agent_response, tool calls, audio quiet and interruption"),
    "usage_granularity": ("observed", "usage event seen on the socket"),
    "cancel_truncate": ("observed", "server interruption on barge-in; there is no client cancel or truncate message, so audio_end_ms cannot be reported to the server"),
    "reconnect_resume": ("unknown", "a fresh connection works; resume was not exercised"),
    "audio_alignment": ("unknown", "no alignment fields were examined"),
    "direct_mcp": ("unknown", "not exercised; tools are client tools bridged by the harness gateway"),
}


def build_report(obs: dict, report_id: str, attestation: str | None = None) -> CapabilityReport:
    now = datetime.now(UTC)
    ev, notes = obs["evidence"], obs.get("notes", {})
    rows = []
    for name in REQUIRED_ROWS:
        status, rule_note = _RULES[name]
        ids = tuple(dict.fromkeys(ev.get(name, ())))[:20]
        if name == "usage_granularity" and not ids:
            # No usage event was seen in the whole session: unsupported, evidenced by the session's metadata event.
            status, ids = "unsupported", tuple(dict.fromkeys(ev.get("input_codecs_rates", ())))[:1]
        if status != "unknown" and not ids:
            status = "unknown"  # fail closed: a row without probe evidence is never claimed
        rows.append(
            CapabilityRow(
                name=name,
                status=status,
                evidence=Evidence(probe_event_ids=ids),
                limitations=(rule_note, *notes.get(name, [])[:6]),
                tool_mode="gateway_bridge" if name == "tool_arguments_and_results" else None,
            )
        )
    meta = obs["meta"]
    return CapabilityReport(
        report_id=report_id,
        provider_family="elevenlabs_agents",
        api_surface="agents conversation websocket wss://api.elevenlabs.io/v1/convai/conversation",
        region=None,
        requested_model_id="elevenlabs-agent",
        returned_model_id=None,
        version_unresolved=True,
        checked_at=now.isoformat(),
        expires_at=(now + DEFAULT_EXPIRY).isoformat(),
        adapter_version="elevenlabs_probe.py/0",
        sdk_version=obs["flags"]["sdk"],
        effective_session_config={
            "raw_event_log": obs.get("log_file"),
            "audio": "pcm_24000 in and out",
            "turn_detection": "server VAD only; no manual commit",
            "initiation_metadata_keys": sorted(meta.get("initiationMetadata") or {}),
            "agent_llm": "not echoed by the session; recorded per run by the harness",
        },
        operator_terms_attestation=attestation,
        rows=tuple(rows),
        limitations=(
            "The platform has no single model id: the agent's LLM is part of what is under test and is not observable on the socket",
            "Barge-in was exercised with one synthetic phrase only",
            "No client cancel or truncate message exists, so AC-09 excludes the interruption dimension (detection only)",
            "No row cites documentation",
            "Terms, retention, quotas and pricing need operator attestation; none is recorded here",
        ),
    )


def load(path: str, report_id: str, attestation: str | None = None) -> CapabilityReport:
    with open(path) as f:
        return build_report(json.load(f), report_id, attestation)
