"""Run-level assessment: rubric + deterministic checks + judges -> adjudicated, review-linked ledger events.

Judge calls happen outside any database transaction and never receive the engine, database
or tool credentials. All results (including errors, abstentions and invalid findings) are
appended to the run ledger as closed-schema events.
"""

from __future__ import annotations

import time
import uuid

from ..scenario_catalogue import scenario_definition
from . import adjudicate, calibration, independence, rubric, store
from .backends import JudgeBackend
from .events import JUDGE_EVENT_CONTRACTS
from .findings import det_finding
from .offline import run_offline, run_online
from .oracle import build_oracle
from .rubric import EvalContext

JUDGING_PREFIXES = ("judge.", "rubric.", "review.")
UNFINISHED = {"queued", "running", "evaluating"}


def emit(tx, kind: str, content: dict, causes=(), source: str = "judge") -> str:
    JUDGE_EVENT_CONTRACTS[kind].model_validate(content)
    return tx.event(source, kind, content, causes=list(causes))


def _plain(events):
    return [e for e in events if not e["type"].startswith(JUDGING_PREFIXES)]


def scenario_for(state: dict):
    try:
        return scenario_definition(state["config"].get("scenario_id"))
    except KeyError:
        return None


def fixture_for(state: dict) -> dict:
    return {
        "fixture_snapshot_id": state.get("fixture_snapshot_id"), "account_id": state.get("account_id", "acct_1001"),
        "balance_minor": state.get("balance_minor"), "currency": "GBP",
    }


def assess_run(database, run_id: str, *, tested: dict, backends: list[JudgeBackend] | None = None,
               material_benchmark: bool = False, required_judges: int | None = None,
               extra_applicable: list[str] | None = None, order_seed: int = 0,
               while_evaluating: bool = False) -> dict:
    """``while_evaluating`` lets the run's own worker assess it before the terminal state is written."""
    store.ensure(database)
    state = database.get(run_id)
    if state["state"] in (UNFINISHED - {"evaluating"} if while_evaluating else UNFINISHED):
        raise ValueError("RUN_NOT_FINISHED")
    all_events = database.events(run_id)
    events = _plain(all_events)
    scenario = scenario_for(state)
    evaluation_id = "eval_" + uuid.uuid4().hex[:12]
    applicable, not_applicable, unknown = rubric.applicability(scenario, extra_applicable)
    ctx = EvalContext(events, scenario, fixture_for(state))
    det = rubric.evaluate(ctx, applicable)
    required = required_judges if required_judges is not None else (2 if material_benchmark else 1)
    backends = list(backends or [])
    indep = independence.assess(
        tested, [b.config for b in backends], required_judges=required, material_benchmark=material_benchmark,
        order_seed=order_seed, run_session_id=state["session_id"],
    )
    judge_criteria = [c for c in applicable if rubric.CRITERIA[c].judge_eligible]
    by_id = ctx.by_id
    judges = []
    for backend, eligibility in zip(backends, indep["judges"], strict=True):
        if not eligibility["eligible"] or not judge_criteria:
            continue
        oracle = build_oracle(
            events, scenario=scenario, fixture=fixture_for(state),
            policy_version=state.get("policy_version", "strict-v1"), criteria=judge_criteria,
            order_seed=order_seed,
        )
        outcome = run_offline(backend, oracle, judge_criteria, by_id, evaluation_id=evaluation_id)
        judges.append({
            "judge_id": backend.config.judge_id, "provider": backend.config.provider,
            "model_id": backend.config.model_id, "eligible": True, "status": outcome["status"],
            "identity": backend.config.identity(), "outcome": outcome,
            "results": {f["criterion_id"]: f for f in outcome["results"]},
        })
    entries = [adjudicate.adjudicate_criterion(c, det.get(c), judges, required_judges=required) for c in applicable]
    entries += [adjudicate.not_applicable_entry(c) for c in not_applicable]
    verdicts = adjudicate.overall(entries, coverage=indep["coverage"], material_benchmark=material_benchmark)
    items = adjudicate.review_items(entries, coverage=indep["coverage"], material_benchmark=material_benchmark)
    if unknown:
        verdicts["reason_codes"].append("UNKNOWN_CRITERIA_IN_SCENARIO")
    now = time.time()
    judge_calibration = [
        calibration.binding_for(database, b.config)
        for b, e in zip(backends, indep["judges"], strict=True) if e["eligible"]
    ]
    independence_content = {k: v for k, v in indep.items() if not k.startswith("_")}
    with database.transaction(run_id) as tx:
        cutoff = all_events[-1]["event_id"]
        rubric_event = emit(tx, "rubric.evaluated", {
            "evaluation_id": evaluation_id, "rubric_id": rubric.RUBRIC_ID, "rubric_version": rubric.RUBRIC_VERSION,
            "evaluation_version": rubric.EVALUATION_VERSION, "scenario_id": state["config"].get("scenario_id"),
            "cutoff_event_id": cutoff, "applicable_criteria": applicable, "not_applicable_criteria": not_applicable,
            "findings": [det_finding(c, r, evaluation_id) for c, r in det.items() if r is not None],
        }, [cutoff])
        indep_event = emit(tx, "judge.independence_recorded", {"evaluation_id": evaluation_id, **independence_content},
                           [rubric_event])
        offline_events = []
        for judge in judges:
            outcome = judge["outcome"]
            offline_events.append(emit(tx, "judge.offline_evaluated", {
                "evaluation_id": evaluation_id, "judge": judge["identity"], "status": outcome["status"],
                "error_code": outcome["error_code"], "results": outcome["results"],
                "abstained_criteria": outcome["abstained_criteria"], "latency_ms": outcome["latency_ms"],
            }, [indep_event]))
        adjudicated = emit(tx, "judge.adjudicated", {
            "evaluation_id": evaluation_id, "rubric_id": rubric.RUBRIC_ID, "rubric_version": rubric.RUBRIC_VERSION,
            "evaluation_version": rubric.EVALUATION_VERSION, "criteria": entries,
            "model_behaviour": verdicts["model_behaviour"], "platform_enforcement": verdicts["platform_enforcement"],
            "overall_assurance": verdicts["overall_assurance"], "reason_codes": verdicts["reason_codes"],
            "judge_coverage": indep["coverage"],
            "review_required_criteria": [e["criterion_id"] for e in entries if e["verdict"] == "review_required"],
            "rubric_event_id": rubric_event, "independence_event_id": indep_event,
            "offline_event_ids": offline_events, "judge_calibration": judge_calibration,
        }, [rubric_event, indep_event, *offline_events])
        review_ids = []
        for cid, reason, severity in items:
            review_id = "rev_" + uuid.uuid5(uuid.NAMESPACE_URL, evaluation_id + cid).hex[:16]
            opened = emit(tx, "review.opened", {
                "review_id": review_id, "evaluation_id": evaluation_id, "criterion_id": cid, "reason": reason,
                "severity": severity, "adjudication_event_id": adjudicated, "state": "open",
            }, [adjudicated], source="orchestrator")
            tx.db.execute(
                "INSERT INTO harness_review_queue VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (review_id, run_id, evaluation_id, cid, reason, severity, "open", None, opened, None, now, now),
            )
            review_ids.append(review_id)
    return {
        "evaluation_id": evaluation_id, "adjudication_event_id": adjudicated, **verdicts,
        "judge_coverage": indep["coverage"], "independent_judge_count": indep["independent_judge_count"],
        "criteria": {e["criterion_id"]: e["verdict"] for e in entries}, "review_ids": review_ids,
    }


