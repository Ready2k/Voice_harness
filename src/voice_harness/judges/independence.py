"""Per-run tested-model vs judge independence and eligibility (spec section 7).

Eligibility is evaluated per run: a model is only excluded from judging itself. Two judges
on the same provider/model do not establish independence (they count once).
"""

from __future__ import annotations

from .backends import JudgeConfig

BLINDING_LIMITATIONS = [
    "Provider names are masked in free text only; audio provider fingerprints may prevent complete blinding.",
    "Judges see synthetic transcripts and ledger metadata, not audio.",
]


def assess(tested: dict, configs: list[JudgeConfig], *, required_judges: int, material_benchmark: bool,
           order_seed: int, run_session_id: str) -> dict:
    """Return the JudgeIndependenceRecorded content plus the eligible judge ids."""
    judges, independent = [], {}
    for config in configs:
        reasons, disclosures = [], []
        if config.provider == tested["provider"] and config.model_id == tested["model_id"]:
            reasons.append("JUDGE_IS_TESTED_MODEL")
        if config.session_id == run_session_id:
            reasons.append("JUDGE_SHARES_RUN_SESSION")
        if config.family == tested["family"] and not reasons:
            disclosures.append("SAME_FAMILY_AS_TESTED_MODEL")
        if config.provider == tested["provider"] and not reasons:
            disclosures.append("SAME_PROVIDER_AS_TESTED_MODEL")
        eligible = not reasons
        key = (config.provider, config.model_id)
        if eligible and key in independent:
            disclosures.append("DUPLICATE_MODEL_NOT_INDEPENDENT")
        elif eligible:
            independent[key] = config.judge_id
        judges.append({
            "judge_id": config.judge_id, "provider": config.provider, "model_id": config.model_id,
            "family": config.family, "eligible": eligible, "reason_codes": reasons,
            "disclosures": disclosures,
        })
    count = len(independent)
    complete = count >= required_judges
    return {
        "tested": dict(tested), "judges": judges, "independent_judge_count": count,
        "required_judges": required_judges, "material_benchmark": material_benchmark,
        "coverage": "complete" if complete else "judge_coverage_incomplete",
        "cross_family_judge_present": any(
            j["eligible"] and j["family"] != tested["family"] and j["provider"] != tested["provider"]
            for j in judges
        ),
        "blinding": {"provider_identity_hidden": True, "limitations": list(BLINDING_LIMITATIONS)},
        "order_seed": order_seed, "execution_credentials_granted": False,
        "judge_session_ids": [c.session_id for c in configs], "run_session_id": run_session_id,
        "_independent_ids": set(independent.values()),
        "_eligible_ids": {j["judge_id"] for j in judges if j["eligible"]},
    }
