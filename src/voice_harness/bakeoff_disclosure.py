"""Provider configuration parity disclosure for the bake-off report (docs/PROVIDER_CONFIG_PARITY.md).

Every run binding records the provider-specific settings that can move scores: sampling sent, output voice, the
independent ASR vendor against the tested vendor, an ElevenLabs agent's LLM, turn control and confirmation delivery.
``parity`` reduces one binding to those fields for the cell observation; ``disclose`` aggregates them per provider and
lists every setting that is not equal across the compared providers, so an X-over-Y statement can be read against it.
"""

from __future__ import annotations

from .live_providers import SAMPLING_POLICY, TESTED_MODELS
from .target_prompts import delta

NOT_RECORDED = "not_recorded"


def parity(binding):
    """The parity fields of one run binding (``run.live_binding`` provider metadata); None for a binding written
    before parity was recorded."""
    if "sampling" not in binding:
        return None
    transport = binding.get("harness_transport") or {}
    sampling = binding.get("sampling") or {}
    return {
        "sampling_policy": sampling.get("policy"), "sampling_overrides": sampling.get("overrides_sent"),
        "output_limits": sampling.get("output_limits"), "sampling_conforms": sampling.get("conforms"),
        "stored_agent_temperature": sampling.get("stored_agent_temperature"),
        "voice": (binding.get("output_voice") or {}).get("name"),
        "voice_source": (binding.get("output_voice") or {}).get("source"),
        "asr_vendor": (binding.get("asr") or {}).get("vendor"), "asr_model": (binding.get("asr") or {}).get("model"),
        "asr_relation": (binding.get("asr") or {}).get("relation"),
        "agent_llm": binding.get("agent_llm"), "agent_llm_vendor": binding.get("agent_llm_vendor"),
        "agent_config_current": binding.get("agent_config_current"),
        "turn_commit": transport.get("turn_commit"), "commit_delay_ms": transport.get("commit_delay_ms"),
        "provider_input_rate": transport.get("provider_input_rate"), "client_truncation": transport.get("client_truncation"),
        "confirmation_delivery": binding.get("confirmation_delivery"),
        "prompt_profile": (binding.get("target_prompt") or {}).get("profile"),
    }


def _values(rows, key, missing=NOT_RECORDED):
    return sorted({missing if r.get(key) is None else str(r[key]) for r in rows})


def _one(values):
    return values[0] if len(values) == 1 else values


def disclose(providers, observed, prompt_profile="standardised-v2"):
    """Per-provider configuration, the settings that differ across providers and the report-level limitations."""
    rows, per = [], {}
    for provider in providers:
        mine = [o["parity"] for o in observed if o["provider"] == provider and o.get("parity")]
        overrides = sorted({f"{k}={v}" for r in mine for k, v in (r.get("sampling_overrides") or {}).items()})
        temps = _values([r for r in mine if r.get("stored_agent_temperature") is not None], "stored_agent_temperature")
        row = {
            "provider": provider, "model_id": TESTED_MODELS[provider]["model_id"], "runs_recorded": len(mine),
            "agent_llm": _one(_values(mine, "agent_llm")) if provider == "elevenlabs-live" else None,
            "agent_llm_vendor": _one(_values(mine, "agent_llm_vendor")) if provider == "elevenlabs-live" else None,
            "sampling_policy": _one(_values(mine, "sampling_policy")), "sampling_overrides": overrides,
            "stored_agent_temperature": temps or None,
            "output_limits": sorted({f"{k}={v}" for r in mine for k, v in (r.get("output_limits") or {}).items()}),
            "sampling_conforms": (None if not mine or any(r.get("sampling_conforms") is None for r in mine)
                                  else all(r["sampling_conforms"] for r in mine)),
            "voice": _one(_values(mine, "voice")), "asr": _one(_values(mine, "asr_relation")),
            "asr_vendor": _one(_values(mine, "asr_vendor")),
            "turn_commit": _one(_values(mine, "turn_commit")), "commit_delay_ms": _one(_values(mine, "commit_delay_ms", "not_applicable")),
            "provider_input_rate": _one(_values(mine, "provider_input_rate")),
            "client_truncation": _one(_values(mine, "client_truncation")),
            "confirmation_delivery": _one(_values([r for r in mine if r.get("confirmation_delivery")], "confirmation_delivery") or [None]),
            "prompt_delta": delta(prompt_profile, provider) if prompt_profile == "advanced-v1" else [],
        }
        rows.append(row)
        per[provider] = row
    differs = []
    for key in ("sampling_overrides", "output_limits", "voice", "turn_commit", "commit_delay_ms", "provider_input_rate",
                "client_truncation", "confirmation_delivery", "prompt_delta"):
        seen = {repr(per[p][key]) for p in providers if per[p]["runs_recorded"] or key == "prompt_delta"}
        if key == "confirmation_delivery":
            seen = {repr(per[p][key]) for p in providers if per[p][key] is not None}
        if len(seen) > 1:
            differs.append(key)
    limitations = []
    same = [p for p in providers if "SAME_VENDOR_ASR" in str(per[p]["asr"])]
    if same:
        limitations.append("Independent ASR, which decides safe-speech and untestable replies, is from the same vendor as "
                           + ", ".join(same) + "; their speech outcomes are not judged by an independent vendor.")
    stale = [p for p in providers if per[p]["sampling_conforms"] is False]
    if stale:
        limitations.append("Sampling overrides outside the provider_default policy were in effect for " + ", ".join(stale)
                           + " (see sampling_overrides / stored_agent_temperature).")
    unrecorded = [p for p in providers if not per[p]["runs_recorded"]]
    if unrecorded and observed:
        limitations.append("Configuration parity was not recorded for runs of " + ", ".join(unrecorded) + " (older ledgers).")
    if "elevenlabs-live" in providers:
        limitations.append("An ElevenLabs Agents result is partly its agent LLM's result (" + str(per["elevenlabs-live"]["agent_llm"])
                           + "); the session does not echo the LLM, so it is taken from the provisioned agent.")
    if "voice" in differs:
        limitations.append("Output voices are each vendor's default and differ; listening-quality scores compare voices as well as models.")
    if prompt_profile == "advanced-v1":
        limitations.append("advanced-v1 prompts are vendor-tuned: each provider's prompt_delta lists what it got beyond the common core.")
    return {"sampling_policy": SAMPLING_POLICY, "prompt_profile": prompt_profile, "providers": rows,
            "differs": differs, "limitations": limitations}
