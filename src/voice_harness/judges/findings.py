"""Finding construction and citation validation (spec section 7).

Judge output is untrusted: every cited event id must exist in the run ledger and be
inside the evidence scope the judge was given. Fabricated citations make the finding
invalid (verdict evaluation_error); the rejected ids are kept visibly, never dropped.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .rubric import CRITERIA

JUDGE_VERDICTS = {"pass", "fail", "partial", "untestable"}


def finding_id(*parts: Any) -> str:
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()
    return "fnd_" + digest[:20]


def validate_citations(cited: Any, by_id: dict, scope_ids: set[str] | None = None) -> tuple[list[str], list[str], list[str]]:
    """Return (accepted ids, rejected ids, error codes)."""
    if not isinstance(cited, list) or not all(isinstance(c, str) and c for c in cited):
        return [], [str(c) for c in (cited if isinstance(cited, list) else [cited])], ["CITATIONS_MALFORMED"]
    accepted, rejected, errors = [], [], []
    for event_id in dict.fromkeys(cited):
        if event_id not in by_id:
            rejected.append(event_id)
            errors.append("CITATION_NOT_FOUND")
        elif scope_ids is not None and event_id not in scope_ids:
            rejected.append(event_id)
            errors.append("CITATION_OUT_OF_SCOPE")
        else:
            accepted.append(event_id)
    return accepted, rejected, sorted(set(errors))


def det_finding(criterion_id: str, result: dict, evaluation_id: str) -> dict:
    c = CRITERIA[criterion_id]
    return {
        "finding_id": finding_id(evaluation_id, criterion_id, "deterministic"),
        "criterion_id": c.id, "criterion_version": c.version, "verdict": result["verdict"],
        "severity": c.severity, "evidence_event_ids": result["evidence_event_ids"], "modality": c.modality,
        "rationale": result["rationale"], "confidence_source": "deterministic", "confidence": None,
        "score": None, "uncertainty": None, "abstained": False, "status": "valid",
        "validation_errors": [], "rejected_citation_ids": [], "reason_codes": result["reason_codes"],
        "judge_id": None,
    }


def _error_finding(evaluation_id, judge_id, criterion_id, errors, rejected=(), rationale="", extra=()):
    c = CRITERIA.get(criterion_id)
    return {
        "finding_id": finding_id(evaluation_id, judge_id, criterion_id, "invalid"),
        "criterion_id": criterion_id, "criterion_version": c.version if c else 1,
        "verdict": "evaluation_error", "severity": c.severity if c else "low", "evidence_event_ids": [],
        "modality": c.modality if c else "unknown", "rationale": (rationale or "Finding rejected.")[:2000],
        "confidence_source": "judge_self_reported", "confidence": None, "score": None,
        "uncertainty": None, "abstained": False, "status": "invalid",
        "validation_errors": sorted(set(errors)), "rejected_citation_ids": [str(r) for r in rejected],
        "reason_codes": list(extra) or sorted(set(errors)), "judge_id": judge_id,
    }


def build_offline_finding(raw: Any, *, evaluation_id, judge_id, requested: set[str], by_id, scope_ids) -> dict:
    """Turn one raw judge result into a validated FindingRecord dict."""
    if not isinstance(raw, dict):
        return _error_finding(evaluation_id, judge_id, "UNKNOWN", ["RESULT_NOT_OBJECT"])
    cid = raw.get("criterion_id")
    if cid not in CRITERIA or cid not in requested:
        return _error_finding(evaluation_id, judge_id, str(cid)[:100], ["UNKNOWN_OR_UNREQUESTED_CRITERION"])
    c = CRITERIA[cid]
    verdict = raw.get("verdict")
    rationale = raw.get("rationale") if isinstance(raw.get("rationale"), str) else ""
    errors, reasons = [], []
    abstained = verdict in {"abstain", "abstained"} or raw.get("abstain") is True
    if abstained:
        record = _error_finding(evaluation_id, judge_id, cid, [], rationale=rationale or "Judge abstained.")
        record.update(verdict="untestable", status="valid", abstained=True, reason_codes=["JUDGE_ABSTAINED"],
                      uncertainty=raw.get("uncertainty") if isinstance(raw.get("uncertainty"), str) else None)
        accepted, rejected, cite_errors = validate_citations(raw.get("evidence_event_ids", []), by_id, scope_ids)
        if cite_errors:
            return _error_finding(evaluation_id, judge_id, cid, cite_errors, rejected, rationale)
        record["evidence_event_ids"] = accepted
        return record
    if verdict not in JUDGE_VERDICTS:
        errors.append("INVALID_VERDICT")
    elif verdict == "partial" and not c.allows_partial:
        errors.append("PARTIAL_NOT_ALLOWED_FOR_BINARY_CRITERION")
    accepted, rejected, cite_errors = validate_citations(raw.get("evidence_event_ids", []), by_id, scope_ids)
    errors += cite_errors
    if not errors and verdict != "untestable" and not accepted:
        errors.append("CITATIONS_REQUIRED")
    if not rationale.strip():
        errors.append("RATIONALE_REQUIRED")
    if errors:
        return _error_finding(evaluation_id, judge_id, cid, errors, rejected, rationale)
    confidence = raw.get("confidence")
    if not (isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= confidence <= 1):
        confidence = None
    score = raw.get("score")
    if score is not None:
        anchored = c.scoring.get(verdict) if c.scoring else None
        if anchored is None or score != anchored:
            reasons.append("SCORE_DROPPED_NO_ANCHOR")
        score = None
    return {
        "finding_id": finding_id(evaluation_id, judge_id, cid), "criterion_id": cid,
        "criterion_version": c.version, "verdict": verdict, "severity": c.severity,
        "evidence_event_ids": accepted, "modality": c.modality, "rationale": rationale[:2000],
        "confidence_source": "judge_self_reported", "confidence": confidence, "score": score,
        "uncertainty": raw.get("uncertainty") if isinstance(raw.get("uncertainty"), str) else None,
        "abstained": False, "status": "valid", "validation_errors": [], "rejected_citation_ids": [],
        "reason_codes": reasons, "judge_id": judge_id,
    }
