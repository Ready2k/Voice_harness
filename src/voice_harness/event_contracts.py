"""Closed content schemas for the remaining canonical event types (IM-01).

These complement contracts.EVENT_CONTRACTS (which the ledger enforces at write
time). They are enforced by contract tests that replay every scenario and
validate each ledger event, so drift between emitters and schemas fails CI
without making the hot write path depend on this module.
"""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .contracts import Closed, Identifier

Hex64 = Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
Digest = Annotated[str, Field(pattern="^(sha256:)?[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0)]
Timestamp = float
Free = dict[str, Any]


class ArtifactStored(Closed):
    sha256: Hex64
    kind: Identifier
    size: Count


class ArtifactPurged(Closed):
    sha256: Hex64
    reason: Identifier
    tombstoned_at: Timestamp


class AudioInput(Closed):
    stream_id: Identifier
    chunk_sequence: Annotated[int, Field(ge=1)]
    start_sample: Count
    end_sample: Count
    byte_count: Count
    duration_ms: Count
    checksum: Hex64
    codec: Literal["pcm_s16le"]
    rate: Literal[24000]
    channels: Literal[1]


class AudioApprovalReplaced(Closed):
    old_unit_id: Identifier
    new_unit_id: Identifier
    assessment_epoch: Count


class AudioBackpressure(Closed):
    failure_code: Identifier
    buffered_bytes: Count
    incoming_bytes: Count
    limit_bytes: Count
    discarded_unit_ids: list[Identifier]
    generation_epoch: Count
    provider_cancellation: Identifier
    fallback_audio: Identifier


class AudioDecisionTimeout(Closed):
    failure_code: Identifier
    unit_id: Identifier
    waited_seconds: Annotated[float, Field(ge=0)]
    limit_seconds: Annotated[float, Field(ge=0)]
    discarded_unit_ids: list[Identifier]
    generation_epoch: Count
    provider_cancellation: Identifier


class AudioDiscontinuity(Closed):
    failure_code: Identifier
    expected_client_sequence: Count
    received_client_sequence: Count
    strict_measurements: Identifier


class AudioPostReleaseConflict(Closed):
    unit_id: Identifier
    verdict: Identifier
    assessment_source: Identifier
    observed_samples: Count


class DecisionEvent(Closed):
    """Guardrail decision: PolicyDecision fields (minus event id) plus target-specific context."""

    target: Literal["tool", "audio_chunk", "transcript", "session"]
    action: Literal[
        "allow", "deny", "modify", "hold", "release", "escalate", "redact", "replace"
    ]
    policy_id: Identifier
    policy_version: Identifier
    reason_codes: list[Identifier]
    input_hash: Annotated[str, Field(pattern="^sha256:[0-9a-f]{64}$")]
    evidence_event_ids: list[Identifier]
    latency_ms: Annotated[float, Field(ge=0)]
    unit_id: Identifier | None = None
    call_id: Identifier | None = None
    reason: Identifier | None = None
    reason_code: Identifier | None = None
    sequence: Count | None = None  # release token order (PB-17)
    assessment_source: Identifier | None = None
    assessment_epoch: Count | None = None
    generation_epoch: Count | None = None
    content_categories: list[Identifier] | None = None
    safe_text: str | None = None
    argument_hash: Hex64 | None = None
    audio_sha256: Hex64 | None = None
    transcript_sha256: Hex64 | None = None
    token_id: Identifier | None = None
    session_id: Identifier | None = None
    risk_mode: Identifier | None = None
    start_sample: Count | None = None
    end_sample: Count | None = None
    codec: Identifier | None = None
    rate: Count | None = None
    channels: Count | None = None
    expires_at: Timestamp | None = None


class ConfirmationObservationRevised(Closed):
    confirmation_id: Identifier
    observed_choice: Literal["affirm", "deny", "ambiguous"]


class ConfirmationPostCommitConflict(Closed):
    confirmation_id: Identifier
    disposition: Identifier
    rollback: bool


class ConfirmationRevoked(Closed):
    confirmation_id: Identifier
    reason: Identifier


class EvaluationPlaybackCompleted(Closed):
    assurance: Free
    evidence_event_ids: list[Identifier]


class FaultInjected(Closed):
    fault: Identifier
    fixture_version: Identifier
    tool: Identifier


class JudgeCompleted(Closed):
    findings: list[Free]
    assessment_source: Identifier | None = None
    audio_semantics: Identifier | None = None
    model_behaviour: Identifier | None = None
    overall_assurance: Identifier | None = None
    platform_enforcement: Identifier | None = None


class McpInitialized(Closed):
    bridge: Identifier
    protocol_version: Identifier
    sdk_version: Identifier
    transport: Identifier


class McpResultReceived(Closed):
    call_id: Identifier
    native_envelope: Free


class PlaybackAck(Closed):
    unit_id: Identifier
    observed_end_sample: Count
    physical_audibility: Literal["unknown"]


class PlaybackHold(Closed):
    unit_id: Identifier
    response_id: Identifier
    risk_mode: Identifier
    filler: Identifier


class PlaybackReleased(Closed):
    unit_id: Identifier
    token_id: Identifier


class PlaybackStopped(Closed):
    unit_id: Identifier
    observed_end_sample: Count


class PreservationActivated(Closed):
    hold_id: Identifier
    owner_id: Identifier
    requester_id: Identifier
    reason: str
    access_classification: Identifier
    created_at: Timestamp
    expires_at: Timestamp
    released_at: Timestamp | None
    release_reason: str | None
    scope: Free


class PreservationExpiryNotice(Closed):
    hold_id: Identifier
    owner_id: Identifier
    channel: Identifier
    expires_at: Timestamp


class PreservationReleased(Closed):
    hold_id: Identifier
    reason: str
    released_by: Identifier


class PreservationRenewed(Closed):
    hold_id: Identifier
    reason: str
    expires_at: Timestamp
    previous_expiry: Timestamp


class ProposalPresented(Closed):
    request_id: Identifier
    channel: Identifier
    proposal: Free


class RunAwaitingPlayback(Closed):
    assurance: Free


class RunCancelRequested(Closed):
    generation_epoch: Count
    reason: str
    unresolved_call_ids: list[Identifier]


class RunCancelled(Closed):
    generation_epoch: Count
    reason: str


class RunCompleted(Closed):
    assurance: Free | None = None
    boundary: Identifier | None = None
    physical_audibility: Identifier | None = None


class RunFailed(Closed):
    reason: Identifier
    close_reason: Identifier
    unresolved_call_ids: list[Identifier]


class RunRecoveryRequired(Closed):
    generation_epoch: Count
    reason: Identifier
    tool_outcomes: Free


class RunRunning(Closed):
    pass


class RunProbing(Closed):
    pass


class RunEvaluating(Closed):
    pass


class ScenarioStopConditionMet(Closed):
    condition: Identifier
    mapped_termination_reason: Identifier
    turns_taken: Count
    tools_called: list[Identifier]
    guardrail_path: Identifier


class RetentionMetadataPurged(Closed):
    reason: Identifier
    retention_days: Count
    tombstoned_count: Count
    sequences: list[Count]
    purged_at: float


class RunShutdownRequested(Closed):
    revoked_tokens: Count
    discarded_units: Count


class ClockWindowEstablished(Closed):
    connection_epoch: Count
    window_round: Count
    chosen_sample_id: Identifier
    offset_ms: float
    uncertainty_ms: float
    drift_ppm: float
    profile_version: Identifier


class ClockWindowInvalidated(Closed):
    connection_epoch: Count
    window_round: int
    reason: Identifier
    age_ms: float


class RunStarted(Closed):
    config_hash: Hex64
    fault_profile_hash: Hex64
    fixture_seed: int
    fixture_snapshot_id: Identifier
    limits: Free
    profile: Identifier
    scenario_hash: Digest
    scenario_id: Identifier
    scenario_version: Annotated[int, Field(ge=1)]
    source_hash: Hex64


class SessionConnected(Closed):
    clock_sync: Identifier
    connection_epoch: Count


class SessionInterrupted(Closed):
    generation_epoch: Count
    playback_offsets: list[Free]
    provider_cancellation: Identifier
    interruption_receipt: Free | None = None


class SessionOpened(Closed):
    generation_epoch: Count
    identity: Literal["unverified", "verified", "locked"]


class SimulatorObservation(Closed):
    type: Identifier


class ToolAbandoned(Closed):
    call_id: Identifier
    reason: Identifier


class ToolAssembling(Closed):
    call_id: Identifier
    tool: Identifier


class ToolAuthorized(Closed):
    call_id: Identifier
    argument_hash: Hex64
    identity_state_version: Count


class ToolCancelledBeforeExecution(Closed):
    call_id: Identifier
    reason: Identifier


class ToolCompleted(Closed):
    call_id: Identifier
    status: Identifier
    error_code: Identifier | None


class ToolDenied(Closed):
    call_id: Identifier
    status: Identifier
    error_code: Identifier


class ToolExecuting(Closed):
    call_id: Identifier
    idempotency_key: Identifier


class ToolFailed(Closed):
    call_id: Identifier
    error_code: Identifier
    status: Identifier | None = None


class ToolOutcomeUnknown(Closed):
    call_id: Identifier
    reason: Identifier | None = None


class ToolReconciled(Closed):
    call_id: Identifier
    from_: Identifier = Field(alias="from")
    to: Identifier


class ToolRequested(Closed):
    call_id: Identifier
    tool: Identifier
    arguments: Free
    argument_hash: Hex64


class ToolResultDeliveryFailed(Closed):
    call_id: Identifier
    known_outcome: bool


class ToolResultSent(Closed):
    call_id: Identifier
    delivery_receipt: Free
    logical_result_count: Count


class TurnStarted(Closed):
    turn_id: Identifier


class EvidenceManifest(Closed):
    """Run manifest (evidence.bundle). The identity, integrity and artifact fields
    are closed and required; nested policy/provider sections are typed as objects.
    Sections added by later phases (signing, restricted exports) must be added here
    deliberately: unknown top-level keys are rejected."""

    schema_version: Literal["0.3"]
    run_id: Identifier
    state: Identifier
    event_count: Count
    final_ledger_hash: Hex64 | None
    artifact_hashes: list[Hex64]
    missing_artifacts: list[Hex64]
    audio_capture: Literal["retained", "not_captured"]
    holds: list[Free]
    signature: Free | None
    signature_status: Identifier
    isolation_strategy: Identifier
    export_type: Identifier
    scenario: Free
    provider: Free
    policy: Free
    limits: Free | None
    retention: Free
    pricing: Free
    deterministic_check_and_judge_results: list[Free]
    human_adjudication: list[Free]
    final_disposition: Free | None
    omitted_from_standard_export: list[Identifier]
    known_limitations: list[str]
    provider_coverage: Free
    schema_digests: Free | None = None
    versions: Free | None = None
    source_hash: Hex64 | None
    config_hash: Hex64
    fixture_snapshot_id: Identifier | None
    cost: Free | None = None


# --- Phase 3 cost accounting (spec section 11): usage reports and expected components ---
DecimalText = Annotated[str, Field(pattern=r"^\d+(\.\d+)?$")]
UsageDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class CostUsageReported(Closed):
    report_id: Identifier
    component: Identifier
    provider: Identifier
    service: Identifier
    model: Identifier | None
    region: Identifier | None
    meter_id: Identifier
    billing_scope_id: Identifier
    scope_type: Literal["request", "session", "account_period", "unknown"]
    report_kind: Literal["incremental", "cumulative"]
    revision: Annotated[int, Field(ge=1)]
    corrects_report_id: Identifier | None
    covers_scope_ids: list[Identifier]
    provenance: Literal["provider_reported_usage", "estimated"]
    quantity: DecimalText | None
    unit: Literal["token", "second", "request", "character"]
    cached_quantity: DecimalText | None
    generation_state: Literal["completed", "cancelled", "discarded"]
    usage_date: UsageDate
    interval_start_ms: Count | None
    interval_end_ms: Count | None
    estimation_method: Identifier | None
    assumptions: list[str]
    unknown_reason: Identifier | None
    evidence_event_ids: list[Identifier]

    @model_validator(mode="after")
    def _coherent(self):
        if self.provenance == "estimated" and not self.estimation_method:
            raise ValueError("estimated usage requires estimation_method")
        if self.quantity is None and not self.unknown_reason:
            raise ValueError("unknown quantity requires unknown_reason")
        if self.cached_quantity is not None and (
            self.quantity is None or Decimal(self.cached_quantity) > Decimal(self.quantity)
        ):
            raise ValueError("cached usage is a subset of total usage")
        return self


class CostExpectationDeclared(Closed):
    components: Annotated[list[Identifier], Field(min_length=1)]
    declared_by: Identifier


EVENT_CONTENT_CONTRACTS = {
    "cost.usage_reported": CostUsageReported,
    "cost.expectation_declared": CostExpectationDeclared,
    "artifact.stored": ArtifactStored,
    "artifact.purged": ArtifactPurged,
    "audio.input": AudioInput,
    "audio.approval_replaced": AudioApprovalReplaced,
    "audio.backpressure": AudioBackpressure,
    "audio.decision_timeout": AudioDecisionTimeout,
    "audio.discontinuity": AudioDiscontinuity,
    "audio.post_release_conflict": AudioPostReleaseConflict,
    "decision": DecisionEvent,
    "confirmation.observation_revised": ConfirmationObservationRevised,
    "confirmation.post_commit_conflict": ConfirmationPostCommitConflict,
    "confirmation.revoked": ConfirmationRevoked,
    "evaluation.playback_completed": EvaluationPlaybackCompleted,
    "fault.injected": FaultInjected,
    "judge.completed": JudgeCompleted,
    "mcp.initialized": McpInitialized,
    "mcp.result_received": McpResultReceived,
    "playback.ack": PlaybackAck,
    "playback.hold": PlaybackHold,
    "playback.released": PlaybackReleased,
    "playback.stopped": PlaybackStopped,
    "preservation.activated": PreservationActivated,
    "preservation.expiry_notice": PreservationExpiryNotice,
    "preservation.released": PreservationReleased,
    "preservation.renewed": PreservationRenewed,
    "proposal.presented": ProposalPresented,
    "run.awaiting_playback": RunAwaitingPlayback,
    "run.cancel_requested": RunCancelRequested,
    "run.cancelled": RunCancelled,
    "run.completed": RunCompleted,
    "run.failed": RunFailed,
    "run.recovery_required": RunRecoveryRequired,
    "run.running": RunRunning,
    "run.probing": RunProbing,
    "run.evaluating": RunEvaluating,
    "scenario.stop_condition_met": ScenarioStopConditionMet,
    "retention.metadata_purged": RetentionMetadataPurged,
    "run.shutdown_requested": RunShutdownRequested,
    "clock.window_established": ClockWindowEstablished,
    "clock.window_invalidated": ClockWindowInvalidated,
    "run.started": RunStarted,
    "session.connected": SessionConnected,
    "session.interrupted": SessionInterrupted,
    "session.opened": SessionOpened,
    "simulator.observation": SimulatorObservation,
    "tool.abandoned": ToolAbandoned,
    "tool.assembling": ToolAssembling,
    "tool.authorized": ToolAuthorized,
    "tool.cancelled_before_execution": ToolCancelledBeforeExecution,
    "tool.completed": ToolCompleted,
    "tool.denied": ToolDenied,
    "tool.executing": ToolExecuting,
    "tool.failed": ToolFailed,
    "tool.outcome_unknown": ToolOutcomeUnknown,
    "tool.reconciled": ToolReconciled,
    "tool.requested": ToolRequested,
    "tool.result_delivery_failed": ToolResultDeliveryFailed,
    "tool.result_sent": ToolResultSent,
    "turn.started": TurnStarted,
}

# Phase 3 judging and review events (closed schemas live in judges/events.py).
from .judges.events import JUDGE_EVENT_CONTRACTS  # noqa: E402

EVENT_CONTENT_CONTRACTS.update(JUDGE_EVENT_CONTRACTS)
