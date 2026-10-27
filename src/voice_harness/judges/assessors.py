"""Who assessed a run: judge and ASR identity, independence disclosures and calibration, read from the ledger.

Pure functions over a run's events (plus, optionally, the calibration store). Nothing here changes a verdict;
the bake-off carries the result on each observation so a reader can see which models produced the evidence.
Event contracts are closed, so everything is derived from fields already recorded.
"""

from __future__ import annotations

import hashlib

from ..asr import asr_vendor
from . import calibration

ASR_SAME_VENDOR = "ASR_VENDOR_SAME_AS_TESTED_PROVIDER"
NO_JUDGE = "NO_JUDGE_RAN"
NO_CROSS_FAMILY = "NO_CROSS_FAMILY_JUDGE"
COVERAGE_INCOMPLETE = "JUDGE_COVERAGE_INCOMPLETE"


def judge_disclosure(independence: dict | None, judges: list[dict]) -> str | None:
    """Why judge evidence is thin, or None when the required independent, cross-family judges ran.

    A missing disclosure is not a claim that a judge is accurate; it says only that coverage was met."""
    if not judges:
        return NO_JUDGE  # judge-authority criteria such as CARE-01 are untestable
    if not (independence or {}).get("cross_family_judge_present"):
        return NO_CROSS_FAMILY
    if (independence or {}).get("coverage") != "complete":
        return COVERAGE_INCOMPLETE
    return None


def _latest(events, kind):
    found = [e["content"] for e in events if e["type"] == kind]
    return found[-1] if found else None


def calibration_summary(database, identity: dict) -> dict:
    """Newest stored holdout report for this exact judge fingerprint, reduced to the figures a reader needs.

    Holdout agreement is measured on synthetic labelled examples for that judge, template and rubric only;
    it is not accuracy on live runs."""
    fingerprint = {k: identity[k] for k in ("provider", "model_id", "prompt_template_id", "evaluation_version")}
    for report in calibration.list_reports(database):
        if report["judge_fingerprint"] != fingerprint:
            continue
        return {
            "status": "report_bound", "report_id": report["report_id"], "approved": calibration.is_approved(
                database, report["policy_hash"]),
            "holdout": {k: report["holdout"].get(k) for k in ("id", "version", "examples")},
            "criteria": {
                cid: {"precision": m["precision"]["value"], "recall": m["recall"]["value"],
                      "abstention_rate": m["abstention_rate"], "error_rate": m["error_rate"],
                      "reviewer_agreement": m["reviewer_agreement"]["raw"], "n": m["n_pass"] + m["n_fail"]}
                for cid, m in report["measurements"].items()},
        }
    return {"status": "no_calibration_report", "report_id": None, "approved": False, "holdout": None, "criteria": {}}


