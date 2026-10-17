"""Closed machine-readable contracts shared by API, MCP and fixtures."""

import base64
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=200)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunConfig(Closed):
    scenario_id: Literal[
        "identity-bypass-001", "balance-verified-001", "unsafe-disclosure-001"
    ] = "identity-bypass-001"
    scenario_version: Literal[1] = 1
    provider_config_id: Literal["mock-banking-v1"] = "mock-banking-v1"
    fixture_seed: int = 42
    policy_profile: Literal["strict-v1", "exploratory-v1"] = "strict-v1"
    input_mode: Literal["fixed_audio"] = "fixed_audio"
    repetitions: Annotated[int, Field(ge=1, le=20)] = 1
    retain_audio: bool = False
    client_mode: Literal["software_fixture", "websocket"] = "software_fixture"


class IdentityArgs(Closed):
    challenge_id: Identifier
    answer: Annotated[str, Field(min_length=1, max_length=200)]


class AccountArgs(Closed):
    account_id: Identifier


class TransactionsArgs(AccountArgs):
    limit: Annotated[int, Field(ge=1, le=100)]
    cursor: str | None = None
    from_date: str | None = Field(default=None, alias="from")
    to: str | None = None


class TransactionArgs(Closed):
    transaction_id: Identifier


class PaymentArgs(AccountArgs):
    payee_id: Identifier
    amount_minor: Annotated[int, Field(gt=0, le=100000)]
    currency: Literal["GBP"]


class CancelArgs(Closed):
    payment_id: Identifier
    confirmation_id: Identifier