def assess_online(database, run_id: str, backend: JudgeBackend, *, tested: dict | None = None,
                  from_sequence: int = 1, to_sequence: int | None = None, blocking_enabled=lambda category: False) -> dict:
    """Advisory online pass over a ledger window; safe to run from a worker thread (off the critical path)."""
    store.ensure(database)
    state = database.get(run_id)
    window = [e for e in _plain(database.events(run_id))
              if e["sequence"] >= from_sequence and (to_sequence is None or e["sequence"] <= to_sequence)]
    if not window:
        raise ValueError("EMPTY_WINDOW")
    if tested and tested["provider"] == backend.config.provider and tested["model_id"] == backend.config.model_id:
        raise ValueError("JUDGE_IS_TESTED_MODEL")
    ctx = EvalContext(window, scenario_for(state), fixture_for(state))
    oracle = build_oracle(
        window, scenario=ctx.scenario, fixture=ctx.fixture, policy_version=state.get("policy_version", "strict-v1"),
        criteria=[c for c in rubric.CRITERIA if rubric.CRITERIA[c].judge_eligible],
    )
    ids = (window[0]["event_id"], window[-1]["event_id"])
    result = run_online(backend, oracle, ctx.by_id, window=ids, blocking_enabled=blocking_enabled)
    binding = calibration.binding_for(database, backend.config)
    with database.transaction(run_id) as tx:
        if result["error_code"]:
            emit(tx, "judge.online_error", {
                "judge": backend.config.identity("online-judge/1"), "error_code": result["error_code"],
                "window_from_event_id": ids[0], "window_to_event_id": ids[1],
            }, [ids[1]])
        for finding in result["findings"]:
            emit(tx, "judge.online_finding", {**finding, "calibration": binding}, [ids[1]])
    return result
