"""Interpretation of declared scenario stop conditions and turn/duration limits (SC-03).

A stop condition ends the *simulated customer's* script. It never short-circuits
the guardrail/evidence path: a forbidden proposal records failure, then audio
holds, decisions and the manifest still finish.
"""

from __future__ import annotations

import time


class StopEvaluator:
    def __init__(self, definition: dict, monotonic=time.monotonic):
        self.definition = definition
        self.monotonic = monotonic
        self.started = monotonic()
        self.turns_taken = 0
        self.tools_called: list[str] = []
        self.transferred = False
        self.reason: str | None = None
        self.condition: dict | None = None

    @property
    def turn_budget(self) -> int:
        """Scripted turns that may run; the rest are reported as max_turns_reached."""
        return min(len(self.definition["turns"]), self.definition["max_turns"])

    def truncated_by_max_turns(self) -> bool:
        return len(self.definition["turns"]) > self.definition["max_turns"]

    def observe_turn(self):
        self.turns_taken += 1
        return self.check()

    def observe_tool(self, name: str):
        self.tools_called.append(name)
        if name == "transfer_to_human":
            self.transferred = True
        return self.check()

    def check(self) -> str | None:
        """First matching condition wins; once stopped the result is sticky."""
        if self.reason:
            return self.reason
        for condition in self.definition["stop_conditions"]:
            if (
                condition.get("tool_called")
                and condition["tool_called"] in self.tools_called
            ):
                return self._stop(
                    "tool_called:" + condition["tool_called"], condition
                )
            if condition.get("session_transferred") and self.transferred:
                return self._stop("session_transferred", condition)
        if (
            self.truncated_by_max_turns()
            and self.turns_taken >= self.definition["max_turns"]
        ):
            return self._stop("max_turns", None)
        if self.monotonic() - self.started >= self.definition["max_duration_seconds"]:
            return self._stop("max_duration_seconds", None)
        return None

    def _stop(self, key, condition):
        self.reason, self.condition = key, condition
        return key

    def mapped_reason(self) -> str | None:
        if not self.reason:
            return None
        return self.definition["termination_reason_mapping"].get(self.reason)
