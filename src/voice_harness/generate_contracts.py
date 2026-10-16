"""Regenerate checked-in schemas and OpenAPI from Python contracts."""

import base64
import hashlib
import json
import tempfile
from pathlib import Path

from .api import create_app
from .approvals import public_key, sign
from .contracts import (
    EVENT_CONTRACTS,
    OUTPUTS,
    TOOLS,
    ApprovalToken,
    AudioInputPayload,
    HoldRecord,
    HoldRequest,
    PolicyDecision,
    Result,
    RunConfig,
)
from .engine import digest
from .events import SOURCES, Event
from .scenario_defs import SCENARIOS, ScenarioDefinition
from .simulator import ScriptedSimulator
from .storage import Database


def _curated_examples():
    """Semantic examples with real SHA-256 digests (spec section 8)."""
    proposal = {
        "payment_id": "payment_001",
        "action": "cancel",
        "payee_id": "payee_001",
        "amount_minor": 1000,
        "currency": "GBP",
        "account_id": "acct_1001",
    }
    proposal_hash = digest(proposal)
    presented = ["evt_proposal_presented"]
    branches = [
        {
            "id": "ask_for_help",
            "requires": "verification_requested",
            "next_node": "await_help",
            "utterance": "I cannot remember the answer.",
        }
    ]
    decision = ScriptedSimulator(
        42, {"persona": "anxious", "visible_facts": {}, "goal": "balance"}
    ).select(
        ["verification_requested"],
        branches,
        previous_node="await_verification",
        observed_event_ids=["evt_assistant_verify_request"],
    )
    policy_text = "Identity verification is required before account access."
    return {
        "scenario-definition": SCENARIOS["identity-bypass-001"],
        "create_payment.result": {
            "proposal_id": "proposal_001",
            "proposal_hash": digest({"action": "payment", "amount_minor": 1000}),
            "expires_at": 1784224060.0,
            "status": "proposed",
        },
        "get_policy_text.result": {
            "policy_id": "identity",
            "version": "1",
            "text": policy_text,
            "content_hash": hashlib.sha256(policy_text.encode()).hexdigest(),
        },
        "audio.output": {
            "unit_id": "unit_001",
            "state": "pending",
            "audio_sha256": hashlib.sha256(bytes(960)).hexdigest(),
            "response_id": "response_001",
            "transcript_revision_id": "transcript_001",
            "byte_count": 960,
            "start_sample": 0,
            "end_sample": 480,
            "generation_epoch": 1,
            "assessment_epoch": 1,
            "codec": "pcm_s16le",
            "rate": 24000,
            "channels": 1,
            "transcript": "Please verify your identity.",
            "final": True,
            "assessable": True,
            "observed_samples": 0,
            "alignment_source": "whole_response_no_alignment",
            "created_at": 1784224000.0,
            "policy_version": "strict-v1",
        },
        "confirmation.requested": {
            "request_id": "creq_001",
            "proposal_id": "creq_001",
            "proposal_hash": proposal_hash,
            "action": "cancel",
            "account_id": "acct_1001",
            "payee_id": "payee_001",
            "amount_minor": 1000,
            "currency": "GBP",
            "session_id": "ses_001",
            "identity_state_version": 3,
            "presentation_event_ids": presented,
            "expires_at": "2026-10-16T10:16:20Z",
            "policy_version": "strict-v1",
        },
        "customer.confirmation_observed": {
            "observation_id": "obs_001",
            "request_id": "creq_001",
            "channel": "simulator",
            "customer_turn_id": "turn_004",
            "evidence_event_ids": presented,
            "transcript_revision_id": None,
            "observed_choice": "affirm",
            "source_message_id": "msg_001",
        },
        "confirmation.accepted": {
            "confirmation_id": "conf_001",
            "request_id": "creq_001",
            "observation_id": "obs_001",
            "proposal_hash": proposal_hash,
            "session_id": "ses_001",
            "identity_state_version": 3,
            "accepted_at": "2026-10-16T10:15:31Z",
            "expires_at": "2026-10-16T10:16:20Z",
            "authorizer_version": "confirmation-authorizer-v1",
            "evidence_event_ids": ["evt_proposal_presented", "evt_customer_yes"],
        },
        "confirmation.rejected": {
            "request_id": "creq_001",
            "observation_id": "obs_002",
            "reason_code": "OBSERVATION_REPLAYED",
            "evidence_event_ids": ["evt_customer_yes"],
        },
        "confirmation.consumed": {
            "confirmation_id": "conf_001",
            "tool_call_id": "call_001",
            "idempotency_key": "run_001:call_001",
            "committed_outcome_id": "call_001",
        },
        "simulator.branch_selected": decision,
        "simulator.utterance_generated": ScriptedSimulator.utterance(
            decision, branches[0]
        ),
        "preservation-hold": {
            "reason": "disputed high-severity finding",
            "owner_id": "reviewer_001",
            "expires_at": 1792000000.0,
            "finding_ids": ["evt_judge_001:SCENARIO-PROPOSAL-01"],
            "run_wide": False,
            "idempotency_key": "hold-001",
        },
        "preservation-hold-record": {
            "hold_id": "hold_001",
            "run_id": "run_001",
            "reason": "disputed high-severity finding",
            "owner_id": "reviewer_001",
            "requester_id": "operator",
            "created_at": 1784224000.0,
            "expires_at": 1792000000.0,
            "released_at": None,
            "release_reason": None,
            "access_classification": "restricted_review",
            "scope": {
                "type": "findings",
                "finding_ids": ["evt_judge_001:SCENARIO-PROPOSAL-01"],
                "event_ids": ["evt_run_started", "evt_tool_requested"],
                "artifact_hashes": [hashlib.sha256(bytes(960)).hexdigest()],
                "fixture_snapshot_id": "fixture_" + digest({"seed": 42}),
                "scenario_id": "identity-bypass-001",
                "policy_profile": "strict-v1",
                "includes_manifest": True,
            },
            "history": [
                {
                    "action": "activated",
                    "at": 1784224000.0,
                    "by": "operator",
                    "expires_at": 1792000000.0,
                }
            ],
            "audit_event_id": "evt_hold_activated",
            "artifact_availability": {hashlib.sha256(bytes(960)).hexdigest(): True},
            "expiry_notice_recorded": False,
        },
        "policy-decision": {
            "decision_id": "evt_decision_001",
            "target": "tool",
            "action": "deny",
            "policy_id": "banking.v1.tool_authorization",
            "policy_version": "strict-v1",
            "reason_codes": ["IDENTITY_REQUIRED"],
            "input_hash": "sha256:" + digest({"name": "get_account_summary"}),
            "evidence_event_ids": ["evt_tool_requested"],
            "latency_ms": 0.4,
        },
    }


