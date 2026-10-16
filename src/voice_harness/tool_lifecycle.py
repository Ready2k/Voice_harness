from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolCall:
    call_id: str
    tool_name: str
    state: str = "pending"
    idempotency_key: str | None = None
    cancellation_reason: str | None = None


class ToolLifecycle:
    """Small, deterministic call registry adapted from Voice_S2S ToolCallRegistry."""

    TERMINAL = frozenset(
        {"completed", "denied", "cancelled", "abandoned", "outcome_unknown"}
    )

    def __init__(self):
        self.calls: dict[str, ToolCall] = {}

    def accept(self, call_id: str, tool_name: str, idempotency_key: str) -> bool:
        if not call_id or call_id in self.calls:
            return False
        self.calls[call_id] = ToolCall(
            call_id, tool_name, idempotency_key=idempotency_key
        )
        return True

    def settle(self, call_id: str, state: str, reason: str | None = None) -> bool:
        call = self.calls.get(call_id)
        if call is None or call.state in self.TERMINAL or state not in self.TERMINAL:
            return False
        call.state = state
        call.cancellation_reason = reason
        return True

    def cancel_pending(self, reason: str) -> list[ToolCall]:
        result = []
        for call in self.calls.values():
            if call.state == "pending":
                call.state = "cancelled"
                call.cancellation_reason = reason
                result.append(call)
        return result
