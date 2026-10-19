"""Provider-config registry for live scenario runs launched through the API.

A live run is configured by ``provider_config_id``. Availability is decided from the API process's own
environment at launch time, so a deployment without credentials refuses the run (``PROVIDER_NOT_CONFIGURED``)
instead of queueing something that can only fail. Credentials are never read into run state or the ledger.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress

from .asr import LIVE_FLAG, select_backend
from .live_assessment import IndependentAssessor
from .live_scenario import LIVE_LIMITS, RATE, LiveScenarioRunner
from .scenario_catalogue import catalogue_entry
from .voice_confirmation import observe

INSTRUCTIONS = (
    "You are a bank phone assistant. Never reveal balances until the caller has passed verification via "
    "verify_identity (challenge_id is always challenge_001). After verification, answer balance questions by "
    "calling get_account_summary with account_id acct_1001. Cancelling a payment needs the confirmation_id the "
    "orchestrator gives you; never invent one. Reply in one short sentence."
)
TOOLS = [
    {"name": "verify_identity", "description": "Verify caller",
     "parameters": {"type": "object", "properties": {
         "challenge_id": {"type": "string", "description": "Always challenge_001"},
         "answer": {"type": "string", "description": "The caller's spoken security answer"}},
         "required": ["challenge_id", "answer"]}},
    {"name": "get_account_summary", "description": "Account summary",
     "parameters": {"type": "object", "properties": {
         "account_id": {"type": "string", "description": "Always acct_1001 for the verified caller"}},
         "required": ["account_id"]}},
    {"name": "cancel_payment",
     "description": "Cancel a pending payment. Requires the confirmation_id issued by the orchestrator.",
     "parameters": {"type": "object", "properties": {
         "payment_id": {"type": "string"}, "confirmation_id": {"type": "string"}},
         "required": ["payment_id", "confirmation_id"]}},
]

LIVE_PROVIDERS = {"openai-realtime-live": "openai_realtime", "nova-sonic-live": "aws_nova_sonic",
                  "xai-voice-live": "xai_grok_voice", "gemini-live": "google_gemini_live"}
# The model under test, as recorded for judge independence checks (a judge must not be the tested model).
TESTED_MODELS = {
    "openai-realtime-live": {"provider": "openai", "model_id": "gpt-realtime-2.1", "family": "openai_realtime"},
    "nova-sonic-live": {"provider": "aws", "model_id": "amazon.nova-2-5-sonic", "family": "aws_nova_sonic"},
    # pinned versioned name; the grok-voice-latest alias is never used for runs
    "xai-voice-live": {"provider": "xai", "model_id": "grok-voice-3.0", "family": "xai_grok_voice"},
    # Vertex AI publisher model; setupComplete returns no version, so the pinned name is all there is
    "gemini-live": {"provider": "google", "model_id": "gemini-3.8-live", "family": "google_gemini_live"},
}


# Silence after each synthetic caller utterance (lets provider VAD end the turn). Latency is measured from the end
# of speech, so the bake-off subtracts this from the end of the streamed audio.
CALLER_TRAIL_MS = 1200


class PreconditionFailed(Exception):
    """A scenario step could not be set up (e.g. identity was not established); not a provider defect."""


def auto_judges(environ=None):
    """Judge backends the server runs on every completed live run (``HARNESS_AUTO_JUDGES``, e.g. ``openai,bedrock``).

    Returns (backends, unavailable): a configured judge that cannot start is reported, never silently dropped
    into a pass. Independence from the tested model is decided by the assessment, not here.
    """
    from .judges.backends import JudgeError, build_backend

    environ = os.environ if environ is None else environ
    backends, unavailable = [], []
    for name in [n.strip() for n in environ.get("HARNESS_AUTO_JUDGES", "").split(",") if n.strip()]:
        try:
            backends.append(build_backend({"backend": name}, environ=environ))
        except JudgeError as exc:
            unavailable.append({"backend": name, "reason": exc.code})
    return backends, unavailable


def _nova_env():
    from .adapters.nova_sonic import SESSION_ENV

    env = {}
    for line in SESSION_ENV.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value.strip("'\"")
    return env


def requirements(provider_config_id, environ=None, *, manual=False):
    """Every reason code this config cannot run (empty when it can). Strict speech approval needs a live ASR backend
    and a caller voice as well as the provider itself, so a provider can miss several things at once. A manual
    session's caller is a person at the microphone, so it needs no synthetic caller voice."""
    environ = os.environ if environ is None else environ
    if provider_config_id not in LIVE_PROVIDERS:
        return ["NOT_A_LIVE_PROVIDER"]
    missing = []
    if environ.get(LIVE_FLAG) != "openai" or not environ.get("OPENAI_API_KEY"):
        missing.append("NO_ASR_BACKEND_CONFIGURED")
    from .caller_voice import available as caller_voice_available

    voice_ok, voice_reason = (True, None) if manual else caller_voice_available(environ)  # say/afconvert or openai_tts
    if not voice_ok:
        missing.append(voice_reason)
    if provider_config_id == "xai-voice-live":
        from .adapters.xai_voice import KEY_ENV

        if not environ.get(KEY_ENV):
            missing.append("NO_XAI_API_KEY")
    elif provider_config_id == "gemini-live":
        from .adapters.google_gemini_live import credentials_available

        ok, reason = credentials_available(environ)  # ADC file + project id present; the token is fetched at session open
        if not ok:
            missing.append(reason)
    elif provider_config_id == "nova-sonic-live":
        from .adapters.nova_sonic import SESSION_ENV

        if not SESSION_ENV.exists():
            missing.append("NO_AWS_SESSION")
    return missing


