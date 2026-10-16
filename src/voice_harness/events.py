from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .canonical import canonical_json

# Closed set of canonical envelope sources (spec section 5).
SOURCES = frozenset({"provider", "orchestrator", "guardrail", "mcp", "judge", "playback"})

PROCESS_CLOCK_DOMAIN = "process_" + uuid.uuid4().hex


def trace_context():
    try:
        from opentelemetry.trace import get_current_span

        span = get_current_span()
        context = span.get_span_context()
        if context.is_valid:
            return {
                "trace_id": format(context.trace_id, "032x"),
                "span_id": format(context.span_id, "016x"),
                "parent_span_id": format(span.parent.span_id, "016x")
                if getattr(span, "parent", None)
                else None,
                "sampled": bool(context.trace_flags.sampled),
                "unknown_reason": None,
            }
    except ImportError:
        pass
    return {
        "trace_id": None,
        "span_id": None,
        "parent_span_id": None,
        "sampled": False,
        "unknown_reason": "NO_ACTIVE_TRACE",
    }


def _now_ns() -> int:
    return time.monotonic_ns()


@dataclass
class Event:
    run_id: str
    source: str
    type: str
    content: dict[str, Any]
    session_id: str | None = None
    turn_id: str | None = None
    response_id: str | None = None
    causation_event_ids: list[str] = field(default_factory=list)
    provider_event_type: str | None = None
    provider_event: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")
    occurred_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    monotonic_ns: int = field(default_factory=_now_ns)
    schema_version: str = "0.3"
    clock_domain_id: str = field(default_factory=lambda: PROCESS_CLOCK_DOMAIN)
    prev_hash: str | None = None
    event_hash: str | None = None
    producer_id: str = field(default_factory=lambda: PROCESS_CLOCK_DOMAIN)
    producer_sequence: int = 0
    connection_epoch: int = 1
    redaction: dict = field(default_factory=lambda: {"applied": False, "fields": []})
    trace_context: dict = field(default_factory=trace_context)

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError("INVALID_EVENT_SOURCE")

    def seal(self, previous_hash: str | None) -> None:
        self.prev_hash = previous_hash
        self.event_hash = self.digest()

    def digest(self) -> str:
        payload = asdict(self)
        payload["event_hash"] = None
        return hashlib.sha256(canonical_json(payload)).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ledger:
    """Unsampled authoritative event ledger for one run."""

    def __init__(self, run_id: str, path: str | Path | None = None):
        self.run_id = run_id
        self.events: list[Event] = []
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.events = [
                Event(**json.loads(line)) for line in self.path.read_text().splitlines()
            ]
            if not self.verify():
                raise ValueError("LEDGER_INTEGRITY_FAILURE")

    def append(
        self, source: str, event_type: str, content: dict[str, Any], **links: Any
    ) -> Event:
        event = Event(
            self.run_id,
            source,
            event_type,
            copy.deepcopy(content),
            sequence=len(self.events) + 1,
            **links,
        )
        event.seal(self.events[-1].event_hash if self.events else None)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        self.events.append(event)
        return event

    def verify(self) -> bool:
        previous = None
        for index, event in enumerate(self.events, start=1):
            if (
                event.run_id != self.run_id
                or event.sequence != index
                or event.prev_hash != previous
            ):
                return False
            actual = event.event_hash
            if event.digest() != actual:
                return False
            previous = actual
        return True

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "0.3",
            "run_id": self.run_id,
            "events": [e.to_dict() for e in self.events],
        }
