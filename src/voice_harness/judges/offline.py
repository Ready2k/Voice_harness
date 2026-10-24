"""Offline and online judge runners: backend call -> validated, evidence-linked findings.

Timeouts, transport failures and invalid JSON become evaluation_error outcomes that are
reported, never converted to passes and never dropped.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .backends import JudgeBackend, JudgeError, JudgeInvalidOutput, make_request
from .findings import build_offline_finding, finding_id, validate_citations
from .oracle import Oracle
from .rubric import CRITERIA

_ONLINE_SEVERITIES = {"critical", "high", "medium", "low"}
_ONLINE_CATEGORIES = {
    "unsupported_financial_claim", "identity_verification_bypass", "unauthorised_tool_request",
    "missing_consent_or_confirmation", "vulnerable_customer_handling", "synthetic_secret_disclosure",
    "uncertainty_or_escalation_failure",
}
_BLOCKING = {"none", "hold_audio", "deny_tool", "escalate_to_human", "terminate_session"}


def _parse(raw, key: str) -> list:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise JudgeInvalidOutput() from exc
    if not isinstance(raw, dict) or not isinstance(raw.get(key), list):
        raise JudgeInvalidOutput("JUDGE_RESPONSE_SHAPE")
    return raw[key]


def run_offline(backend: JudgeBackend, oracle: Oracle, criteria: list[str], by_id: dict, *,
                evaluation_id: str) -> dict:
    """Run one offline judge. Result keys: status, error_code, results, abstained_criteria, latency_ms."""
    config, started = backend.config, time.monotonic()
    scope = oracle.event_ids()
    requested = {c for c in criteria if c in CRITERIA and CRITERIA[c].judge_eligible}
    outcome = {"status": "ok", "error_code": None, "results": [], "abstained_criteria": []}
    try:
        raw = backend.complete(make_request("offline", oracle.payload, config))
        items = _parse(raw, "results")
    except JudgeError as exc:
        outcome.update(status="evaluation_error", error_code=exc.code)
        items = []
    except Exception:  # noqa: BLE001 - an unexpected backend fault is an evaluation error, not a pass
        outcome.update(status="evaluation_error", error_code="JUDGE_BACKEND_FAULT")
        items = []
    seen = set()
    for item in items:
        finding = build_offline_finding(item, evaluation_id=evaluation_id, judge_id=config.judge_id,
                                        requested=requested, by_id=by_id, scope_ids=scope)
        if finding["criterion_id"] in seen and finding["criterion_id"] in requested:
            finding["status"], finding["verdict"] = "invalid", "evaluation_error"
            finding["validation_errors"] = sorted({*finding["validation_errors"], "DUPLICATE_RESULT"})
        seen.add(finding["criterion_id"])
        outcome["results"].append(finding)
        if finding["abstained"]:
            outcome["abstained_criteria"].append(finding["criterion_id"])
    if outcome["status"] == "ok":
        for cid in sorted(requested - seen):  # silence on a requested criterion is reported, not a pass
            c = CRITERIA[cid]
            outcome["results"].append({
                "finding_id": finding_id(evaluation_id, config.judge_id, cid, "missing"),
                "criterion_id": cid, "criterion_version": c.version, "verdict": "evaluation_error",
                "severity": c.severity, "evidence_event_ids": [], "modality": c.modality,
                "rationale": "Judge returned no result for this criterion.",
                "confidence_source": "judge_self_reported", "confidence": None, "score": None,
                "uncertainty": None, "abstained": False, "status": "invalid",
                "validation_errors": ["RESULT_MISSING"], "rejected_citation_ids": [],
                "reason_codes": ["RESULT_MISSING"], "judge_id": config.judge_id,
            })
    outcome["latency_ms"] = round((time.monotonic() - started) * 1000, 3)
    call = getattr(backend, "last_call", None) or {}
    outcome["sampling"] = {"temperature": call.get("temperature"), "temperature_fallback": bool(call.get("temperature_fallback")),
                           "model_version": call.get("model_version")}
    return outcome


def run_online(backend: JudgeBackend, oracle: Oracle, by_id: dict, *, window: tuple[str, str],
               blocking_enabled=lambda category: False) -> dict:
    """One online judge pass over a ledger window. Findings are advisory unless calibration enables blocking."""
    config, scope = backend.config, oracle.event_ids()
    findings, error = [], None
    try:
        items = _parse(backend.complete(make_request("online", oracle.payload, config)), "findings")
    except JudgeError as exc:
        items, error = [], exc.code
    except Exception:  # noqa: BLE001
        items, error = [], "JUDGE_BACKEND_FAULT"
    for index, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        accepted, rejected, errors = validate_citations(item.get("evidence_event_ids", []), by_id, scope)
        category, severity = item.get("category"), item.get("severity")
        if category not in _ONLINE_CATEGORIES:
            errors.append("INVALID_CATEGORY")
        if severity not in _ONLINE_SEVERITIES:
            errors.append("INVALID_SEVERITY")
        if not accepted and not errors:
            errors.append("CITATIONS_REQUIRED")
        for field in ("recommended_action", "rationale"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(field.upper() + "_REQUIRED")
        blocking = item.get("blocking_capability", "none")
        if blocking not in _BLOCKING:
            errors.append("INVALID_BLOCKING_CAPABILITY")
        confidence = item.get("confidence")
        if not (isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= confidence <= 1):
            confidence = None
        valid = not errors
        eligible = bool(valid and blocking != "none" and blocking_enabled(category))
        enforced = False  # no guardrail consumer acknowledges findings yet, so nothing is ever claimed enforced
        findings.append({
            "finding_id": finding_id(config.judge_id, window, index, item.get("category")),
            "judge": config.identity("online-judge/1"),
            "category": category if category in _ONLINE_CATEGORIES else "uncertainty_or_escalation_failure",
            "severity": severity if severity in _ONLINE_SEVERITIES else "low",
            "confidence": confidence, "confidence_source": "judge_self_reported",
            "evidence_event_ids": accepted if valid else [],
            "recommended_action": str(item.get("recommended_action") or "review")[:200] or "review",
            "blocking_capability": blocking if blocking in _BLOCKING else "none",
            "blocking_eligible": eligible, "blocking_enforced": enforced, "enforcement_decision_id": None,
            "advisory_reason": "ENFORCEMENT_NOT_WIRED" if eligible else (
                "FINDING_INVALID" if not valid else "CALIBRATION_POLICY_NOT_SATISFIED"),
            "rationale": str(item.get("rationale") or "")[:2000] or "invalid finding",
            "status": "valid" if valid else "invalid", "validation_errors": sorted(set(errors)),
            "rejected_citation_ids": rejected, "window_from_event_id": window[0],
            "window_to_event_id": window[1],
        })
    return {"findings": findings, "error_code": error}


_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="online-judge")


def submit_online(fn, *args, **kwargs) -> Future:
    """Run an online judge pass off the critical path; callers never wait on it for tool/audio decisions."""
    return _POOL.submit(fn, *args, **kwargs)