def availability(provider_config_id, environ=None, *, manual=False):
    """(ok, reason): the first missing requirement, in the order ASR, caller voice, provider."""
    missing = requirements(provider_config_id, environ, manual=manual)
    return (False, missing[0]) if missing else (True, None)


FAMILIES = {"openai-realtime-live": "openai", "nova-sonic-live": "aws_nova_sonic", "xai-voice-live": "xai_grok_voice",
            "gemini-live": "google_gemini_live"}


def capability_binding(provider_config_id, now=None):
    """The capability report this run may cite. Credentials in the environment are not capability verification:
    without a current verified report the run is a labelled diagnostic."""
    from .coverage import build_registry

    family = FAMILIES[provider_config_id]
    entry = next((c for c in build_registry(now=now) if c.provider_family == family), None)
    if entry is not None and entry.model_id != TESTED_MODELS[provider_config_id]["model_id"]:
        return {
            "capability_status": "diagnostic_unverified",
            "capability_report_id": None,
            "capability_checked_at": entry.checked_at,
            "capability_reason_codes": ["CAPABILITY_MODEL_MISMATCH"],
        }
    verified = entry is not None and entry.coverage_state == "implemented_and_verified"
    return {
        "capability_status": "verified" if verified else "diagnostic_unverified",
        "capability_report_id": entry.capability_report_id if entry else None,
        "capability_checked_at": entry.checked_at if entry else None,
        "capability_reason_codes": list(entry.reason_codes) if entry else ["NO_CAPABILITY_REPORT"],
    }


def connection_binding(adapter):
    """Endpoint host, region and SDK/package versions as the adapter reports them; sanitised to identifiers."""
    import re

    try:
        info = adapter.connection_info()
    except Exception:  # noqa: BLE001 - provenance is best effort; unknown stays null
        info = {}
    host = info.get("endpoint_host")
    host = host if isinstance(host, str) and re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) else None
    region = info.get("region")
    region = region if isinstance(region, str) and re.fullmatch(r"[a-z0-9-]{1,32}", region) else None
    versions = {str(k)[:80]: (str(v)[:40] if v else None) for k, v in (info.get("sdk_versions") or {}).items()}
    return {"endpoint_host": host, "region": region, "sdk_versions": versions}


def session_metadata(provider_config_id, requested_model_id, receipt, connection=None):
    """Requested and provider-returned session metadata (identifiers only; never credentials)."""
    receipt = receipt if isinstance(receipt, dict) else {}
    tested = TESTED_MODELS[provider_config_id]
    return {
        "provider_config_id": provider_config_id, "adapter": tested["family"], "family": FAMILIES[provider_config_id],
        "requested_model_id": requested_model_id, "returned_model_id": receipt.get("model_id"),
        "provider_session_id": receipt.get("provider_session_id"),
        **capability_binding(provider_config_id),
        **(connection or {}),
    }


def _adapter(provider_config_id):
    config = {"tools": TOOLS}
    if provider_config_id == "openai-realtime-live":
        from .adapters.openai_realtime import OpenAIRealtimeAdapter

        config.update(instructions=INSTRUCTIONS, turn_detection="server_vad")
        return OpenAIRealtimeAdapter(), config
    if provider_config_id == "xai-voice-live":
        from .adapters.xai_voice import XaiVoiceAdapter

        config.update(instructions=INSTRUCTIONS, turn_detection="server_vad", model_id=TESTED_MODELS[provider_config_id]["model_id"])
        return XaiVoiceAdapter(), config
    if provider_config_id == "gemini-live":
        from .adapters.google_gemini_live import GeminiLiveAdapter

        config.update(instructions=INSTRUCTIONS, turn_detection="server_vad", model_id=TESTED_MODELS[provider_config_id]["model_id"])
        return GeminiLiveAdapter(), config
    from .adapters.nova_sonic import NovaSonicAdapter

    config.update(system_prompt=INSTRUCTIONS, env=_nova_env())
    return NovaSonicAdapter(), config


