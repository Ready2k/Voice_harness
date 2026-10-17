"""Evidence-manifest judging section (spec sections 7 and 10).

The section is *derived* from the ledger: judge, rubric and review events -> evaluations,
online findings, human adjudications, effective verdict, calibration bindings. Derivation is
deterministic, so ``verify_judging_section`` recomputes it from a bundle's events and detects a
forged or stripped section even when the manifest has been re-hashed. The manifest carries a
compact entry (hash-bound, so covered by the manifest signature) and the full human adjudication list.

Precedence stays visible: criteria whose verdict is a deterministic failure are listed with the
judge verdicts that could not overturn them and the effect of any human decision on them.
"""

from __future__ import annotations

import hashlib

from ..canonical import canonical_json
from . import review

SECTION_VERSION = "1"
PRECEDENCE_RULE = (
    "deterministic failures are not overturned by a judge pass or by a human decision without a "
    "check correction citing ledger evidence; AI findings are advisory unless a calibration policy enables blocking"
)
_EVALUATION_KINDS = {"rubric.evaluated", "judge.independence_recorded", "judge.offline_evaluated", "judge.adjudicated"}
_JUDGING_KINDS = _EVALUATION_KINDS | {
    "judge.online_finding", "judge.online_error", "review.opened", "review.claimed", "review.decided",
}


def section_digest(section) -> str:
    return hashlib.sha256(canonical_json(section)).hexdigest()


def _live(events):
    return [e for e in events if "tombstone" not in e]


def _item(event):
    return {"event_id": event["event_id"], "sequence": event["sequence"], **event["content"]}


def _deterministic_failures(adjudication, decisions):
    out = []
    for entry in adjudication["criteria"]:
        if entry["verdict"] == "fail" and entry["verdict_source"] == "deterministic":
            human = [d for d in decisions if d["criterion_id"] == entry["criterion_id"]]
            out.append({
                "criterion_id": entry["criterion_id"], "verdict": "fail", "verdict_source": "deterministic",
                "judge_verdicts": entry["judge_verdicts"], "evidence_event_ids": entry["evidence_event_ids"],
                "human_decision_effects": [d["effect"] for d in human],
                "overturned": any(d["effect"] == "applied" and d["effective_verdict"] != "fail" for d in human),
            })
    return out


def build_section(run_id: str, events: list[dict]) -> dict:
    live = _live(events)
    judging = [e for e in live if e["type"] in _JUDGING_KINDS]
    evaluations: dict[str, dict] = {}
    for event in judging:
        if event["type"] not in _EVALUATION_KINDS:
            continue
        item = _item(event)
        slot = evaluations.setdefault(item["evaluation_id"], {
            "evaluation_id": item["evaluation_id"], "rubric": None, "independence": None, "offline": [],
            "adjudication": None,
        })
        kind = event["type"]
        if kind == "judge.offline_evaluated":
            slot["offline"].append(item)
        else:
            slot[{"rubric.evaluated": "rubric", "judge.independence_recorded": "independence",
                  "judge.adjudicated": "adjudication"}[kind]] = item
    decisions = [_item(e) for e in judging if e["type"] == "review.decided"]
    effective = review.effective_from_events(live, run_id)
    latest = evaluations.get(effective.get("evaluation_id")) if effective["status"] == "assessed" else None
    precedence = _deterministic_failures(latest["adjudication"], decisions) if latest and latest["adjudication"] else []
    bindings: dict[str, dict] = {}
    for evaluation in evaluations.values():
        for binding in (evaluation["adjudication"] or {}).get("judge_calibration", []):
            bindings[canonical_json(binding).decode()] = binding
    online = [e for e in judging if e["type"] == "judge.online_finding"]
    for event in online:
        if event["content"].get("calibration"):
            bindings[canonical_json(event["content"]["calibration"]).decode()] = event["content"]["calibration"]
    policy_hashes = sorted({b["policy_hash"] for b in bindings.values() if b.get("policy_hash")})
    reviews = review.replay(live)["reviews"]
    return {
        "section_version": SECTION_VERSION, "run_id": run_id,
        "assessed": bool(evaluations),
        "evaluations": [evaluations[k] for k in sorted(evaluations, key=lambda k: _first_sequence(evaluations[k]))],
        "online_findings": [_item(e) for e in online],
        "online_errors": [_item(e) for e in judging if e["type"] == "judge.online_error"],
        "reviews": [
            {"review_id": r["review_id"], "evaluation_id": r["evaluation_id"], "criterion_id": r["criterion_id"],
             "reason": r["reason"], "severity": r["severity"], "state": r["state"], "claimed_by": r["claimed_by"],
             "opened_event_id": r["opened_event_id"], "decided_event_id": r.get("decided_event_id")}
            for r in reviews.values()
        ],
        "human_adjudication": decisions,
        "effective": effective,
        "judge_coverage": effective.get("judge_coverage"),
        "deterministic_precedence": {"rule": PRECEDENCE_RULE, "deterministic_failures": precedence},
        "calibration": {
            "policy_hashes": policy_hashes,
            "bindings": sorted(bindings.values(), key=canonical_json),
            "blocking": "advisory_only_unless_binding_lists_enabled_criteria",
        },
    }


def _first_sequence(evaluation):
    for key in ("rubric", "independence", "adjudication"):
        if evaluation[key]:
            return evaluation[key]["sequence"], evaluation["evaluation_id"]
    return evaluation["offline"][0]["sequence"], evaluation["evaluation_id"]


def manifest_entry(section: dict) -> dict:
    """Compact, hash-bound manifest link; ``section_hash`` is re-verified against the ledger."""
    effective = section["effective"]
    return {
        "section_hash": section_digest(section), "section_version": section["section_version"],
        "assessed": section["assessed"], "evaluation_count": len(section["evaluations"]),
        "online_finding_count": len(section["online_findings"]), "review_count": len(section["reviews"]),
        "human_decision_count": len(section["human_adjudication"]),
        "effective_overall_assurance": effective.get("overall_assurance"),
        "judge_coverage": section["judge_coverage"], "unresolved": effective.get("unresolved"),
        "deterministic_failure_criteria": [f["criterion_id"] for f in section["deterministic_precedence"]["deterministic_failures"]],
        "precedence_rule": PRECEDENCE_RULE,
        "calibration_policy_hashes": section["calibration"]["policy_hashes"],
    }


def verify_judging_section(bundle) -> bool:
    manifest, section = bundle["manifest"], bundle.get("judging")
    entry = manifest.get("judging")
    if entry is None:
        # Legacy bundle: no section, no marker, nothing adjudicated in the manifest.
        return (
            section is None
            and "judging_section" not in (manifest.get("versions") or {})
            and not manifest.get("human_adjudication")
        )
    if section is None or section.get("run_id") != manifest["run_id"]:
        return False
    if entry != manifest_entry(section) or entry["section_hash"] != section_digest(section):
        return False
    if manifest.get("human_adjudication") != section["human_adjudication"]:
        return False
    rebuilt = build_section(manifest["run_id"], bundle["events"])
    return canonical_json(rebuilt) == canonical_json(section)
