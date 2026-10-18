"""Human-review workflow: queue state machine, ledger-recorded decisions, effective assurance.

States: open -> in_review (claim) -> decided. The ledger is the record; harness_review_queue
indexes it. A human decision is human evidence (not independent-model validation). Deterministic
protocol failures stay failures unless a check correction citing ledger evidence is supplied; missing
evidence cannot be overridden by assertion.
"""

from __future__ import annotations

import time

from . import adjudicate, store
from .rubric import CRITERIA
from .service import emit

DISPOSITIONS = {
    "confirm_fail": "fail", "confirm_pass": "pass", "confirm_untestable": "untestable", "overturn_fail": "pass",  # nosec B105
}
STATES = ("open", "in_review", "decided")


class ReviewError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def _ledger(tx):
    rows = tx.db.execute("SELECT payload FROM harness_events WHERE run_id=? ORDER BY sequence", (tx.run_id,)).fetchall()
    return [tx.database.decoded(r["payload"]) for r in rows]


def replay(events: list[dict]) -> dict:
    """Rebuild review state and the latest adjudication from ledger events only."""
    reviews, adjudications, latest = {}, {}, None
    for e in events:
        if "tombstone" in e:  # purged payloads carry no content; the surviving ledger is replayed
            continue
        c, kind = e["content"], e["type"]
        if kind == "judge.adjudicated":
            adjudications[c["evaluation_id"]] = e
            latest = c["evaluation_id"]
        elif kind == "review.opened":
            reviews[c["review_id"]] = {**c, "state": "open", "claimed_by": None, "opened_event_id": e["event_id"],
                                       "decision": None}
        elif kind == "review.claimed" and c["review_id"] in reviews:
            reviews[c["review_id"]].update(state="in_review", claimed_by=c["reviewer_id"])
        elif kind == "review.decided" and c["review_id"] in reviews:
            reviews[c["review_id"]].update(state="decided", decision=c, decided_event_id=e["event_id"])
    return {"reviews": reviews, "adjudications": adjudications, "latest": latest}


def _transition(database, review_id):
    store.ensure(database)
    with database.connection() as db:
        row = db.execute("SELECT run_id FROM harness_review_queue WHERE review_id=?", (review_id,)).fetchone()
    if row is None:
        raise ReviewError("REVIEW_NOT_FOUND", 404)
    return row["run_id"]


def claim(database, review_id: str, reviewer_id: str, role: str) -> dict:
    run_id = _transition(database, review_id)
    with database.transaction(run_id) as tx:
        review = replay(_ledger(tx))["reviews"].get(review_id)
        if review is None:
            raise ReviewError("REVIEW_NOT_FOUND", 404)
        if review["state"] != "open":
            raise ReviewError("INVALID_REVIEW_TRANSITION")
        emit(tx, "review.claimed", {"review_id": review_id, "reviewer_id": reviewer_id, "from_state": "open",
                                    "to_state": "in_review"}, [review["opened_event_id"]], source="orchestrator")
        tx.db.execute("UPDATE harness_review_queue SET state=?, claimed_by=?, updated_at=? WHERE review_id=?",
                      ("in_review", reviewer_id, time.time(), review_id))
    return {"review_id": review_id, "state": "in_review", "claimed_by": reviewer_id, "run_id": run_id}


def decide(database, review_id: str, *, reviewer_id: str, role: str, disposition: str, rationale: str,
           evidence_event_ids: list[str], check_correction: dict | None = None) -> dict:
    if disposition not in DISPOSITIONS:
        raise ReviewError("INVALID_DISPOSITION", 422)
    if not rationale.strip():
        raise ReviewError("RATIONALE_REQUIRED", 422)
    run_id = _transition(database, review_id)
    with database.transaction(run_id) as tx:
        state = replay(_ledger(tx))
        review = state["reviews"].get(review_id)
        if review is None:
            raise ReviewError("REVIEW_NOT_FOUND", 404)
        if review["state"] != "in_review":
            raise ReviewError("INVALID_REVIEW_TRANSITION")
        if review["claimed_by"] != reviewer_id:
            raise ReviewError("REVIEW_CLAIMED_BY_OTHER", 403)
        adjudication = state["adjudications"][review["evaluation_id"]]["content"]
        entry = next(c for c in adjudication["criteria"] if c["criterion_id"] == review["criterion_id"])
        prior, desired = entry["verdict"], DISPOSITIONS[disposition]
        det_fail = prior == "fail" and entry["verdict_source"] == "deterministic"
        correction = check_correction
        if correction is not None and correction.get("corrected_check_id") != entry["criterion_id"]:
            correction = None  # a correction must name the check it corrects
        if det_fail and desired != "fail" and not (correction and correction.get("evidence_event_ids")):
            effect, effective = "not_applied_deterministic_failure", prior
        elif desired != prior and desired in {"pass", "fail"} and not evidence_event_ids:
            effect, effective = "not_applied_no_supporting_evidence", prior
        else:
            effect, effective = "applied", desired
        decided = emit(tx, "review.decided", {
            "review_id": review_id, "criterion_id": entry["criterion_id"], "reviewer_id": reviewer_id,
            "role": role, "disposition": disposition, "rationale": rationale[:4000],
            "evidence_event_ids": evidence_event_ids, "check_correction": correction, "effect": effect,
            "effective_verdict": effective, "prior_verdict": prior, "from_state": "in_review",
            "to_state": "decided", "evidence_class": "human_adjudication", "independent_model_validation": False,
        }, [review["opened_event_id"]], source="orchestrator")
        tx.db.execute("UPDATE harness_review_queue SET state=?, decided_event_id=?, updated_at=? WHERE review_id=?",
                      ("decided", decided, time.time(), review_id))
    return {"review_id": review_id, "state": "decided", "effect": effect, "effective_verdict": effective,
            "prior_verdict": prior, "decision_event_id": decided, "run_id": run_id}


