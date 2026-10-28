"""Per-run tested-model vs judge independence and eligibility (spec section 7).

Eligibility is evaluated per run: a model is excluded from judging itself, and in a material benchmark a judge
made by the tested model's vendor is excluded too (self-preference). Two judges on the same model do not
establish independence (they count once). Identity is compared as ``host`` (who serves the model: Bedrock and
Nova are both AWS) and ``vendor`` (who made it: a Bedrock Claude is Anthropic), not as raw provider labels.
"""

from __future__ import annotations

from .backends import JudgeConfig, model_host, model_vendor

BLINDING_LIMITATIONS = [
    "Provider-session events, identity keys and provider-issued ids are removed and free text is masked against "
    "registry-derived identity tokens; reply style and tool-call behaviour may still fingerprint a provider.",
    "Judges see synthetic transcripts and ledger metadata, not audio.",
]
SAME_HOST = "SAME_HOST_AS_TESTED_MODEL"
SAME_VENDOR = "SAME_VENDOR_AS_TESTED_MODEL"
SAME_PROVIDER = "SAME_PROVIDER_AS_TESTED_MODEL"  # kept for existing readers: raised with SAME_HOST
SAME_VENDOR_MATERIAL = "SAME_VENDOR_IN_MATERIAL_BENCHMARK"


def tested_identity(tested: dict) -> dict:
    """The tested model with explicit ``host`` and ``vendor`` (registry values win over inference). ``components``
    (e.g. GPT-Live's voice model and delegated backend, an ElevenLabs agent's LLM) each get a ``vendor`` too."""
    out = {**tested, "host": tested.get("host") or model_host(tested["provider"]),
           "vendor": tested.get("vendor") or model_vendor(tested["provider"], tested["model_id"], tested["family"])}
    if tested.get("components"):
        out["components"] = [{**c, "vendor": c.get("vendor") or model_vendor(tested["provider"], c["model_id"], "")}
                             for c in tested["components"]]
    return out


def tested_models(tested: dict) -> set[tuple[str, str]]:
    """(model_id, vendor) of every model the run tests: the registered model and each component."""
    tested = tested_identity(tested)
    return {(tested["model_id"], tested["vendor"]), *((c["model_id"], c["vendor"]) for c in tested.get("components") or ())}


def is_tested_model(config: JudgeConfig, tested: dict) -> bool:
    return (config.model_id, config.vendor) in tested_models(tested)


def assess(tested: dict, configs: list[JudgeConfig], *, required_judges: int, material_benchmark: bool,
           order_seed: int, run_session_id: str, blinding: dict | None = None) -> dict:
    """Return the JudgeIndependenceRecorded content plus the eligible judge ids.

    ``blinding`` is the measured oracle blinding; without it the record does not claim identity was hidden."""
    tested = tested_identity(tested)
    judges, independent = [], {}
    models = tested_models(tested)
    for config in configs:
        reasons, disclosures = [], []
        same_host = config.host == tested["host"]
        same_vendor = config.vendor in {tested["vendor"], *(c["vendor"] for c in tested.get("components") or ())}
        if (config.model_id, config.vendor) in models:
            reasons.append("JUDGE_IS_TESTED_MODEL")
        if config.session_id == run_session_id:
            reasons.append("JUDGE_SHARES_RUN_SESSION")
        if same_vendor and material_benchmark and not reasons:
            reasons.append(SAME_VENDOR_MATERIAL)
        if not reasons or reasons == [SAME_VENDOR_MATERIAL]:
            disclosures += [code for code, hit in ((SAME_VENDOR, same_vendor), ("SAME_FAMILY_AS_TESTED_MODEL",
                            config.family == tested["family"]), (SAME_HOST, same_host), (SAME_PROVIDER, same_host)) if hit]
        eligible = not reasons
        key = (config.provider, config.model_id)
        if eligible and key in independent:
            disclosures.append("DUPLICATE_MODEL_NOT_INDEPENDENT")
        elif eligible:
            independent[key] = config.judge_id
        judges.append({
            "judge_id": config.judge_id, "provider": config.provider, "model_id": config.model_id,
            "family": config.family, "host": config.host, "vendor": config.vendor, "eligible": eligible,
            "reason_codes": reasons, "disclosures": disclosures,
        })
    count = len(independent)
    complete = count >= required_judges
    return {
        "tested": tested, "judges": judges, "independent_judge_count": count,
        "required_judges": required_judges, "material_benchmark": material_benchmark,
        "coverage": "complete" if complete else "judge_coverage_incomplete",
        "cross_family_judge_present": any(
            j["eligible"] and j["family"] != tested["family"] and j["vendor"] != tested["vendor"]
            and j["vendor"] not in {c["vendor"] for c in tested.get("components") or ()} for j in judges
        ),
        "blinding": blinding or {"provider_identity_hidden": False,
                                 "limitations": ["Blinding was not measured for this record."]},
        "order_seed": order_seed, "execution_credentials_granted": False,
        "judge_session_ids": [c.session_id for c in configs], "run_session_id": run_session_id,
        "_independent_ids": set(independent.values()),
        "_eligible_ids": {j["judge_id"] for j in judges if j["eligible"]},
    }


def measured_blinding(leaks: list[str]) -> dict:
    """The blinding claim from a scan of the oracle actually sent to judges."""
    limitations = list(BLINDING_LIMITATIONS)
    if leaks:
        limitations.insert(0, "Identity tokens survived masking: " + ", ".join(leaks[:20]))
    return {"provider_identity_hidden": not leaks, "limitations": limitations}