class FraudArgs(Closed):
    transaction_ids: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class HandoffArgs(Closed):
    queue: Literal["fraud", "support", "vulnerable_customer"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class PolicyArgs(Closed):
    policy_id: Identifier
    version: Identifier


TOOLS = {
    "verify_identity": IdentityArgs,
    "get_account_summary": AccountArgs,
    "list_recent_transactions": TransactionsArgs,
    "get_transaction_details": TransactionArgs,
    "create_payment": PaymentArgs,
    "cancel_payment": CancelArgs,
    "raise_fraud_case": FraudArgs,
    "transfer_to_human": HandoffArgs,
    "get_policy_text": PolicyArgs,
}


class ToolError(Closed):
    """Spec section 8 error: catalogue code, retryable flag, safe message."""

    code: str
    retryable: bool
    message: str


class Result(Closed):
    call_id: Identifier
    status: Literal["success", "denied", "error", "cancelled", "outcome_unknown"]
    structured_content: dict | None
    error_code: str | None  # internal diagnostic; error.code is the public catalogue code
    evidence_event_ids: list[Identifier]
    error: ToolError | None = None

    @model_validator(mode="after")
    def _catalogue_error(self):
        if self.error_code and self.error is None:
            from .errors import tool_error

            self.error = ToolError(**tool_error(self.error_code))
        return self


class CancelRun(Closed):
    reason: Annotated[str, Field(min_length=1, max_length=500)] = "operator_request"


class HoldRequest(Closed):
    """Preservation hold: exactly one of finding_ids or an explicit run-wide scope."""

    reason: Annotated[str, Field(min_length=1, max_length=500)]
    owner_id: Identifier
    expires_at: float | None = None  # default: 90 days from request
    finding_ids: Annotated[list[Identifier], Field(min_length=1, max_length=100)] | None = None
    run_wide: bool = False
    idempotency_key: Identifier

    @model_validator(mode="after")
    def exactly_one_scope(self):
        if bool(self.finding_ids) == self.run_wide:
            raise ValueError("HOLD_SCOPE_REQUIRED")
        return self


class RenewHold(Closed):
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    expires_at: float


class IdentityResult(Closed):
    identity_state: Literal["verified", "unverified", "locked"]
    attempts_remaining: int


class AccountResult(Closed):
    account_id: Identifier
    balance_minor: int
    available_minor: int
    currency: Literal["GBP"]
    as_of: str


class TransactionResult(Closed):
    transaction_id: Identifier
    account_id: Identifier
    amount_minor: int
    currency: Literal["GBP"]
    status: Literal["booked"]
    booked_at: str
    description: str


class TransactionsResult(Closed):
    transactions: list[TransactionResult]
    next_cursor: str | None


class PaymentResult(Closed):
    proposal_id: Identifier
    proposal_hash: Identifier
    expires_at: float
    status: Literal["proposed"]


class CancellationResult(Closed):
    payment_id: Identifier
    status: Literal["cancelled", "not_cancellable"]


class FraudResult(Closed):
    case_id: Identifier
    status: Literal["open"]


class HandoffResult(Closed):
    handoff_id: Identifier
    status: Literal["queued"]


class PolicyResult(Closed):
    policy_id: Identifier
    version: Identifier
    text: str
    content_hash: Identifier


OUTPUTS = dict(
    zip(
        TOOLS,
        [
            IdentityResult,
            AccountResult,
            TransactionsResult,
            TransactionResult,
            PaymentResult,
            CancellationResult,
            FraudResult,
            HandoffResult,
            PolicyResult,
        ],
        strict=True,
    )
)


class ConfirmationRequested(Closed):
    request_id: Identifier
    proposal_id: Identifier
    proposal_hash: Identifier
    action: Literal["cancel", "payment"]
    account_id: Identifier
    payee_id: str | None
    amount_minor: int
    currency: Literal["GBP"]
    session_id: Identifier
    identity_state_version: int
    presentation_event_ids: list[Identifier]
    expires_at: str
    policy_version: Identifier


class ConfirmationObserved(Closed):
    observation_id: Identifier
    request_id: Identifier
    channel: Literal["voice", "ui", "simulator"]
    customer_turn_id: Identifier
    evidence_event_ids: list[Identifier]
    transcript_revision_id: str | None
    observed_choice: Literal["affirm", "deny", "ambiguous"]
    source_message_id: Identifier


class ConfirmationAccepted(Closed):
    confirmation_id: Identifier
    request_id: Identifier
    observation_id: Identifier
    proposal_hash: Identifier
    session_id: Identifier
    identity_state_version: int
    accepted_at: str
    expires_at: str
    authorizer_version: Identifier
    evidence_event_ids: list[Identifier]


class ConfirmationRejected(Closed):
    request_id: Identifier
    observation_id: str | None
    reason_code: Identifier
    evidence_event_ids: list[Identifier]


class ConfirmationConsumed(Closed):
    confirmation_id: Identifier
    tool_call_id: Identifier
    idempotency_key: Identifier
    committed_outcome_id: Identifier


CONFIRMATION_EVENTS = {
    "confirmation.requested": ConfirmationRequested,
    "customer.confirmation_observed": ConfirmationObserved,
    "confirmation.accepted": ConfirmationAccepted,
    "confirmation.rejected": ConfirmationRejected,
    "confirmation.consumed": ConfirmationConsumed,
}


Sha256Prefixed = Annotated[str, Field(pattern="^sha256:[0-9a-f]{64}$")]


class PolicyDecision(Closed):
    """Spec section 6 decision record; decision_id is the ledger event id."""

    decision_id: Identifier
    target: Literal["tool", "audio_chunk", "transcript", "session"]
    action: Literal["allow", "deny", "modify", "hold", "release", "escalate"]
    policy_id: Identifier
    policy_version: Identifier
    reason_codes: list[Identifier]
    input_hash: Sha256Prefixed
    evidence_event_ids: list[Identifier]
    latency_ms: Annotated[float, Field(ge=0)]


class TranscriptRevised(Closed):
    unit_id: Identifier
    old: str
    new: str
    old_state: Identifier
    source: Literal["independent_fixture", "provider", "independent_asr"]
    old_revision_id: Identifier
    new_revision_id: Identifier
    affected_sample_range: Annotated[list[int], Field(min_length=2, max_length=2)]
    alignment_known: bool
    assessment_epoch: int


class AudioOutputUnit(Closed):
    """Immutable approval unit content (spec section 6)."""

    unit_id: Identifier
    state: Literal["pending"]
    audio_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    response_id: Identifier
    transcript_revision_id: Identifier
    byte_count: int
    start_sample: int
    end_sample: int
    generation_epoch: int
    assessment_epoch: int
    codec: Literal["pcm_s16le"]
    rate: Literal[24000]
    channels: Literal[1]
    transcript: str
    final: bool
    assessable: bool
    observed_samples: int
    alignment_source: Identifier
    created_at: float
    policy_version: Identifier


class HoldScope(Closed):
    type: Literal["run", "findings"]
    finding_ids: list[Identifier]
    event_ids: list[Identifier] | Literal["all_run_events"]
    artifact_hashes: list[Annotated[str, Field(pattern="^[0-9a-f]{64}$")]]
    fixture_snapshot_id: str | None
    scenario_id: str | None
    policy_profile: str | None
    includes_manifest: bool


class HoldHistory(Closed):
    action: Literal["activated", "renewed", "released"]
    at: float
    by: Identifier
    expires_at: float | None = None
    previous_expiry: float | None = None
    reason: str | None = None


class HoldRecord(Closed):
    """Response body for preservation-hold creation, renewal and release."""

    hold_id: Identifier
    run_id: Identifier
    reason: str
    owner_id: Identifier
    requester_id: Identifier
    created_at: float
    expires_at: float
    released_at: float | None
    release_reason: str | None
    access_classification: Literal["restricted_review"]
    scope: HoldScope
    history: list[HoldHistory]
    audit_event_id: Identifier
    artifact_availability: dict[str, bool]
    expiry_notice_recorded: bool = False


class SimulatorBranchSelected(Closed):
    decision_id: Identifier
    previous_node: Identifier
    previous_state_hash: Sha256Prefixed
    branch_policy_id: Identifier
    branch_policy_version: Identifier
    branch_policy_hash: Sha256Prefixed
    observed_event_ids: list[Identifier]
    eligible_branch_ids: list[Identifier]
    predicate_results: dict[str, bool]
    selected_branch_id: Identifier
    selection_method: Literal["scripted", "seeded", "model"]
    selector_model_config_hash: str | None
    seed: int | None
    random_draw: float | None
    next_node: Identifier
    terminate: bool
    fallback_used: bool


class SimulatorUtteranceGenerated(Closed):
    decision_id: Identifier
    output_text: str
    output_text_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    output_audio_sha256: str | None
    audio_capture: Literal["not_generated_fixture", "retained", "disabled"]


SIMULATOR_EVENTS = {
    "simulator.branch_selected": SimulatorBranchSelected,
    "simulator.utterance_generated": SimulatorUtteranceGenerated,
}

EVENT_CONTRACTS = {
    **CONFIRMATION_EVENTS,
    **SIMULATOR_EVENTS,
    "transcript.revised": TranscriptRevised,
    "audio.output": AudioOutputUnit,
}


class ApprovalToken(Closed):
    token_id: Identifier
    unit_id: Identifier
    audio_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    session_id: Identifier
    generation_epoch: int
    assessment_epoch: int
    start_sample: int
    end_sample: int
    expires_at: int
    risk_mode: Literal["pessimistic_hold", "unreviewed_release"]
    codec: Literal["pcm_s16le"]
    rate: Literal[24000]
    channels: Literal[1]
    transcript_sha256: Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
    policy_version: Identifier
    decision_id: Identifier
    signature: Identifier


class AudioInputPayload(Closed):
    bytes: Annotated[str, Field(min_length=1, max_length=4096)]
    stream_id: Identifier
    chunk_sequence: Annotated[int, Field(ge=1)]
    start_sample: Annotated[int, Field(ge=0)]
    end_sample: Annotated[int, Field(gt=0)]
    codec: Literal["pcm_s16le"]
    rate: Literal[24000]
    channels: Literal[1]

    @model_validator(mode="after")
    def reference_frame(self):
        try:
            decoded = base64.b64decode(self.bytes, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("INVALID_AUDIO_FRAME") from None
        if len(decoded) != 960 or self.end_sample - self.start_sample != 480:
            raise ValueError("INVALID_AUDIO_FRAME")
        return self
