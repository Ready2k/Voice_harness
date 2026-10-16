"""Runtime-neutral live adapter contract (Phase 2).

Adapters expose the same async operations as ``MockProvider`` and emit a
closed set of normalised events. Unsupported operations must say so via
``capabilities()['interrupt_truncate']`` and raise ``CapabilityUnsupported``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..mock_provider import CapabilityUnsupported

__all__ = [
    "AdapterEvent",
    "AdapterEventAdapter",
    "AudioOut",
    "CapabilityUnsupported",
    "Interrupted",
    "LiveAdapter",
    "ProviderError",
    "ToolCall",
    "TranscriptDelta",
    "TranscriptFinal",
    "TurnEnd",
    "TurnStart",
    "Usage",
]

# Capability values for optional dimensions.
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"


class _Ev(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: str | None = None
    turn_id: str | None = None
    ts_ms: int | None = None


class AudioOut(_Ev):
    type: Literal["audio_out"] = "audio_out"
    item_id: str | None = None
    pcm: bytes  # s16le mono at sample_rate
    sample_rate: int = 24000
    sample_count: int


class TranscriptDelta(_Ev):
    type: Literal["transcript_delta"] = "transcript_delta"
    role: Literal["user", "assistant"]
    item_id: str | None = None
    text: str


class TranscriptFinal(_Ev):
    type: Literal["transcript_final"] = "transcript_final"
    role: Literal["user", "assistant"]
    item_id: str | None = None
    text: str


class ToolCall(_Ev):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: dict[str, Any]


class TurnStart(_Ev):
    type: Literal["turn_start"] = "turn_start"


class TurnEnd(_Ev):
    type: Literal["turn_end"] = "turn_end"


class Interrupted(_Ev):
    type: Literal["interrupted"] = "interrupted"
    item_id: str | None = None
    audio_end_ms: int | None = None


class Usage(_Ev):
    type: Literal["usage"] = "usage"
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ProviderError(_Ev):
    type: Literal["error"] = "error"
    code: str
    message: str = ""
    fatal: bool = False


AdapterEvent = Annotated[
    AudioOut | TranscriptDelta | TranscriptFinal | ToolCall | TurnStart | TurnEnd | Interrupted | Usage | ProviderError,
    Field(discriminator="type"),
]
AdapterEventAdapter: TypeAdapter = TypeAdapter(AdapterEvent)


@runtime_checkable
class LiveAdapter(Protocol):
    """Operations every adapter implements (same shape as MockProvider).

    ``capabilities()`` must include provider, model_id, audio_in, audio_out,
    and ``interrupt_truncate`` ("supported"|"unsupported"); other optional
    dimensions may be "unsupported" and are then excluded from AC-09.
    """

    def capabilities(self) -> dict[str, Any]: ...
    async def open_session(self, config: dict[str, Any]) -> dict[str, Any]: ...
    async def send_audio(self, data: bytes, metadata: dict[str, Any]) -> None: ...
    async def send_text(self, text: str) -> None: ...
    async def submit_tool_result(self, call_id: str, result: dict[str, Any]) -> dict[str, Any]: ...
    async def cancel_tool(self, call_id: str, reason: str) -> dict[str, Any]: ...
    async def interrupt(self, item_id: str | None, audio_end_ms: int) -> dict[str, Any]:
        """Truncate/interrupt; raise CapabilityUnsupported if not supported."""
        ...
    def receive(self) -> AsyncIterator[AdapterEvent]: ...
    async def close(self, reason: str) -> None: ...