def generate(root="contracts"):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    models = {
        "run-config": RunConfig,
        "tool-result": Result,
        "preservation-hold": HoldRequest,
        "approval-token": ApprovalToken,
        "audio-input": AudioInputPayload,
        "policy-decision": PolicyDecision,
        "scenario-definition": ScenarioDefinition,
        "preservation-hold-record": HoldRecord,
    }
    models.update({name + ".arguments": model for name, model in TOOLS.items()})
    models.update({name + ".result": model for name, model in OUTPUTS.items()})
    models.update(EVENT_CONTRACTS)
    types = {}
    examples = {}
    curated = _curated_examples()
    for name, model in models.items():
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }
        (root / (name + ".schema.json")).write_text(json.dumps(schema, indent=2) + "\n")
        definitions = schema.get("$defs", {})
        types.update(definitions)
        types[model.__name__] = schema
        example = _example(schema, definitions)
        if name == "audio-input":
            example.update(
                bytes=base64.b64encode(bytes(960)).decode(),
                start_sample=0,
                end_sample=480,
            )
        if name == "approval-token":
            example.update(
                audio_sha256=hashlib.sha256(bytes(960)).hexdigest(),
                transcript_sha256=hashlib.sha256(b"fixture").hexdigest(),
                generation_epoch=1,
                assessment_epoch=1,
                start_sample=0,
                end_sample=480,
                expires_at=2000,
            )
            example = sign(example, "public-schema-fixture-only")
            (root / "approval-fixture.json").write_text(
                json.dumps(
                    {
                        "public_key": public_key("public-schema-fixture-only"),
                        "token": example,
                        "audio": base64.b64encode(bytes(960)).decode(),
                    },
                    indent=2,
                )
                + "\n"
            )
        example = curated.get(name, example)
        model.model_validate(example)
        examples[name] = example
    example = Event("example", "orchestrator", "run.started", {})
    example.sequence = 1
    example.seal(None)
    nullable = {
        "session_id",
        "turn_id",
        "response_id",
        "provider_event_type",
        "prev_hash",
    }
    properties = {}
    for key, value in example.to_dict().items():
        if key in nullable:
            kind = ["string", "null"]
        elif isinstance(value, int):
            kind = "integer"
        elif isinstance(value, list):
            kind = "array"
        elif isinstance(value, dict):
            kind = "object"
        else:
            kind = "string"
        properties[key] = {"type": kind}
    properties["schema_version"] = {"const": "0.3"}
    properties["source"] = {"enum": sorted(SOURCES)}
    properties["event_hash"] = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }
    (root / "canonical-event.schema.json").write_text(
        json.dumps(schema, indent=2) + "\n"
    )
    types["CanonicalEvent"] = schema
    examples["canonical-event"] = example = Event(
        "example", "orchestrator", "run.started", {}
    ).to_dict()
    example["sequence"] = 1
    sealed = Event(**example)
    sealed.seal(None)
    examples["canonical-event"] = sealed.to_dict()
    (root / "examples.json").write_text(json.dumps(examples, indent=2) + "\n")
    (root / "generated.ts").write_text(
        "// Generated by voice_harness.generate_contracts; do not edit.\n"
        + "\n".join(
            "export type " + name + " = " + _typescript(definition) + ";"
            for name, definition in types.items()
        )
        + "\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(
            Database(str(Path(directory) / "schemas.sqlite")),
            directory,
            "schema-generation-only",
        )
        (root / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")


def _typescript(schema):
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(item) for item in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(_typescript(item) for item in schema["anyOf"])
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(_typescript({"type": item}) for item in kind)
    if kind == "array":
        return "Array<" + _typescript(schema.get("items", {})) + ">"
    if kind == "object":
        fields = [
            json.dumps(key)
            + ("" if key in schema.get("required", []) else "?")
            + ": "
            + _typescript(value)
            for key, value in schema.get("properties", {}).items()
        ]
        if not fields:
            return "Record<string, unknown>"
        return "{ " + "; ".join(fields) + " }"
    return {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(kind, "unknown")


def _example(schema, definitions):
    if "$ref" in schema:
        return _example(definitions[schema["$ref"].split("/")[-1]], definitions)
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return _example(schema["anyOf"][0], definitions)
    kind = schema.get("type")
    if kind == "object":
        return {
            key: _example(value, definitions)
            for key, value in schema.get("properties", {}).items()
            if key in schema.get("required", [])
        }
    if kind == "array":
        return [
            _example(schema["items"], definitions)
            for _ in range(schema.get("minItems", 0))
        ]
    if kind in {"number", "integer"}:
        return max(1, schema.get("minimum", 1), schema.get("exclusiveMinimum", 0) + 1)
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    if schema.get("pattern") == "^[0-9a-f]{64}$":
        return "0" * 64
    return "fixture"


if __name__ == "__main__":
    generate()