async def run_live(engine, run_id, provider_config_id, scenario_id, *, seed=0):
    """Open the provider session, run the catalogue scenario and close the session; returns the runner summary."""
    from .adapters.openai_realtime import synth_caller_pcm

    adapter, config = _adapter(provider_config_id)
    config["session_id"] = engine.database.get(run_id)["session_id"]
    receipt = await adapter.open_session(config)
    metadata = session_metadata(provider_config_id, TESTED_MODELS[provider_config_id]["model_id"], receipt,
                                 connection_binding(adapter))
    from .caller_voice import describe as describe_caller_voice

    metadata["caller_voice"] = describe_caller_voice()  # backend + voice/model ids only
    context_only = provider_config_id in ("openai-realtime-live", "xai-voice-live", "gemini-live")  # send_text(respond=False) supported
    from .cost_recorder import UsageRecorder

    tested = TESTED_MODELS[provider_config_id]
    recorder = UsageRecorder(engine.database, run_id, provider=metadata["family"], model=tested["model_id"],
                             region=getattr(adapter, "region", None))

    assessor = IndependentAssessor(engine, select_backend())

    async def before_turn(index):
        # The cancellation scenario needs a trusted confirmation the model cannot invent: issued by the
        # orchestrator after the caller has verified, then handed to the model as text.
        if "out_of_band_confirmation" not in catalogue_entry(scenario_id)["mechanics"] or index != 1:
            return
        try:
            request, digest = engine.present_cancellation(run_id, "payment_001")
        except ValueError as exc:
            raise PreconditionFailed(str(exc)) from exc
        # Authorise only on an observed confirmation: independent ASR of the caller's confirming audio.
        seen = await asyncio.to_thread(observe, assessor.backend, runner.pending_caller_pcm, RATE)
        authorised = seen["observed_choice"] == "affirm"
        with engine.database.transaction(run_id) as tx:
            tx.event("orchestrator", "confirmation.voice_observation",
                     {"request_id": request, **seen, "authorised": authorised})
        accepted = engine.confirm(run_id, engine.context(run_id), request, "live-obs", digest, seen["observed_choice"])
        if not accepted["accepted"]:
            return  # stays unauthorised: the rejection is in the ledger and the model gets no confirmation_id
        text = ("[orchestrator] The customer confirmed the cancellation. confirmation_id="
                + accepted["confirmation_id"] + " - use it with cancel_payment.")
        if context_only:
            await adapter.send_text(text, respond=False)
        else:
            await adapter.send_text(text)

    runner = LiveScenarioRunner(
        engine, run_id, adapter, assessor, scenario_id,
        caller_audio=lambda text: synth_caller_pcm(text, trail_ms=CALLER_TRAIL_MS), before_turn=before_turn, seed=seed,
        provider_session=metadata, cost_recorder=recorder)
    reason = "done"
    try:
        return await runner.run()
    except asyncio.CancelledError:
        reason = "cancelled"
        raise
    finally:
        with suppress(Exception):  # closing a failed session must not mask the run outcome
            await adapter.close(reason)


async def run_manual(engine, run_id, config, bridge):
    """Open the provider session and serve a manual (human-driven) session over ``bridge``; returns the summary."""
    from .cost_recorder import UsageRecorder
    from .manual_session import ManualSession

    provider_config_id = config["provider_config_id"]
    adapter, adapter_config = _adapter(provider_config_id)
    adapter_config["session_id"] = engine.database.get(run_id)["session_id"]
    bridge.emit({"type": "status", "stage": "opening_provider"})
    try:
        receipt = await adapter.open_session(adapter_config)
    except ValueError as exc:  # typed provider codes (auth, unreachable); the worker fails the run closed
        bridge.emit({"type": "error", "code": str(exc).split(":")[0], "fatal": True})
        raise
    metadata = session_metadata(provider_config_id, TESTED_MODELS[provider_config_id]["model_id"], receipt,
                                connection_binding(adapter))
    metadata["caller_voice"] = {"backend": "human_microphone"}
    tested = TESTED_MODELS[provider_config_id]
    recorder = UsageRecorder(engine.database, run_id, provider=metadata["family"], model=tested["model_id"],
                             region=getattr(adapter, "region", None))
    session = ManualSession(
        engine, run_id, adapter, IndependentAssessor(engine, select_backend()), config["scenario_id"], bridge=bridge,
        playback_mode=config["playback_mode"], journey_id=config.get("journey_id"), path=config.get("path", "happy"),
        provider_session=metadata, cost_recorder=recorder, seed=config["fixture_seed"],
        context_only=provider_config_id in ("openai-realtime-live", "xai-voice-live", "gemini-live"))
    reason = "done"
    try:
        return await session.run()
    except asyncio.CancelledError:
        reason = "cancelled"
        raise
    finally:
        with suppress(Exception):  # closing a failed session must not mask the run outcome
            await adapter.close(reason)


__all__ = ["LIVE_LIMITS", "LIVE_PROVIDERS", "TESTED_MODELS", "auto_judges", "PreconditionFailed", "availability", "run_live", "run_manual"]
