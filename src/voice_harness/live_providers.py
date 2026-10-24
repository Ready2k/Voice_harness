"""Provider-config registry for live scenario runs launched through the API.

A live run is configured by ``provider_config_id``. Availability is decided from the API process's own
environment at launch time, so a deployment without credentials refuses the run (``PROVIDER_NOT_CONFIGURED``)
instead of queueing something that can only fail. Credentials are never read into run state or the ledger.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress

from .asr import LIVE_FLAG, select_backend
from .contracts import TOOLS as TOOL_CONTRACTS
from .events import PROCESS_CLOCK_DOMAIN
from .live_assessment import IndependentAssessor
from .live_scenario import LIVE_LIMITS, RATE, TURN_TIMEOUT_S, LiveScenarioRunner
from .scenario_catalogue import catalogue_entry
from .target_prompts import instructions as target_instructions
from .target_prompts import provenance as prompt_provenance
from .voice_confirmation import observe

INSTRUCTIONS = target_instructions("standardised-v2")
LEGACY_INSTRUCTIONS = (
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


# Every tool required by a live catalogue scenario must be visible to every provider.
# Use the gateway's argument contracts so the advertised interface cannot drift.
for _name, _description in {
    "list_recent_transactions": "List recent transactions after verification. Use account_id acct_1001 and limit 10.",
    "get_transaction_details": "Get a transaction after verification using the transaction_id supplied by the caller.",
    "get_policy_text": "Answer questions about identity verification from policy_id identity, version 1. No verification needed.",
    "transfer_to_human": "Transfer on request or when help is needed. Use queue support, fraud or vulnerable_customer and a brief reason. No verification needed.",
}.items():
    TOOLS.append({"name": _name, "description": _description,
                  "parameters": TOOL_CONTRACTS[_name].model_json_schema(by_alias=True)})


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
    if not SESSION_ENV.exists():  # building a config needs no credentials; requirements() reports NO_AWS_SESSION
        return env
    for line in SESSION_ENV.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value.strip("'\"")
    return env


def requirements(provider_config_id, environ=None, *, manual=False, caller=None):
    """Every reason code this config cannot run (empty when it can). Strict speech approval needs a live ASR backend
    and a caller voice as well as the provider itself, so a provider can miss several things at once. A manual
    session's caller is a person at the microphone, so it needs no synthetic caller voice."""
    environ = os.environ if environ is None else environ
    caller = caller or {}
    if caller.get("fixed_backend", "system") != "system":
        environ = {**environ, "VOICE_HARNESS_CALLER_VOICE": caller["fixed_backend"]}
    if provider_config_id not in LIVE_PROVIDERS:
        return ["NOT_A_LIVE_PROVIDER"]
    missing = []
    if environ.get(LIVE_FLAG) != "openai" or not environ.get("OPENAI_API_KEY"):
        missing.append("NO_ASR_BACKEND_CONFIGURED")
    from .caller_voice import available as caller_voice_available

    voice_ok, voice_reason = (True, None) if manual or caller.get("mode") == "live_ai" else caller_voice_available(environ)
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


def availability(provider_config_id, environ=None, *, manual=False, caller=None):
    """(ok, reason): the first missing requirement, in the order ASR, caller voice, provider."""
    missing = requirements(provider_config_id, environ, manual=manual, caller=caller)
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


def session_metadata(provider_config_id, requested_model_id, receipt, connection=None, prompt_profile="standardised-v2"):
    """Requested and returned session metadata, including public prompt provenance; never credentials."""
    receipt = receipt if isinstance(receipt, dict) else {}
    tested = TESTED_MODELS[provider_config_id]
    return {
        "provider_config_id": provider_config_id, "adapter": tested["family"], "family": FAMILIES[provider_config_id],
        "requested_model_id": requested_model_id, "returned_model_id": receipt.get("model_id"),
        "provider_session_id": receipt.get("provider_session_id"),
        **capability_binding(provider_config_id),
        "target_prompt": prompt_provenance(prompt_profile, LEGACY_INSTRUCTIONS if prompt_profile == "legacy-v1" else target_instructions(prompt_profile), TOOLS),
        **(connection or {}),
    }


