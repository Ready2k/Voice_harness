"""Build a CapabilityReport from scripts/probe/google_gemini_probe.py observations (Phase 0)."""

import json
from datetime import UTC, datetime

from .capability_report import (
    DEFAULT_EXPIRY,
    REQUIRED_ROWS,
    CapabilityReport,
    CapabilityRow,
    Evidence,
)

# The Vertex pages could only be read in part (navigation text, no protocol body), so no row cites a doc:
# nothing here is "verified"; probe-backed rows are "observed" (docs/PHASE4_NOTES_google.md).
_RULES = {
    "input_codecs_rates": ("observed", "audio/pcm;rate=16000 accepted as realtimeInput.audio; other rates untested"),
    "output_codecs_rates": ("observed", "audio/pcm without a rate in the mime type; 24 kHz inferred from byte count against the reported audio token count (4.71 s vs 4.72 s), not declared by the server"),
    "audio_in": ("observed", "synthetic speech streamed under automatic activity detection was transcribed and answered"),
    "audio_out": ("observed", "serverContent.modelTurn inlineData audio/pcm chunks"),
    "tool_arguments_and_results": ("observed", "toolCall with id, toolResponse by id, spoken answer used the result; no explicit acknowledgement message; text-only context (turnComplete=false) was accepted without a reply"),
    "transcript_finality": ("observed", "input and output transcripts arrive as incremental text fragments with an empty finished=true marker; no per-fragment finality otherwise"),
    "turn_signalling": ("observed", "no turn-start message; serverContent.turnComplete ends a turn (also the tool-calling turn); automatic activity detection starts replies without a client turn message"),
    "usage_granularity": ("observed", "usageMetadata on the turnComplete message: prompt and candidate token counts split by modality; no prices recorded"),
    "cancel_truncate": ("observed", "serverContent.interrupted on barge-in; there is no client cancel or truncate message, so audio_end_ms cannot be reported to the server"),
    "reconnect_resume": ("unknown", "sessionResumptionUpdate with a handle was issued; reconnecting with it was not exercised"),
    "audio_alignment": ("unknown", "no timing or alignment fields were seen"),
    "direct_mcp": ("unknown", "not exercised; tools are function declarations bridged by the harness gateway"),
}


def build_report(obs: dict, report_id: str, attestation: str | None = None) -> CapabilityReport:
    now = datetime.now(UTC)
    ev, notes = obs["evidence"], obs.get("notes", {})
    rows = []
    for name in REQUIRED_ROWS:
        status, rule_note = _RULES[name]
        ids = tuple(dict.fromkeys(ev.get(name, ())))[:20]
        if name == "output_codecs_rates":
            ids = tuple(dict.fromkeys(ev.get("audio_out", ())))[:20]
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
    region = meta["region"]
    glob = meta.get("globalEndpoint") or {}
    return CapabilityReport(
        report_id=report_id,
        provider_family="google_gemini_live",
        api_surface=f"Vertex AI Live websocket wss://{region}-aiplatform.googleapis.com/ws/google.cloud.aiplatform.v1.LlmBidiService/BidiGenerateContent",
        region=region,
        requested_model_id=meta["modelId"],
        returned_model_id=None,  # setupComplete does not echo the model
        version_unresolved=True,
        checked_at=now.isoformat(),
        expires_at=(now + DEFAULT_EXPIRY).isoformat(),
        adapter_version="google_gemini_probe.py/0",
        sdk_version=obs["flags"]["sdk"],
        effective_session_config={
            "raw_event_log": obs.get("log_file"),
            "audio": "16 kHz PCM in, 24 kHz PCM out (inferred)",
            "response_modalities": "AUDIO",
            "turn_detection": "automatic activity detection (server) used for speech; text turns via clientContent",
            "transcription": "inputAudioTranscription and outputAudioTranscription enabled",
            "credentials": "Application Default Credentials OAuth access token (never recorded)",
        },
        operator_terms_attestation=attestation,
        rows=tuple(rows),
        limitations=(
            f"Pinned {meta['modelId']}; the server returns no model version, so the version is unresolved",
            f"Only region {region} was used for runs; the global endpoint refused this model at connect "
            f"(setupComplete={glob.get('setupComplete')}, closed={json.dumps(glob.get('closed'))[:160]})",
            "Server speech detection ends the in-flight answer; audio arrives faster than real time (about 4x in one sample)",
            "Barge-in was exercised with one synthetic (macOS say) phrase only",
            "No cancel or truncate client message exists, so AC-09 excludes the interruption dimension (detection only)",
            "No row cites documentation: the Vertex Live pages were only partly readable",
            "Terms, retention, quotas and pricing are covered only by the operator attestation recorded on the report, if any",
        ),
    )


def load(path: str, report_id: str, attestation: str | None = None) -> CapabilityReport:
    with open(path) as f:
        return build_report(json.load(f), report_id, attestation)
