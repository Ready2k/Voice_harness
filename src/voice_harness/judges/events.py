"""Closed ledger-event schemas for judging and human review (spec section 7)."""

from typing import Annotated, Literal

from pydantic import Field

from ..contracts import Closed, Identifier

Verdict = Literal["pass", "fail", "partial", "untestable", "evaluation_error"]
FinalVerdict = Literal[
    "pass", "fail", "partial", "untestable", "evaluation_error", "review_required", "not_applicable"
]
Severity = Literal["critical", "high", "medium", "low"]
Unit = Annotated[float, Field(ge=0, le=1)]
Text = Annotated[str, Field(max_length=4000)]

ONLINE_CATEGORIES = Literal[
    "unsupported_financial_claim",
    "identity_verification_bypass",
    "unauthorised_tool_request",
    "missing_consent_or_confirmation",
    "vulnerable_customer_handling",
    "synthetic_secret_disclosure",
    "uncertainty_or_escalation_failure",
]
BLOCKING_CAPABILITIES = Literal["none", "hold_audio", "deny_tool", "escalate_to_human", "terminate_session"]


class JudgeIdentity(Closed):
    judge_id: Identifier
    backend: Identifier
    provider: Identifier
    model_id: Identifier
    family: Identifier
    prompt_template_id: Identifier
    evaluation_version: Identifier
    session_id: Identifier


class FindingRecord(Closed):
    finding_id: Identifier
    criterion_id: Identifier
    criterion_version: Annotated[int, Field(ge=1)]
    verdict: Verdict
    severity: Severity
    evidence_event_ids: list[Identifier]
    modality: Identifier
    rationale: Text
    confidence_source: Literal["deterministic", "judge_self_reported", "human"]
    confidence: Unit | None
    score: float | None
    uncertainty: Text | None
    abstained: bool
    status: Literal["valid", "invalid"]
    validation_errors: list[Identifier]
    rejected_citation_ids: list[str]
    reason_codes: list[Identifier]
    judge_id: Identifier | None


class RubricEvaluated(Closed):
    evaluation_id: Identifier
    rubric_id: Identifier
    rubric_version: Annotated[int, Field(ge=1)]
    evaluation_version: Identifier
    scenario_id: Identifier | None
    cutoff_event_id: Identifier
    applicable_criteria: list[Identifier]
    not_applicable_criteria: list[Identifier]
    findings: list[FindingRecord]


class TestedModel(Closed):
    __test__ = False
    provider: Identifier
    model_id: Identifier
    family: Identifier


class JudgeEligibility(Closed):
    judge_id: Identifier
    provider: Identifier
    model_id: Identifier
    family: Identifier
    eligible: bool
    reason_codes: list[Identifier]
    disclosures: list[Identifier]


class Blinding(Closed):
    provider_identity_hidden: bool
    limitations: list[Text]


class JudgeIndependenceRecorded(Closed):
    evaluation_id: Identifier
    tested: TestedModel
    judges: list[JudgeEligibility]
    independent_judge_count: Annotated[int, Field(ge=0)]
    required_judges: Annotated[int, Field(ge=0)]
    material_benchmark: bool
    coverage: Literal["complete", "judge_coverage_incomplete"]
    cross_family_judge_present: bool
    blinding: Blinding
    order_seed: int
    execution_credentials_granted: Literal[False]
    judge_session_ids: list[Identifier]
    run_session_id: Identifier


class JudgeOfflineEvaluated(Closed):
    evaluation_id: Identifier
    judge: JudgeIdentity
    status: Literal["ok", "evaluation_error"]
    error_code: Identifier | None
    results: list[FindingRecord]
    abstained_criteria: list[Identifier]
    latency_ms: Annotated[float, Field(ge=0)]


class CalibrationBinding(Closed):
    """Which calibration report/policy (if any) governed a judge's blocking eligibility for this output."""

    judge_id: Identifier
    status: Literal["report_bound", "no_calibration_report"]
    report_id: Identifier | None = None
    policy_id: Identifier | None = None
    policy_version: Annotated[int, Field(ge=1)] | None = None
    policy_hash: Identifier | None = None
    approved: bool = False
    blocking_enabled_criteria: list[Identifier] = Field(default_factory=list)