def _adapter(provider_config_id, prompt_profile="standardised-v2"):
    instructions = LEGACY_INSTRUCTIONS if prompt_profile == "legacy-v1" else target_instructions(prompt_profile)
    config = {"tools": TOOLS}
    if provider_config_id == "openai-realtime-live":
        from .adapters.openai_realtime import OpenAIRealtimeAdapter

        config.update(instructions=instructions, turn_detection="server_vad")
        return OpenAIRealtimeAdapter(), config
    if provider_config_id == "xai-voice-live":
        from .adapters.xai_voice import XaiVoiceAdapter

        config.update(instructions=instructions, turn_detection="server_vad", model_id=TESTED_MODELS[provider_config_id]["model_id"])
        return XaiVoiceAdapter(), config
    if provider_config_id == "gemini-live":
        from .adapters.google_gemini_live import GeminiLiveAdapter

        config.update(instructions=instructions, turn_detection="server_vad", model_id=TESTED_MODELS[provider_config_id]["model_id"])
        return GeminiLiveAdapter(), config
    from .adapters.nova_sonic import NovaSonicAdapter

    config.update(system_prompt=instructions, env=_nova_env())
    return NovaSonicAdapter(), config


# A room mic is far noisier than the synthetic caller: a high threshold keeps background speech and noise
# from opening turns (and cutting the welcome), a short silence window still closes real turns promptly.
MIC_VAD = {"threshold": 0.8, "silence_duration_ms": 700, "prefix_padding_ms": 300}
MIC_VAD_PROVIDERS = ("openai-realtime-live", "xai-voice-live")


def configure_transport(provider_config_id, config, caller_mode):
    """Select native turn controls for the input source, without changing task or judge prompts."""
    if provider_config_id not in LIVE_PROVIDERS:
        raise ValueError("NOT_A_LIVE_PROVIDER")
    if caller_mode not in {"fixed", "live_ai", "human_microphone"}:
        raise ValueError("UNKNOWN_CALLER_MODE")
    nova = provider_config_id == "nova-sonic-live"
    if nova:
        # Nova owns endpointing and requires its existing continuous silence pump.
        config.pop("turn_detection", None)
        config["endpointing"] = "LOW" if caller_mode == "live_ai" else "MEDIUM"
        detection = "provider_vad"
    else:
        detection = "server_vad" if caller_mode == "human_microphone" else "manual"
        config["turn_detection"] = detection
        if detection == "server_vad" and provider_config_id in MIC_VAD_PROVIDERS:
            config["turn_detection"] = {"type": "server_vad", **MIC_VAD, **(
                {"interrupt_response": True, "create_response": True} if provider_config_id == "openai-realtime-live" else {})}
    if provider_config_id == "gemini-live" and detection == "server_vad" and caller_mode == "human_microphone":
        # Default end-of-speech detection answered 8-9 s after a short utterance in a room-mic session.
        config["activity_detection"] = {"endOfSpeechSensitivity": "END_SENSITIVITY_HIGH", "silenceDurationMs": 600,
                                        "prefixPaddingMs": 200}
    return {
        "profile_version": "provider-transport-v1", "caller_mode": caller_mode,
        "turn_detection": detection, "endpointing": config.get("endpointing") if nova else None,
        "turn_commit": "provider_vad" if nova else ("automatic" if detection == "server_vad" else "explicit"),
        "harness_input_rate": RATE, "provider_input_rate": 16000 if provider_config_id in {
            "gemini-live", "nova-sonic-live"} else RATE,
        "output_rate": RATE, "chunk_ms": 20, "caller_trailing_silence_ms": CALLER_TRAIL_MS if caller_mode == "fixed" else None,
        "client_truncation": provider_config_id in {"openai-realtime-live", "xai-voice-live"},
        "turn_timeout_seconds": TURN_TIMEOUT_S if caller_mode != "human_microphone" else None,
        "response_audio_seconds": LIVE_LIMITS["response_audio_seconds"],
    }


