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


class Result(Closed):
    call_id: Identifier
    status: Literal["success", "denied", "error", "cancelled", "outcome_unknown"]
    structured_content: dict | None
    error_code: str | None
    evidence_event_ids: list[Identifier]


class CancelRun(Closed):
    reason: Annotated[str, Field(min_length=1, max_length=500)] = "operator_request"


class HoldRequest(Closed):
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    owner_id: Identifier
    expires_at: float
    run_wide: Literal[True] = True
    idempotency_key: Identifier


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
