"""Build a CapabilityReport from scripts/probe/openai_live_probe.py observations (Phase 0)."""

import json
from datetime import UTC, datetime

from .capability_report import (
    DEFAULT_EXPIRY,
    REQUIRED_ROWS,
    CapabilityReport,
    CapabilityRow,
    Evidence,
)

# No row cites documentation: nothing here is "verified"; probe-backed rows are "observed" (docs/OPENAI_LIVE.md).
_RULES = {
    "input_codecs_rates": ("observed", "session.start declared audio/pcm 24 kHz and session.started accepted it; other rates untested"),
    "output_codecs_rates": ("observed", "audio/pcm 24 kHz output deltas; other rates untested"),
    "audio_in": ("observed", "synthetic speech streamed as session.input_audio.append frames (continuous input, silence included) was transcribed"),
    "audio_out": ("observed", "session.output_audio.delta events"),
    "tool_arguments_and_results": ("observed", "tools are delegated to a backend model: a function_call appears inside response.event and its result goes back as a response item; harness-bridged"),
    "transcript_finality": ("observed", "input and output transcripts arrive as session.*_transcript.delta fragments; the adapter assembles the final text"),
    "turn_signalling": ("observed", "no turn-start marker; nested response.completed ends a reply; the harness groups replies by audio silence and backend activity, which is a tuned diagnostic heuristic"),
    "usage_granularity": ("observed", "session.usage.updated and session.closed carry usage; per-response usage nested in response.completed"),
    "cancel_truncate": ("unknown", "Live has no truncate primitive and no cancel was exercised"),
    "reconnect_resume": ("unknown", "a fresh session works; resume was not exercised"),
    "audio_alignment": ("unknown", "no alignment fields were examined"),
    "direct_mcp": ("unknown", "not exercised; tools are delegated function calls bridged by the harness gateway"),
}


def build_report(obs: dict, report_id: str, attestation: str | None = None) -> CapabilityReport:
    now = datetime.now(UTC)
    ev, notes = obs["evidence"], obs.get("notes", {})
    rows = []
    for name in REQUIRED_ROWS:
        status, rule_note = _RULES[name]
        ids = tuple(dict.fromkeys(ev.get(name, ())))[:20]
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
    started = meta.get("sessionStarted") or {}
    return CapabilityReport(
        report_id=report_id,
        provider_family="openai_live",
        api_surface="live websocket wss://api.openai.com/v1/live/sessions",
        region=None,
        requested_model_id=obs["flags"]["model"],
        returned_model_id=started.get("model_id"),
        version_unresolved=started.get("model_id") in (None, obs["flags"]["model"]),
        checked_at=now.isoformat(),
        expires_at=(now + DEFAULT_EXPIRY).isoformat(),
        adapter_version="openai_live_probe.py/0",
        sdk_version=obs["flags"]["sdk"],
        effective_session_config={
            "raw_event_log": obs.get("log_file"),
            "audio": "audio/pcm 24000 in and out",
            "backend_model_id": obs["flags"].get("backend"),
            "turn_detection": "provider endpointing; the harness never sends commit or response.create for audio turns",
        },
        operator_terms_attestation=attestation,
        rows=tuple(rows),
        limitations=(
            "The probe drives the harness adapter and records its native provider events; session setup passes through the adapter",
            f"Voice model {obs['flags']['model']} delegates tool use and reasoning to backend {obs['flags'].get('backend')}; both are under test",
            "Live has no truncate primitive, so AC-09 excludes the interruption dimension (detection only)",
            "No row cites documentation",
            "Terms, retention, quotas and pricing need operator attestation; none is recorded here",
        ),
    )


def load(path: str, report_id: str, attestation: str | None = None) -> CapabilityReport:
    with open(path) as f:
        return build_report(json.load(f), report_id, attestation)