def effective_assurance(database, run_id: str) -> dict:
    """Latest adjudication with human decisions applied; derived purely from the ledger."""
    return effective_from_events(database.events(run_id), run_id)


def effective_from_events(events: list[dict], run_id: str) -> dict:
    state = replay(events)
    if state["latest"] is None:
        return {"run_id": run_id, "status": "not_assessed", "overall_assurance": "untestable",
                "reason_codes": ["NOT_ASSESSED"]}
    adjudication = state["adjudications"][state["latest"]]["content"]
    entries = [dict(e) for e in adjudication["criteria"]]
    human, pending_coverage = [], False
    for review in state["reviews"].values():
        if review["evaluation_id"] != state["latest"]:
            continue
        decision = review["decision"]
        if review["reason"] == "JUDGE_COVERAGE_INCOMPLETE" and review["state"] != "decided":
            pending_coverage = True
        if decision:
            human.append({"review_id": review["review_id"], "criterion_id": decision["criterion_id"],
                          "disposition": decision["disposition"], "effect": decision["effect"],
                          "effective_verdict": decision["effective_verdict"], "reviewer_id": decision["reviewer_id"],
                          "evidence_class": "human_adjudication", "decision_event_id": review["decided_event_id"]})
            if decision["effect"] == "applied":
                for e in entries:
                    if e["criterion_id"] == decision["criterion_id"]:
                        e["verdict"], e["verdict_source"] = decision["effective_verdict"], "human_adjudication"
    material = "JUDGE_COVERAGE_INCOMPLETE" in adjudication["reason_codes"]
    verdicts = adjudicate.overall(entries, coverage=adjudication["judge_coverage"], material_benchmark=material,
                                  coverage_review_pending=pending_coverage)
    open_reviews = [r["review_id"] for r in state["reviews"].values()
                    if r["evaluation_id"] == state["latest"] and r["state"] != "decided"]
    return {
        "run_id": run_id, "status": "assessed", "evaluation_id": state["latest"], **verdicts,
        "judge_coverage": adjudication["judge_coverage"], "criteria": {
            e["criterion_id"]: {"verdict": e["verdict"], "source": e["verdict_source"], "applicable": e["applicable"],
                                "mandatory": e["mandatory_for_assurance"], "track": e["track"],
                                "reason_codes": e["reason_codes"]}
            for e in entries
        },
        "judge_calibration": adjudication.get("judge_calibration", []),
        "human_adjudication": human, "open_reviews": open_reviews,
        "unresolved": bool(open_reviews) or verdicts["overall_assurance"] in {"review_required", "untestable"},
    }


def list_queue(database, state: str | None = None, after: str = "", limit: int = 100) -> dict:
    store.ensure(database)
    sql = "SELECT * FROM harness_review_queue WHERE review_id>?"
    params: list = [after]
    if state:
        sql += " AND state=?"
        params.append(state)
    sql += " ORDER BY review_id LIMIT ?"
    params.append(limit + 1)
    with database.connection() as db:
        rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    items = rows[:limit]
    return {"items": items, "next_cursor": items[-1]["review_id"] if len(rows) > limit else None}


def get_review(database, review_id: str) -> dict:
    run_id = _transition(database, review_id)
    review = replay(database.events(run_id))["reviews"][review_id]
    return {**review, "run_id": run_id, "criterion": CRITERIA[review["criterion_id"]].model_dump()}
