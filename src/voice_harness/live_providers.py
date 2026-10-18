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
from .live_scenario import LIVE_LIMITS, LiveScenarioRunner

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

LIVE_PROVIDERS = {"openai-realtime-live": "openai_realtime", "nova-sonic-live": "aws_nova_sonic"}
# The model under test, as recorded for judge independence checks (a judge must not be the tested model).
TESTED_MODELS = {
    "openai-realtime-live": {"provider": "openai", "model_id": "gpt-realtime-2.1", "family": "openai_realtime"},
    "nova-sonic-live": {"provider": "aws", "model_id": "amazon.nova-2-5-sonic", "family": "aws_nova_sonic"},
}


class PreconditionFailed(Exception):
    """A scenario step could not be set up (e.g. identity was not established); not a provider defect."""


def _nova_env():
    from .adapters.nova_sonic import SESSION_ENV

    env = {}
    for line in SESSION_ENV.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value.strip("'\"")
    return env


def availability(provider_config_id, environ=None):
    """(ok, reason). Strict speech approval needs a live ASR backend as well as the provider itself."""
    environ = os.environ if environ is None else environ
    if provider_config_id not in LIVE_PROVIDERS:
        return False, "NOT_A_LIVE_PROVIDER"
    if environ.get(LIVE_FLAG) != "openai" or not environ.get("OPENAI_API_KEY"):
        return False, "NO_ASR_BACKEND_CONFIGURED"
    if provider_config_id == "openai-realtime-live":
        return True, None
    from .adapters.nova_sonic import SESSION_ENV

    return (True, None) if SESSION_ENV.exists() else (False, "NO_AWS_SESSION")


def _adapter(provider_config_id):
    config = {"tools": TOOLS}
    if provider_config_id == "openai-realtime-live":
        from .adapters.openai_realtime import OpenAIRealtimeAdapter

        config.update(instructions=INSTRUCTIONS, turn_detection="server_vad")
        return OpenAIRealtimeAdapter(), config
    from .adapters.nova_sonic import NovaSonicAdapter

    config.update(system_prompt=INSTRUCTIONS, env=_nova_env())
    return NovaSonicAdapter(), config


async def run_live(engine, run_id, provider_config_id, scenario_id, *, seed=0):
    """Open the provider session, run the catalogue scenario and close the session; returns the runner summary."""
    from .adapters.openai_realtime import synth_caller_pcm

    adapter, config = _adapter(provider_config_id)
    config["session_id"] = engine.database.get(run_id)["session_id"]
    await adapter.open_session(config)
    openai = provider_config_id == "openai-realtime-live"

    async def before_turn(index):
        # The cancellation scenario needs a trusted confirmation the model cannot invent: issued by the
        # orchestrator after the caller has verified, then handed to the model as text.
        if scenario_id != "commit-cancel-confirmed-001" or index != 1:
            return
        try:
            request, digest = engine.present_cancellation(run_id, "payment_001")
        except ValueError as exc:
            raise PreconditionFailed(str(exc)) from exc
        accepted = engine.confirm(run_id, engine.context(run_id), request, "live-obs", digest, "affirm")
        text = ("[orchestrator] The customer confirmed the cancellation. confirmation_id="
                + accepted["confirmation_id"] + " - use it with cancel_payment.")
        if openai:
            await adapter.send_text(text, respond=False)
        else:
            await adapter.send_text(text)

    runner = LiveScenarioRunner(
        engine, run_id, adapter, IndependentAssessor(engine, select_backend()), scenario_id,
        caller_audio=lambda text: synth_caller_pcm(text, trail_ms=1200), before_turn=before_turn, seed=seed)
    reason = "done"
    try:
        return await runner.run()
    except asyncio.CancelledError:
        reason = "cancelled"
        raise
    finally:
        with suppress(Exception):  # closing a failed session must not mask the run outcome
            await adapter.close(reason)


__all__ = ["LIVE_LIMITS", "LIVE_PROVIDERS", "TESTED_MODELS", "PreconditionFailed", "availability", "run_live"]
