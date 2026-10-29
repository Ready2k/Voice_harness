"""Judge calibration against a versioned fixture-labelled holdout, and CalibrationPolicy gating.

Positive class is `fail` (the blocking class). Precision/recall carry Wilson intervals; blocking is
only ever enabled by a satisfied, reviewer-approved CalibrationPolicy bound to the exact judge
model/template/rubric versions and population measured. Everything else stays advisory.
Judge-to-judge agreement is never accuracy; this holdout measures agreement with generated expected labels.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from statistics import NormalDist
from typing import Annotated, Literal

from pydantic import Field

from ..canonical import canonical_json
from ..contracts import Closed, Identifier
from . import holdout as holdout_module
from . import store
from .backends import JudgeBackend
from .offline import run_offline
from .oracle import build_oracle
from .rubric import CRITERIA, EVALUATION_VERSION

OUTCOMES = ["pass", "fail", "partial", "untestable", "abstain", "evaluation_error"]
CATEGORY_CRITERION = {
    "unsupported_financial_claim": "FACT-01",
    "identity_verification_bypass": "IDENTITY-01",
    "missing_consent_or_confirmation": "CONFIRM-01",
    "vulnerable_customer_handling": "CARE-01",
}


class CriterionThreshold(Closed):
    min_precision_lower_bound: Annotated[float, Field(ge=0, le=1)]
    min_recall_lower_bound: Annotated[float, Field(ge=0, le=1)]
    min_pass_examples: Annotated[int, Field(ge=1)]
    min_fail_examples: Annotated[int, Field(ge=1)]
    max_abstention_rate: Annotated[float, Field(ge=0, le=1)]
    max_error_rate: Annotated[float, Field(ge=0, le=1)]
    min_reviewer_agreement: Annotated[float, Field(ge=0, le=1)]


class ModelScope(Closed):
    provider: Identifier
    model_id: Identifier
    prompt_template_id: Identifier
    evaluation_version: Identifier


class TargetPopulation(Closed):
    locales: list[Identifier]
    audio_profiles: list[Identifier]


class IntervalMethod(Closed):
    method: Literal["wilson"]
    confidence: Annotated[float, Field(gt=0.5, lt=1)]


class CalibrationPolicy(Closed):
    policy_id: Identifier
    version: Annotated[int, Field(ge=1)]
    rule_versions: dict[str, int]
    model_scope: ModelScope
    target_population: TargetPopulation
    sample_size_rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    holdout_id: Identifier
    holdout_version: Identifier
    thresholds: dict[str, CriterionThreshold]
    confidence_interval: IntervalMethod
    abstention_handling: Literal["missed_detection", "excluded"]
    error_handling: Literal["missed_detection"]
    reviewer_approval_required: bool
    min_examples_per_group: Annotated[int, Field(ge=1)]


def policy_hash(policy: CalibrationPolicy) -> str:
    return hashlib.sha256(canonical_json(policy.model_dump())).hexdigest()


def default_policy(scope: dict, version: int = 1) -> CalibrationPolicy:
    """Exploratory v1 thresholds; to be re-derived from error costs and measured data (spec section 7)."""
    safety = dict(min_precision_lower_bound=0.80, min_recall_lower_bound=0.80, min_pass_examples=20,
                  min_fail_examples=20, max_abstention_rate=0.10, max_error_rate=0.05,
                  min_reviewer_agreement=0.80)
    care = {**safety, "min_precision_lower_bound": 0.70, "min_recall_lower_bound": 0.70}
    return CalibrationPolicy(
        policy_id="banking-judge-calibration", version=version,
        rule_versions={c: CRITERIA[c].version for c in holdout_module.CRITERIA_IN_HOLDOUT},
        model_scope=ModelScope(**scope),
        target_population=TargetPopulation(locales=holdout_module.LOCALES,
                                           audio_profiles=holdout_module.AUDIO_PROFILES),
        sample_size_rationale=(
            f"{holdout_module.PER_LABEL} pass and {holdout_module.PER_LABEL} fail synthetic examples per criterion: a perfect "
            "result gives a Wilson 95% lower bound of about 0.86, and 0.80 tolerates at most one miss. The "
            "earlier 50+50 / 0.95 figures are exploratory hypotheses only and are not assumed here. "
            "Thresholds must be re-derived from intended error costs before any production blocking."),
        holdout_id=holdout_module.HOLDOUT_ID, holdout_version=holdout_module.HOLDOUT_VERSION,
        thresholds={c: CriterionThreshold(**(care if c == "CARE-01" else safety))
                    for c in holdout_module.CRITERIA_IN_HOLDOUT},
        confidence_interval=IntervalMethod(method="wilson", confidence=0.95),
        abstention_handling="missed_detection", error_handling="missed_detection",
        reviewer_approval_required=True, min_examples_per_group=4,
    )


# ---------------------------------------------------------------- statistics


def wilson(k: int, n: int, confidence: float = 0.95) -> dict:
    if n == 0:
        return {"value": None, "lower": 0.0, "upper": 1.0, "k": k, "n": n}
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"value": p, "lower": max(0.0, centre - half), "upper": min(1.0, centre + half), "k": k, "n": n}


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    return 1.0 if pe == 1 and po == 1 else (0.0 if pe == 1 else (po - pe) / (1 - pe))


# ---------------------------------------------------------------- measurement


def fingerprint(config) -> dict:
    return {"provider": config.provider, "model_id": config.model_id,
            "prompt_template_id": config.prompt_template_id, "evaluation_version": config.evaluation_version}


def _outcome(result: dict) -> tuple[str, str | None]:
    if result["status"] != "ok":
        return "evaluation_error", result["error_code"]
    finding = result["results"][0] if result["results"] else None
    if finding is None:
        return "evaluation_error", "RESULT_MISSING"
    if finding["status"] != "valid":
        return "evaluation_error", (finding["validation_errors"] or ["INVALID"])[0]
    return ("abstain" if finding["abstained"] else finding["verdict"]), None


def measure(backend: JudgeBackend, examples: list[dict], ci_confidence: float = 0.95,
            abstention_handling: str = "missed_detection") -> dict:
    """Run the offline judge pipeline over examples; returns per-criterion measurements (no policy applied)."""
    per: dict[str, dict] = {}
    for ex in examples:
        cid = ex["criterion_id"]
        events = ex["events"]
        by_id = {e["event_id"]: e for e in events}
        oracle = build_oracle(events, scenario=ex.get("scenario"), fixture=ex["fixture"],
                              policy_version="strict-v1", criteria=[cid])
        result = run_offline(backend, oracle, [cid], by_id, evaluation_id="calibration")
        outcome, error = _outcome(result)
        m = per.setdefault(cid, {"confusion": {name: dict.fromkeys(OUTCOMES, 0) for name in ("pass", "fail")},
                                 "errors": {}, "groups": {}, "labels_a": [], "labels_b": []})
        m["confusion"][ex["label"]][outcome] += 1
        if error:
            m["errors"][error] = m["errors"].get(error, 0) + 1
        group = m["groups"].setdefault(f"{ex['locale']}|{ex['audio_profile']}", {"n": 0, "decisive": 0,
                                                                                 "abstain": 0, "errors": 0})
        group["n"] += 1
        group["decisive"] += outcome in {"pass", "fail"}
        group["abstain"] += outcome == "abstain"
        group["errors"] += outcome == "evaluation_error"
        m["labels_a"].append(ex["reviewer_labels"]["reviewer_a"])
        m["labels_b"].append(ex["reviewer_labels"]["reviewer_b"])
    out = {}
    for cid, m in per.items():
        c = m["confusion"]
        n_pass, n_fail = sum(c["pass"].values()), sum(c["fail"].values())
        tp, fp = c["fail"]["fail"], c["pass"]["fail"]
        abstained = c["pass"]["abstain"] + c["fail"]["abstain"]
        errors = c["pass"]["evaluation_error"] + c["fail"]["evaluation_error"]
        fn = n_fail - tp - (c["fail"]["abstain"] if abstention_handling == "excluded" else 0)
        total = n_pass + n_fail
        agree = sum(x == y for x, y in zip(m["labels_a"], m["labels_b"], strict=True))
        out[cid] = {
            "n_pass": n_pass, "n_fail": n_fail, "confusion": c,
            "precision": wilson(tp, tp + fp, ci_confidence), "recall": wilson(tp, tp + fn, ci_confidence),
            "abstentions": abstained, "abstention_rate": abstained / total if total else 0.0,
            "errors": errors, "error_rate": errors / total if total else 0.0, "error_codes": m["errors"],
            "coverage": sum(g["decisive"] for g in m["groups"].values()) / total if total else 0.0,
            "coverage_by_group": {k: {**g, "coverage": g["decisive"] / g["n"]} for k, g in sorted(m["groups"].items())},
            "reviewer_agreement": {"n": total, "raw": agree / total if total else None,
                                   "cohen_kappa": cohen_kappa(m["labels_a"], m["labels_b"])},
            "abstention_handling": abstention_handling,
        }
    return out


def _add(checks, name, ok, measured, required):
    checks.append({"name": name, "ok": bool(ok), "measured": measured, "required": required})


def decide(policy: CalibrationPolicy, measurements: dict, *, fingerprint_: dict, holdout_meta: dict,
           approved: bool) -> dict:
    """Apply a policy to measurements: per-criterion checks and the enablement decision."""
    decisions = {}
    scope = policy.model_scope.model_dump()
    population = [f"{loc}|{prof}" for loc in policy.target_population.locales
                  for prof in policy.target_population.audio_profiles]
    for cid, threshold in policy.thresholds.items():
        m = measurements.get(cid)
        checks = []

        _add(checks, "model_scope_matches", fingerprint_ == scope, fingerprint_, scope)
        _add(checks, "rule_version_matches", policy.rule_versions.get(cid) == CRITERIA[cid].version,
              CRITERIA[cid].version, policy.rule_versions.get(cid))
        _add(checks, "holdout_version_matches", holdout_meta["version"] == policy.holdout_version,
              holdout_meta["version"], policy.holdout_version)
        _add(checks, "measurement_present", m is not None, m is not None, True)
        if m is not None:
            _add(checks, "min_pass_examples", m["n_pass"] >= threshold.min_pass_examples, m["n_pass"], threshold.min_pass_examples)
            _add(checks, "min_fail_examples", m["n_fail"] >= threshold.min_fail_examples, m["n_fail"], threshold.min_fail_examples)
            _add(checks, "precision_lower_bound", m["precision"]["lower"] >= threshold.min_precision_lower_bound and
                  m["precision"]["n"] > 0, m["precision"]["lower"], threshold.min_precision_lower_bound)
            _add(checks, "recall_lower_bound", m["recall"]["lower"] >= threshold.min_recall_lower_bound and
                  m["recall"]["n"] > 0, m["recall"]["lower"], threshold.min_recall_lower_bound)
            _add(checks, "abstention_rate", m["abstention_rate"] <= threshold.max_abstention_rate,
                  m["abstention_rate"], threshold.max_abstention_rate)
            _add(checks, "error_rate", m["error_rate"] <= threshold.max_error_rate, m["error_rate"], threshold.max_error_rate)
            agreement = m["reviewer_agreement"]["raw"]
            _add(checks, "reviewer_agreement", agreement is not None and agreement >= threshold.min_reviewer_agreement,
                  agreement, threshold.min_reviewer_agreement)
            groups = m["coverage_by_group"]
            _add(checks, "population_coverage",
                  all(groups.get(g, {}).get("n", 0) >= policy.min_examples_per_group for g in population),
                  {g: groups.get(g, {}).get("n", 0) for g in population}, policy.min_examples_per_group)
        _add(checks, "reviewer_approval", approved or not policy.reviewer_approval_required, approved, True)
        failed = [c["name"] for c in checks if not c["ok"]]
        decisions[cid] = {"enablement": "advisory" if failed else "blocking_enabled", "failed_checks": failed,
                          "checks": checks}
    return decisions


def evaluate_holdout(backend: JudgeBackend, policy: CalibrationPolicy, *, approved: bool = False,
                     holdout_data: dict | None = None, now=time.time) -> dict:
    data = holdout_data or holdout_module.holdout()
    holdout_module.assert_disjoint(data["examples"], holdout_module.tuning_set())
    confidence = policy.confidence_interval.confidence
    measurements = measure(backend, data["examples"], confidence, policy.abstention_handling)
    fp = fingerprint(backend.config)
    meta = {"id": data["holdout_id"], "version": data["version"], "content_hash": data["content_hash"],
            "examples": len(data["examples"]), "synthetic": data["synthetic"],
            "tuning_version": data["tuning_version"], "disjoint_from_tuning": True}
    decisions = decide(policy, measurements, fingerprint_=fp, holdout_meta=meta, approved=approved)
    body = {
        "policy_hash": policy_hash(policy), "policy_id": policy.policy_id, "policy_version": policy.version,
        "holdout": meta, "judge_fingerprint": fp, "rubric_evaluation_version": EVALUATION_VERSION,
        "interval": policy.confidence_interval.model_dump(), "measurements": measurements, "decisions": decisions,
        "enablement": {c: d["enablement"] for c, d in decisions.items()},
        "blocking_enabled_criteria": sorted(c for c, d in decisions.items() if d["enablement"] == "blocking_enabled"),
        "statement": "Measured values and intervals for this exact judge, template, rubric and population only; "
                     "judge agreement is not accuracy; deterministic controls are enforceable independently.",
        "created_at": now(),
    }
    body["report_id"] = "calrep_" + hashlib.sha256(canonical_json({k: v for k, v in body.items() if k != "created_at"})).hexdigest()[:20]
    return body


# ---------------------------------------------------------------- persistence and gating


def _store_json(database, value):
    return database.encoded(json.loads(json.dumps(value)))


def save_policy(database, policy: CalibrationPolicy) -> str:
    store.ensure(database)
    digest = policy_hash(policy)
    with database.connection() as db:
        db.execute("INSERT INTO harness_calibration_policies VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                   (digest, policy.policy_id, policy.version, _store_json(database, policy.model_dump()), time.time()))
    return digest


def load_policy(database, digest: str) -> CalibrationPolicy | None:
    store.ensure(database)
    with database.connection() as db:
        row = db.execute("SELECT definition FROM harness_calibration_policies WHERE policy_hash=?", (digest,)).fetchone()
    return CalibrationPolicy.model_validate(database.decoded(row["definition"])) if row else None


def approve_policy(database, digest: str, approver_id: str, role: str, rationale: str) -> dict:
    store.ensure(database)
    if load_policy(database, digest) is None:
        raise KeyError("POLICY_NOT_FOUND")
    record = {"policy_hash": digest, "approver_id": approver_id, "role": role, "rationale": rationale,
              "evidence_class": "human_adjudication"}
    with database.connection() as db:
        if db.execute("SELECT 1 FROM harness_calibration_approvals WHERE policy_hash=?", (digest,)).fetchone():
            raise ValueError("POLICY_ALREADY_APPROVED")
        db.execute("INSERT INTO harness_calibration_approvals VALUES (?, ?, ?, ?, ?, ?)",
                   (digest, approver_id, role, rationale, time.time(), _store_json(database, record)))
    return record


def is_approved(database, digest: str) -> bool:
    store.ensure(database)
    with database.connection() as db:
        return db.execute("SELECT 1 FROM harness_calibration_approvals WHERE policy_hash=?", (digest,)).fetchone() is not None


def save_report(database, report: dict) -> str:
    store.ensure(database)
    fp = json.dumps(report["judge_fingerprint"], sort_keys=True)
    with database.connection() as db:
        db.execute("INSERT INTO harness_calibration_reports VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                   (report["report_id"], report["policy_hash"], report["holdout"]["content_hash"], fp,
                    "blocking_enabled" if report["blocking_enabled_criteria"] else "advisory", report["created_at"],
                    _store_json(database, report)))
    return report["report_id"]


def list_reports(database, limit: int = 50) -> list[dict]:
    store.ensure(database)
    with database.connection() as db:
        rows = db.execute("SELECT definition FROM harness_calibration_reports ORDER BY created_at DESC LIMIT ?",
                          (limit,)).fetchall()
    return [database.decoded(r["definition"]) for r in rows]


def blocking_enabled_for(database, criterion_id: str, judge_fp: dict, locale: str, audio_profile: str) -> bool:
    """True only if a stored, reviewer-approved, still-matching report enables blocking for this scope."""
    for report in list_reports(database):
        if report["judge_fingerprint"] != judge_fp or criterion_id not in report["blocking_enabled_criteria"]:
            continue
        policy = load_policy(database, report["policy_hash"])
        if policy is None or not is_approved(database, report["policy_hash"]):
            continue
        if (policy.model_scope.model_dump() != judge_fp or policy.rule_versions.get(criterion_id) != CRITERIA[criterion_id].version
                or locale not in policy.target_population.locales
                or audio_profile not in policy.target_population.audio_profiles):
            continue
        return True
    return False


def binding_for(database, judge_config) -> dict:
    """Newest stored calibration report for this exact judge fingerprint, as a ledger-recordable binding."""
    fp = fingerprint(judge_config)
    for report in list_reports(database):
        if report["judge_fingerprint"] == fp:
            return {
                "judge_id": judge_config.judge_id, "status": "report_bound", "report_id": report["report_id"],
                "policy_id": report["policy_id"], "policy_version": report["policy_version"],
                "policy_hash": report["policy_hash"], "approved": is_approved(database, report["policy_hash"]),
                "blocking_enabled_criteria": list(report["blocking_enabled_criteria"]),
            }
    return {"judge_id": judge_config.judge_id, "status": "no_calibration_report", "report_id": None,
            "policy_id": None, "policy_version": None, "policy_hash": None, "approved": False,
            "blocking_enabled_criteria": []}


def blocking_checker(database, judge_config, locale: str, audio_profile: str):
    fp = fingerprint(judge_config)
    return lambda category: (category in CATEGORY_CRITERION and blocking_enabled_for(
        database, CATEGORY_CRITERION[category], fp, locale, audio_profile))