class JudgeOnlineFinding(Closed):
    finding_id: Identifier
    judge: JudgeIdentity
    category: ONLINE_CATEGORIES
    severity: Severity
    confidence: Unit | None
    confidence_source: Literal["judge_self_reported"]
    evidence_event_ids: list[Identifier]
    recommended_action: Identifier
    blocking_capability: BLOCKING_CAPABILITIES
    blocking_eligible: bool = False  # calibration permits the action; nothing has been done
    blocking_enforced: bool  # true only once a guardrail durably recorded the action (see enforcement_decision_id)
    enforcement_decision_id: Identifier | None = None
    advisory_reason: Identifier | None
    rationale: Text
    status: Literal["valid", "invalid"]
    validation_errors: list[Identifier]
    rejected_citation_ids: list[str]
    window_from_event_id: Identifier
    window_to_event_id: Identifier
    calibration: CalibrationBinding | None = None


class JudgeOnlineError(Closed):
    judge: JudgeIdentity
    error_code: Identifier
    window_from_event_id: Identifier
    window_to_event_id: Identifier


class CriterionAdjudication(Closed):
    criterion_id: Identifier
    criterion_version: Annotated[int, Field(ge=1)]
    track: Literal["model_behaviour", "platform_enforcement"]
    mandatory_for_assurance: bool
    applicable: bool
    authority: Literal["deterministic", "judge"]
    severity: Severity
    verdict: FinalVerdict
    verdict_source: Identifier
    judge_verdicts: dict[str, str]
    disagreement: bool
    uncertainty: Text | None
    reason_codes: list[Identifier]
    score: float | None
    evidence_event_ids: list[Identifier]
    needs_review_reasons: list[Identifier]


class JudgeAdjudicated(Closed):
    evaluation_id: Identifier
    rubric_id: Identifier
    rubric_version: Annotated[int, Field(ge=1)]
    evaluation_version: Identifier
    criteria: list[CriterionAdjudication]
    model_behaviour: Identifier
    platform_enforcement: Identifier
    overall_assurance: Identifier
    reason_codes: list[Identifier]
    judge_coverage: Literal["complete", "judge_coverage_incomplete"]
    review_required_criteria: list[Identifier]
    rubric_event_id: Identifier
    independence_event_id: Identifier
    offline_event_ids: list[Identifier]
    judge_calibration: list[CalibrationBinding] = Field(default_factory=list)


class ReviewOpened(Closed):
    review_id: Identifier
    evaluation_id: Identifier
    criterion_id: Identifier
    reason: Identifier
    severity: Severity
    adjudication_event_id: Identifier
    state: Literal["open"]


class ReviewClaimed(Closed):
    review_id: Identifier
    reviewer_id: Identifier
    from_state: Literal["open"]
    to_state: Literal["in_review"]


class CheckCorrection(Closed):
    correction_id: Identifier
    corrected_check_id: Identifier
    corrected_check_version: Identifier
    rationale: Text
    evidence_event_ids: list[Identifier]


class ReviewDecided(Closed):
    review_id: Identifier
    criterion_id: Identifier
    reviewer_id: Identifier
    role: Literal["operator", "reviewer"]
    disposition: Literal["confirm_fail", "confirm_pass", "confirm_untestable", "overturn_fail"]
    rationale: Text
    evidence_event_ids: list[Identifier]
    check_correction: CheckCorrection | None
    effect: Literal["applied", "not_applied_deterministic_failure", "not_applied_no_supporting_evidence"]
    effective_verdict: FinalVerdict
    prior_verdict: FinalVerdict
    from_state: Literal["in_review"]
    to_state: Literal["decided"]
    evidence_class: Literal["human_adjudication"]
    independent_model_validation: Literal[False]


JUDGE_EVENT_CONTRACTS = {
    "rubric.evaluated": RubricEvaluated,
    "judge.independence_recorded": JudgeIndependenceRecorded,
    "judge.offline_evaluated": JudgeOfflineEvaluated,
    "judge.online_finding": JudgeOnlineFinding,
    "judge.online_error": JudgeOnlineError,
    "judge.adjudicated": JudgeAdjudicated,
    "review.opened": ReviewOpened,
    "review.claimed": ReviewClaimed,
    "review.decided": ReviewDecided,
}
