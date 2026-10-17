"""HTTP surface for assessments, human review and calibration (operator and reviewer roles)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import calibration, review, service, store
from .backends import JudgeError, build_backend
from .rubric import rubric_definition


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestedBody(_Body):
    __test__ = False
    provider: str = Field(min_length=1, max_length=100)
    model_id: str = Field(min_length=1, max_length=200)
    family: str = Field(min_length=1, max_length=100)


class AssessBody(_Body):
    tested: TestedBody
    judges: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    material_benchmark: bool = False
    required_judges: int | None = Field(default=None, ge=1, le=8)
    extra_applicable: list[str] = Field(default_factory=list, max_length=20)
    order_seed: int = 0


class ClaimBody(_Body):
    reviewer_id: str = Field(min_length=1, max_length=100)


class CorrectionBody(_Body):
    correction_id: str = Field(min_length=1, max_length=200)
    corrected_check_id: str = Field(min_length=1, max_length=200)
    corrected_check_version: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=4000)
    evidence_event_ids: list[str] = Field(min_length=1, max_length=100)


class DecisionBody(_Body):
    reviewer_id: str = Field(min_length=1, max_length=100)
    disposition: str
    rationale: str = Field(min_length=1, max_length=4000)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=100)
    check_correction: CorrectionBody | None = None


class ApproveBody(_Body):
    approver_id: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000)


class CalibrateBody(_Body):
    judge: dict[str, Any]
    policy_hash: str | None = None


def _backends(specs):
    try:
        return [build_backend(s) for s in specs]
    except JudgeError as exc:
        raise HTTPException(422, exc.code) from exc


def register_judge_routes(app, database, auth):
    store.ensure(database)

    def operator_only(request: Request):
        if request.state.role != "operator":
            raise HTTPException(403, "FORBIDDEN")

    @app.get("/api/v1/rubric", dependencies=[Depends(auth)])
    def rubric():
        return rubric_definition()

    @app.post("/api/v1/runs/{run_id}/assessments", status_code=201, dependencies=[Depends(auth)])
    def assess(request: Request, run_id: str, body: AssessBody):
        operator_only(request)
        backends = _backends(body.judges)
        try:
            database.get(run_id)
            return service.assess_run(
                database, run_id, tested=body.tested.model_dump(), backends=backends,
                material_benchmark=body.material_benchmark, required_judges=body.required_judges,
                extra_applicable=body.extra_applicable, order_seed=body.order_seed)
        except KeyError as exc:
            raise HTTPException(404, "RUN_NOT_FOUND") from exc
        except ValueError as exc:
            raise HTTPException(409 if str(exc) == "RUN_NOT_FINISHED" else 422, str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/assurance", dependencies=[Depends(auth)])
    def assurance(run_id: str):
        try:
            database.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, "RUN_NOT_FOUND") from exc
        return review.effective_assurance(database, run_id)

    @app.get("/api/v1/reviews", dependencies=[Depends(auth)])
    def reviews(state: str | None = None, cursor: str = "", limit: int = 100):
        if (state is not None and state not in review.STATES) or not 1 <= limit <= 1000:
            raise HTTPException(422, "INVALID_PAGINATION")
        return review.list_queue(database, state, cursor, limit)

    @app.get("/api/v1/reviews/{review_id}", dependencies=[Depends(auth)])
    def get_review(review_id: str):
        try:
            return review.get_review(database, review_id)
        except review.ReviewError as exc:
            raise HTTPException(exc.status, exc.code) from exc

    @app.post("/api/v1/reviews/{review_id}/claim", dependencies=[Depends(auth)])
    def claim(request: Request, review_id: str, body: ClaimBody):
        try:
            return review.claim(database, review_id, body.reviewer_id, request.state.role)
        except review.ReviewError as exc:
            raise HTTPException(exc.status, exc.code) from exc

    @app.post("/api/v1/reviews/{review_id}/decision", dependencies=[Depends(auth)])
    def decision(request: Request, review_id: str, body: DecisionBody):
        try:
            return review.decide(
                database, review_id, reviewer_id=body.reviewer_id, role=request.state.role,
                disposition=body.disposition, rationale=body.rationale, evidence_event_ids=body.evidence_event_ids,
                check_correction=body.check_correction.model_dump() if body.check_correction else None)
        except review.ReviewError as exc:
            raise HTTPException(exc.status, exc.code) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/calibration/policy", dependencies=[Depends(auth)])
    def default_policy():
        policy = calibration.default_policy({
            "provider": "unspecified", "model_id": "unspecified", "prompt_template_id": "offline-judge/1",
            "evaluation_version": "eval-1"})
        return {"policy_hash": calibration.policy_hash(policy), "policy": policy.model_dump()}

    @app.get("/api/v1/calibration/reports", dependencies=[Depends(auth)])
    def reports(limit: int = 50):
        return {"items": calibration.list_reports(database, min(max(limit, 1), 200))}

    @app.post("/api/v1/calibration/evaluate", status_code=201, dependencies=[Depends(auth)])
    def evaluate(request: Request, body: CalibrateBody):
        operator_only(request)
        backend = _backends([body.judge])[0]
        scope = calibration.fingerprint(backend.config)
        policy = calibration.default_policy(scope)
        if body.policy_hash:
            policy = calibration.load_policy(database, body.policy_hash)
            if policy is None:
                raise HTTPException(404, "POLICY_NOT_FOUND")
        digest = calibration.save_policy(database, policy)
        report = calibration.evaluate_holdout(backend, policy, approved=calibration.is_approved(database, digest))
        calibration.save_report(database, report)
        return report

    @app.post("/api/v1/calibration/policies/{policy_hash}/approve", dependencies=[Depends(auth)])
    def approve(request: Request, policy_hash: str, body: ApproveBody):
        try:
            return calibration.approve_policy(database, policy_hash, body.approver_id, request.state.role,
                                              body.rationale)
        except KeyError as exc:
            raise HTTPException(404, "POLICY_NOT_FOUND") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
