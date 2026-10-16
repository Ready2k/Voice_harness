"""Phase 0 capability report schema (spec sections 2-3). Additive; no shared imports."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["verified", "observed", "hypothesis", "unsupported", "unknown"]
ToolMode = Literal["native_mcp", "gateway_bridge", "none", "unknown"]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]

DEFAULT_EXPIRY = timedelta(days=7)

# Spec section 3 "Required capability rows".
REQUIRED_ROWS = (
    "input_codecs_rates",
    "output_codecs_rates",
    "audio_in",
    "audio_out",
    "tool_arguments_and_results",
    "transcript_finality",
    "audio_alignment",
    "turn_signalling",
    "cancel_truncate",
    "usage_granularity",
    "reconnect_resume",
    "direct_mcp",
)
# Absence of any of these fails the report closed.
REQUIRED_PRIMITIVES = ("audio_in", "audio_out", "tool_arguments_and_results", "turn_signalling")


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(Closed):
    """Documentation and runtime evidence are stored separately (spec section 2)."""

    doc_url: str | None = None
    doc_retrieved_at: str | None = None
    probe_event_ids: tuple[Identifier, ...] = ()


class CapabilityRow(Closed):
    name: Identifier
    status: Status = "unknown"
    evidence: Evidence = Evidence()
    limitations: tuple[str, ...] = ()
    tool_mode: ToolMode | None = None  # only meaningful for direct_mcp / tool rows

    @model_validator(mode="after")
    def _verified_needs_both_evidence_kinds(self):
        if self.status == "verified" and not (
            self.evidence.doc_url and self.evidence.probe_event_ids
        ):
            raise ValueError("verified requires documentation and probe evidence")
        return self


class CapabilityReport(Closed):
    schema_version: Literal[1] = 1
    report_id: Identifier
    provider_family: Identifier
    api_surface: Identifier | None = None
    region: Identifier | None = None
    requested_model_id: Identifier
    returned_model_id: Identifier | None = None
    version_unresolved: bool = True  # alias without known resolution
    checked_at: str
    expires_at: str
    adapter_version: str | None = None
    sdk_version: str | None = None
    effective_session_config: dict[str, object] = Field(default_factory=dict)
    operator_terms_attestation: str | None = None  # probes cannot establish terms
    rows: tuple[CapabilityRow, ...]
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _rows_complete(self):
        names = [r.name for r in self.rows]
        if len(set(names)) != len(names):
            raise ValueError("duplicate capability rows")
        missing = set(REQUIRED_ROWS) - set(names)
        if missing:
            raise ValueError(f"missing required rows: {sorted(missing)}")
        if self.returned_model_id is None and not self.version_unresolved:
            raise ValueError("cannot claim resolved version without returned model id")
        return self

    def failed_closed(self) -> list[str]:
        """Required primitives that are not verified/observed."""
        by_name = {r.name: r for r in self.rows}
        return [
            p for p in REQUIRED_PRIMITIVES if by_name[p].status not in ("verified", "observed")
        ]

    def expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= datetime.fromisoformat(self.expires_at)


def unknown_report(
    report_id: str, provider_family: str, requested_model_id: str, now: datetime | None = None
) -> CapabilityReport:
    """Seed report: every row Unknown. Nothing is claimed until a probe fills it in."""
    now = now or datetime.now(UTC)
    return CapabilityReport(
        report_id=report_id,
        provider_family=provider_family,
        requested_model_id=requested_model_id,
        checked_at=now.isoformat(),
        expires_at=(now + DEFAULT_EXPIRY).isoformat(),
        rows=tuple(CapabilityRow(name=n) for n in REQUIRED_ROWS),
        limitations=("seed report: no probe executed",),
    )