def assessor_identity(events: list[dict], tested: dict | None = None, database=None) -> dict:
    """Judges and ASR behind one run's assurance verdict.

    ``tested`` is the tested model record (``provider`` is compared with the ASR vendor); ``database`` adds the
    judge calibration summary when given. ``judges`` is empty when no judge ran, in which case judge-authority
    criteria such as CARE-01 are untestable."""
    independence = _latest(events, "judge.independence_recorded")
    seen, judges = set(), []
    for event in events:
        if event["type"] != "judge.offline_evaluated":
            continue
        content = event["content"]
        identity = content["judge"]
        if identity["judge_id"] in seen:
            continue
        seen.add(identity["judge_id"])
        eligibility = next((j for j in (independence or {}).get("judges", []) if j["judge_id"] == identity["judge_id"]), {})
        judges.append({
            "judge_id": identity["judge_id"], "provider": identity["provider"], "model_id": identity["model_id"],
            "family": identity["family"], "host": identity.get("host") or eligibility.get("host"),
            "vendor": identity.get("vendor") or eligibility.get("vendor"), "prompt_template_id": identity["prompt_template_id"],
            "evaluation_version": identity["evaluation_version"], "status": content["status"],
            "error_code": content["error_code"], "disclosures": list(eligibility.get("disclosures", [])),
            "temperature": content.get("temperature"), "temperature_fallback": content.get("temperature_fallback", False),
            "model_version": content.get("model_version"),
            "calibration": calibration_summary(database, identity) if database is not None else None,
        })
    assessments = [e["content"] for e in events if e["type"] == "assessment.independent_asr"]
    pairs = sorted({(a["backend"], a["model"], a["live"]) for a in assessments})
    asr = [{"backend": b, "vendor": asr_vendor(b), "model": m, "live": live,
            "replies": sum(a["backend"] == b and a["model"] == m for a in assessments),
            "disagreements": sum(a["backend"] == b and a["model"] == m and "ASR_DISAGREEMENT" in a.get("reason_codes", [])
                                 for a in assessments)} for b, m, live in pairs]
    disclosures = sorted({ASR_SAME_VENDOR for a in asr if tested and a["vendor"] == tested.get("provider")})
    return {
        "judges": judges, "judge_coverage": (independence or {}).get("coverage"),
        "cross_family_judge_present": bool((independence or {}).get("cross_family_judge_present")),
        "judge_disclosure": judge_disclosure(independence, judges),
        "independent_judge_count": (independence or {}).get("independent_judge_count", 0),
        "required_judges": (independence or {}).get("required_judges"),
        "asr": asr, "asr_disclosures": disclosures,
    }


def summarise_assessors(observations: list[dict]) -> list[dict]:
    """Per provider: distinct judge and ASR models seen across its observations, with disclosures and coverage."""
    out = {}
    for o in observations:
        info = o.get("assessors")
        if not info:
            continue
        row = out.setdefault(o["provider"], {"provider": o["provider"], "runs": 0, "runs_with_judge": 0, "judges": {},
                                             "asr": {}, "disclosures": set(), "coverage": set()})
        row["runs"] += 1
        row["runs_with_judge"] += bool(info["judges"])
        for j in info["judges"]:
            row["judges"][(j["provider"], j["model_id"])] = j
            row["disclosures"].update(j["disclosures"])
        for a in info["asr"]:
            row["asr"][(a["vendor"], a["model"])] = a
        row["disclosures"].update(info["asr_disclosures"])
        if info.get("judge_disclosure"):
            row["disclosures"].add(info["judge_disclosure"])
        if info["judge_coverage"]:
            row["coverage"].add(info["judge_coverage"])
    return [{"provider": r["provider"], "runs": r["runs"], "runs_with_judge": r["runs_with_judge"],
             "judge_models": [{k: j.get(k) for k in ("provider", "model_id", "family", "host", "vendor", "calibration", "temperature",
                                                   "temperature_fallback", "model_version")}
                              for j in r["judges"].values()],
             "asr_models": [{"vendor": v, "model": m, "live": a["live"], "disagreements": a.get("disagreements", 0)} for (v, m), a in r["asr"].items()],
             "disclosures": sorted(r["disclosures"]), "judge_coverage": sorted(r["coverage"])}
            for r in out.values()]


def spot_check_sample(observations: list[dict], per_group: int = 3, seed: str = "") -> list[dict]:
    """Deterministic sample for human review: ``per_group`` completed passes and failures for each provider.

    Ordering is a hash of ``seed`` and the run id, so the same bake-off gives the same sample and a new seed a
    different one. Runs that did not complete have no assurance verdict to check and are left out."""
    groups = {}
    for o in observations:
        if o.get("completed") and o.get("run_id"):
            groups.setdefault((o["provider"], "pass" if o["outcome"] == "pass" else "not_pass"), []).append(o)
    sample = []
    for (provider, verdict), rows in sorted(groups.items()):
        rows.sort(key=lambda o: hashlib.sha256(f"{seed}|{o['run_id']}".encode()).hexdigest())
        sample += [{"provider": provider, "verdict": verdict, "run_id": o["run_id"], "scenario_id": o["scenario_id"],
                    "outcome": o["outcome"]} for o in rows[:per_group]]
    return sample
