"""Read-only judge oracle (spec section 7, ground truth).

The judge never receives a database handle, tool credentials or the live engine: only a
JSON snapshot of ledger events, the fixture snapshot, expected facts/actions, the policy
version and criterion definitions. Data leaving the machine is synthetic fixture data.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass

from ..canonical import canonical_json
from .rubric import CRITERIA, EVALUATION_VERSION, RUBRIC_ID, RUBRIC_VERSION

DATA_CLASSIFICATION = "synthetic_fixture"
# Event content never sent to a judge: raw provider payloads and trace plumbing.
_STRIP_KEYS = {"provider_event", "trace_context"}
PROVIDER_WORDS = ("openai", "anthropic", "amazon", "nova", "gemini", "xai", "grok")


@dataclass(frozen=True)
class Oracle:
    payload: str  # canonical JSON text; immutable by construction
    sha256: str

    def data(self) -> dict:
        return json.loads(self.payload)

    def event_ids(self) -> set[str]:
        return {e["event_id"] for e in self.data()["events"]}


def _blind(value):
    """Best-effort provider-identity blinding of free text; audio fingerprints cannot be hidden."""
    if isinstance(value, str):
        text = value
        for word in PROVIDER_WORDS:
            text = text.replace(word, "[provider]").replace(word.title(), "[provider]")
        return text
    if isinstance(value, list):
        return [_blind(v) for v in value]
    if isinstance(value, dict):
        return {k: _blind(v) for k, v in value.items() if k not in _STRIP_KEYS}
    return value


def slim_event(event: dict) -> dict:
    content = _blind(event["content"])
    envelope = content.get("native_envelope") if isinstance(content, dict) else None
    if isinstance(envelope, dict):  # tool text is evidence data; keep only the structured result
        content = {**content, "native_envelope": {"structuredContent": envelope.get("structuredContent")}}
    return {
        "event_id": event["event_id"], "sequence": event["sequence"], "source": event["source"],
        "type": event["type"], "occurred_at": event.get("occurred_at"), "content": content,
    }


def build_oracle(events: list[dict], *, scenario: dict | None, fixture: dict | None, policy_version: str,
                 criteria: list[str], run_profile: str = "controlled-fixture", order_seed: int = 0) -> Oracle:
    if run_profile != "controlled-fixture":
        raise ValueError("ORACLE_REQUIRES_SYNTHETIC_FIXTURE_DATA")
    expected_actions = [
        {"expected_intent": t["expected_intent"], "allowed_actions": t["allowed_actions"],
         "prohibited_actions": t["prohibited_actions"]}
        for t in (scenario or {}).get("turns", [])
    ]
    ordered = list(criteria)
    random.Random(order_seed).shuffle(ordered)  # nosec B311 - presentation order only
    body = {
        "data_classification": DATA_CLASSIFICATION,
        "rubric": {"id": RUBRIC_ID, "version": RUBRIC_VERSION, "evaluation_version": EVALUATION_VERSION},
        "policy_version": policy_version,
        "fixture": fixture or {},
        "expected": {"actions": expected_actions, "goal": (scenario or {}).get("goal"),
                     "reference_transcript": (scenario or {}).get("reference_transcript")},
        "criteria": [CRITERIA[c].model_dump() for c in ordered],
        "events": [slim_event(e) for e in events],
    }
    text = canonical_json(body).decode()
    return Oracle(text, hashlib.sha256(text.encode()).hexdigest())