async def _open_timed(engine, run_id, adapter, config):
    """``adapter.open_session``, then a ledger event with how long the provider took to become ready.

    The event is written inside the caller's setup guard: if the evidence write fails the run fails closed."""
    started = time.monotonic()
    receipt = await adapter.open_session(config)
    ready = time.monotonic()
    with engine.database.transaction(run_id) as tx:
        tx.event("provider", "adapter.session_ready", {
            "adapter": type(adapter).__name__, "setup_ms": round((ready - started) * 1000),
            "started_ms": int(started * 1000), "ready_ms": int(ready * 1000), "clock_domain_id": PROCESS_CLOCK_DOMAIN})
    return receipt


async def run_live(engine, run_id, provider_config_id, scenario_id, *, seed=0, caller=None, evidence=None, prompt_profile="standardised-v2"):
    """Open the provider session, run the catalogue scenario and close the session; returns the runner summary."""
    from .adapters.openai_realtime import synth_caller_pcm
    from .contracts import CallerConfig

    caller = CallerConfig.model_validate(caller or {}).model_dump()
    if caller["mode"] == "live_ai":
        return await _run_interactive(engine, run_id, provider_config_id, scenario_id, caller, evidence, seed, prompt_profile)

    adapter, config = _adapter(provider_config_id, prompt_profile)
    transport = configure_transport(provider_config_id, config, "fixed")
    config["session_id"] = engine.database.get(run_id)["session_id"]
    try:
        receipt = await _open_timed(engine, run_id, adapter, config)
    except BaseException:
        with suppress(Exception):
            await asyncio.wait_for(adapter.close("setup_failed"), 5)
        raise
    metadata = session_metadata(provider_config_id, TESTED_MODELS[provider_config_id]["model_id"], receipt,
                                 connection_binding(adapter), prompt_profile=prompt_profile)
    from .caller_voice import describe as describe_caller_voice

    voice_env = dict(os.environ)
    if caller["fixed_backend"] != "system":
        voice_env["VOICE_HARNESS_CALLER_VOICE"] = caller["fixed_backend"]
    metadata["harness_transport"] = transport
    metadata["caller_mode"] = "fixed"
    metadata["caller_voice"] = describe_caller_voice(voice_env)
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
        caller_audio=lambda text: synth_caller_pcm(text, trail_ms=CALLER_TRAIL_MS, environ=voice_env), before_turn=before_turn, seed=seed,
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


async def _run_interactive(engine, run_id, provider_config_id, scenario_id, caller_config, evidence, seed, prompt_profile="standardised-v2"):
    from .adapters.openai_realtime import OpenAIRealtimeAdapter
    from .cost_recorder import UsageRecorder
    from .interactive_caller import MODEL, SCENARIOS, InteractiveScenarioRunner, RealtimeCaller, configuration

    if scenario_id not in SCENARIOS:
        raise ValueError("LIVE_CALLER_SCENARIO_UNSUPPORTED")
    adapter, config = _adapter(provider_config_id, prompt_profile)
    transport = configure_transport(provider_config_id, config, "live_ai")
    if provider_config_id == "openai-realtime-live":
        config["voice"] = "coral" if caller_config["voice"] == "marin" else "marin"
    config["session_id"] = engine.database.get(run_id)["session_id"]
    caller = RealtimeCaller(OpenAIRealtimeAdapter(), caller_config,
                            UsageRecorder(engine.database, run_id, provider="openai", model=MODEL, component="caller"))
    reason = "failed"
    deadline = time.monotonic() + caller_config["max_duration_seconds"]
    runner = None
    setup_stage = "TARGET"
    try:
        async with asyncio.timeout_at(deadline):
            receipt = await _open_timed(engine, run_id, adapter, config)
            setup_stage = "CALLER"
            caller_receipt = await caller.open(config["session_id"])
        metadata = session_metadata(provider_config_id, TESTED_MODELS[provider_config_id]["model_id"], receipt,
                                    connection_binding(adapter), prompt_profile=prompt_profile)
        metadata["harness_transport"] = transport
        metadata["caller_mode"] = "live_ai"
        metadata["caller_turn_control"] = "provider_vad_low" if provider_config_id == "nova-sonic-live" else "manual"
        metadata["requested_voice"] = config.get("voice") or {
            "nova-sonic-live": "matthew", "xai-voice-live": "ara"}.get(provider_config_id)
        metadata["caller_voice"] = {**configuration(caller_config), "session": caller_receipt}
        recorder = UsageRecorder(engine.database, run_id, provider=metadata["family"],
                                 model=TESTED_MODELS[provider_config_id]["model_id"])
        runner = InteractiveScenarioRunner(
            engine, run_id, adapter, IndependentAssessor(engine, select_backend()), scenario_id,
            caller=caller, caller_config=caller_config, evidence=evidence, seed=seed,
            duration_budget=max(0, deadline - time.monotonic()),
            provider_session=metadata, cost_recorder=recorder)
        result = await runner.run()
        reason = result["caller_stop_reason"]
        return result
    except TimeoutError:
        reason = "CALLER_SETUP_TIMEOUT"
        with engine.database.transaction(run_id) as tx:
            tx.event("orchestrator", "caller.session_ended", {"reason": reason, "failure": reason,
                     "turns": 0, "fidelity": "not_independently_assessed"})
        return {"turns": [], "adapter_errors": [], "caller_failure": reason, "caller_stop_reason": reason}
    except ValueError as exc:
        if runner is not None:
            raise
        # Only stable adapter codes are public; never include provider response bodies or credentials.
        code = str(exc).split(":")[0]
        known = {"PROVIDER_CONNECTION_CLOSED", "PROVIDER_AUTH_FAILED", "PROVIDER_AUTH_MISSING",
                 "PROVIDER_CREDIT_BALANCE_EXHAUSTED", "PROVIDER_INSUFFICIENT_QUOTA", "PROVIDER_UNREACHABLE"}
        reason = setup_stage + "_SETUP_" + (code if code in known else "FAILED")
        failure = reason if setup_stage == "CALLER" else None
        with engine.database.transaction(run_id) as tx:
            tx.event("orchestrator", "caller.session_ended", {"reason": reason, "failure": failure,
                     "turns": 0, "fidelity": "not_independently_assessed"})
        return {"turns": [], "adapter_errors": [], "caller_failure": failure,
                "setup_failure": reason, "caller_stop_reason": reason}
    except asyncio.CancelledError:
        reason = "cancelled"
        raise
    finally:
        for session in (caller, adapter):
            with suppress(Exception):
                await asyncio.wait_for(session.close(reason), 5)


async def run_manual(engine, run_id, config, bridge):
    """Open the provider session and serve a manual (human-driven) session over ``bridge``; returns the summary."""
    from .cost_recorder import UsageRecorder
    from .manual_session import ManualSession

    provider_config_id = config["provider_config_id"]
    prompt_profile = "standardised-v2"
    adapter, adapter_config = _adapter(provider_config_id, prompt_profile)
    transport = configure_transport(provider_config_id, adapter_config, "human_microphone")
    adapter_config["session_id"] = engine.database.get(run_id)["session_id"]
    bridge.emit({"type": "status", "stage": "opening_provider"})
    try:
        receipt = await adapter.open_session(adapter_config)
    except ValueError as exc:  # typed provider codes (auth, unreachable); the worker fails the run closed
        bridge.emit({"type": "error", "code": str(exc).split(":")[0], "fatal": True})
        raise
    metadata = session_metadata(provider_config_id, TESTED_MODELS[provider_config_id]["model_id"], receipt,
                                connection_binding(adapter), prompt_profile=prompt_profile)
    metadata["harness_transport"] = transport
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
