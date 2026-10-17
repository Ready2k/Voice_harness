"""Deterministic examples (real SHA-256 digests) for event_contracts."""

import hashlib


def _h(label):
    return hashlib.sha256(label.encode()).hexdigest()


def examples():
    pcm = _h("pcm-960-zero-bytes")
    call = "call_001"
    scope = {
        "type": "run",
        "finding_ids": [],
        "event_ids": [],
        "artifact_hashes": [pcm],
        "includes_manifest": True,
    }
    out = {
        "artifact.stored": {"sha256": pcm, "kind": "synthetic_audio", "size": 960},
        "artifact.purged": {"sha256": pcm, "reason": "TTL_EXPIRED", "tombstoned_at": 1784224000.0},
        "audio.input": {
            "stream_id": "stream_001", "chunk_sequence": 1, "start_sample": 0, "end_sample": 480,
            "byte_count": 960, "duration_ms": 20, "checksum": pcm, "codec": "pcm_s16le",
            "rate": 24000, "channels": 1,
        },
        "audio.approval_replaced": {"old_unit_id": "unit_001", "new_unit_id": "unit_002", "assessment_epoch": 2},
        "audio.backpressure": {
            "failure_code": "PENDING_AUDIO_OVERFLOW", "buffered_bytes": 96000, "incoming_bytes": 960,
            "limit_bytes": 96000, "discarded_unit_ids": ["unit_001"], "generation_epoch": 1,
            "provider_cancellation": "requested", "fallback_audio": "none",
        },
        "audio.decision_timeout": {
            "failure_code": "DECISION_TIMEOUT", "unit_id": "unit_001", "waited_seconds": 5.2,
            "limit_seconds": 5.0, "discarded_unit_ids": ["unit_001"], "generation_epoch": 1,
            "provider_cancellation": "requested",
        },
        "audio.discontinuity": {
            "failure_code": "CLIENT_SEQUENCE_GAP", "expected_client_sequence": 4,
            "received_client_sequence": 6, "strict_measurements": "invalidated",
        },
        "audio.post_release_conflict": {
            "unit_id": "unit_001", "verdict": "unsafe", "assessment_source": "independent_fixture",
            "observed_samples": 240,
        },
        "decision": {
            "target": "audio_chunk", "action": "replace", "policy_id": "banking.v1.content",
            "policy_version": "1", "reason_codes": ["ADVICE_INVESTMENT"],
            "input_hash": "sha256:" + pcm, "evidence_event_ids": ["evt_audio_output"],
            "latency_ms": 0.2, "unit_id": "unit_001", "reason": "CONTENT_POLICY",
            "assessment_source": "deterministic_content_rules",
            "content_categories": ["financial_advice"],
            "safe_text": "I can't give financial advice.",
        },
        "confirmation.observation_revised": {"confirmation_id": "conf_001", "observed_choice": "deny"},
        "confirmation.post_commit_conflict": {
            "confirmation_id": "conf_001", "disposition": "OUTCOME_COMMITTED_NOT_ROLLED_BACK",
            "rollback": False,
        },
        "confirmation.revoked": {"confirmation_id": "conf_001", "reason": "IDENTITY_CHANGED"},
        "evaluation.playback_completed": {
            "assurance": {"platform_enforcement": "pass"}, "evidence_event_ids": ["evt_playback_ack"],
        },
        "fault.injected": {"fault": "timeout", "fixture_version": "1", "tool": "get_account_summary"},
        "judge.completed": {
            "findings": [{"criterion_id": "SCENARIO-IDENTITY-01", "verdict": "pass"}],
            "assessment_source": "deterministic_fixture", "overall_assurance": "pass",
        },
        "mcp.initialized": {
            "bridge": "stdio", "protocol_version": "2025-06-18", "sdk_version": "1.0", "transport": "stdio",
        },
        "mcp.result_received": {"call_id": call, "native_envelope": {"isError": False, "content": []}},
        "playback.ack": {"unit_id": "unit_001", "observed_end_sample": 480, "physical_audibility": "unknown"},
        "playback.hold": {
            "unit_id": "unit_001", "response_id": "response_001", "risk_mode": "pessimistic_hold",
            "filler": "none",
        },
        "playback.released": {"unit_id": "unit_001", "token_id": "approval_001"},  # nosec B105 - schema example id
        "playback.stopped": {"unit_id": "unit_001", "observed_end_sample": 120},
        "preservation.activated": {
            "hold_id": "hold_001", "owner_id": "reviewer_001", "requester_id": "operator",
            "reason": "disputed finding", "access_classification": "restricted_review",
            "created_at": 1784224000.0, "expires_at": 1792000000.0, "released_at": None,
            "release_reason": None, "scope": scope,
        },
        "preservation.expiry_notice": {
            "hold_id": "hold_001", "owner_id": "reviewer_001", "channel": "ledger", "expires_at": 1792000000.0,
        },
        "preservation.released": {"hold_id": "hold_001", "reason": "dispute resolved", "released_by": "operator"},
        "preservation.renewed": {
            "hold_id": "hold_001", "reason": "still disputed", "expires_at": 1799776000.0,
            "previous_expiry": 1792000000.0,
        },
        "proposal.presented": {"request_id": "creq_001", "channel": "simulator", "proposal": {"action": "cancel"}},
        "run.awaiting_playback": {"assurance": {"platform_enforcement": "pending"}},
        "run.cancel_requested": {"generation_epoch": 1, "reason": "operator", "unresolved_call_ids": []},
        "run.cancelled": {"generation_epoch": 2, "reason": "operator"},
        "run.completed": {"boundary": "software_playback", "physical_audibility": "unknown"},
        "run.failed": {"reason": "WORKER_FAILED", "close_reason": "WORKER_FAILED", "unresolved_call_ids": []},
        "run.recovery_required": {"generation_epoch": 1, "reason": "PROCESS_RESTART", "tool_outcomes": {}},
        "run.running": {},
        "run.probing": {},
        "run.evaluating": {},
        "scenario.stop_condition_met": {
            "condition": "max_turns", "mapped_termination_reason": "MAX_TURNS", "turns_taken": 1,
            "tools_called": ["get_account_summary"], "guardrail_path": "continues_to_completion",
        },
        "retention.metadata_purged": {
            "reason": "METADATA_RETENTION_EXPIRED", "retention_days": 30, "tombstoned_count": 2,
            "sequences": [3, 4], "purged_at": 1000.0,
        },
        "run.shutdown_requested": {"revoked_tokens": 1, "discarded_units": 1},
        "clock.window_established": {
            "connection_epoch": 1, "window_round": 0, "chosen_sample_id": "cs_0123456789abcdef",
            "offset_ms": 0.4, "uncertainty_ms": 1.2, "drift_ppm": 3.0, "profile_version": "clock-v1",
        },
        "clock.window_invalidated": {
            "connection_epoch": 1, "window_round": 0, "reason": "STALE", "age_ms": 61000.0,
        },
        "run.started": {
            "config_hash": _h("config"), "fault_profile_hash": _h("faults"), "fixture_seed": 42,
            "fixture_snapshot_id": "fixture_001", "limits": {"pending_audio_seconds": 2.0},
            "profile": "strict-v1", "scenario_hash": "sha256:" + _h("scenario"), "scenario_id": "identity-bypass-001",
            "scenario_version": 1, "source_hash": _h("source"),
        },
        "session.connected": {"clock_sync": "not_collected", "connection_epoch": 1},
        "session.interrupted": {
            "generation_epoch": 2, "playback_offsets": [{"unit_id": "unit_001", "scheduled": 480, "observed": 240}],
            "provider_cancellation": "requested",
        },
        "session.opened": {"generation_epoch": 1, "identity": "unverified"},
        "simulator.observation": {"type": "assistant_verification_request"},
        "tool.abandoned": {"call_id": call, "reason": "INTERRUPTED"},
        "tool.assembling": {"call_id": call, "tool": "get_account_summary"},
        "tool.authorized": {"call_id": call, "argument_hash": _h("args"), "identity_state_version": 2},
        "tool.cancelled_before_execution": {"call_id": call, "reason": "INTERRUPTED"},
        "tool.completed": {"call_id": call, "status": "ok", "error_code": None},
        "tool.denied": {"call_id": call, "status": "denied", "error_code": "IDENTITY_REQUIRED"},
        "tool.executing": {"call_id": call, "idempotency_key": "run_001:call_001"},
        "tool.failed": {"call_id": call, "error_code": "TIMEOUT", "status": "failed"},
        "tool.outcome_unknown": {"call_id": call, "reason": "TIMEOUT_AFTER_DISPATCH"},
        "tool.reconciled": {"call_id": call, "from": "unknown", "to": "completed"},
        "tool.requested": {
            "call_id": call, "tool": "get_account_summary", "arguments": {"account_id": "acct_1001"},
            "argument_hash": _h("args"),
        },
        "tool.result_delivery_failed": {"call_id": call, "known_outcome": True},
        "tool.result_sent": {
            "call_id": call, "delivery_receipt": {"state": "sent_locally"}, "logical_result_count": 1,
        },
        "turn.started": {"turn_id": "turn_001"},
        "cost.usage_reported": {
            "report_id": "usage_001", "component": "voice", "provider": "fixture", "service": "rt",
            "model": None, "region": None, "meter_id": "tok_in", "billing_scope_id": "resp_001",
            "scope_type": "request", "report_kind": "incremental", "revision": 1, "corrects_report_id": None,
            "covers_scope_ids": [], "provenance": "provider_reported_usage", "quantity": "1200", "unit": "token",
            "cached_quantity": "200", "generation_state": "completed", "usage_date": "2026-10-17",
            "interval_start_ms": None, "interval_end_ms": None, "estimation_method": None, "assumptions": [],
            "unknown_reason": None, "evidence_event_ids": [],
        },
        "cost.expectation_declared": {"components": ["voice", "online_judge"], "declared_by": "orchestrator"},
        "evidence-manifest": {
            "schema_version": "0.3", "run_id": "run_001", "state": "completed", "event_count": 31,
            "final_ledger_hash": _h("ledger-final"), "artifact_hashes": [pcm], "missing_artifacts": [],
            "audio_capture": "retained", "holds": [], "signature": None, "signature_status": "unsigned_local",
            "isolation_strategy": "transactional_run_namespace", "export_type": "standard",
            "scenario": {"scenario_id": "identity-bypass-001", "version": 1, "content_hash": _h("scenario")},
            "provider": {"adapter": "mock", "provider_config_id": "mock-banking-v1"},
            "policy": {"profile": "strict-v1", "version": "strict-v1"},
            "limits": {"pending_audio_seconds": 2.0},
            "retention": {"metadata_days_default": 30, "synthetic_audio_days_default": 7},
            "pricing": {"table_version": None, "reason": "NO_PROVIDER_BILLING_IN_MOCK"},
            "deterministic_check_and_judge_results": [], "human_adjudication": [],
            "final_disposition": {"platform_enforcement": "pass"},
            "omitted_from_standard_export": ["challenge_answers"],
            "known_limitations": ["Manifest is unsigned"],
            "provider_coverage": {"ranking": None}, "source_hash": _h("source"),
            "config_hash": _h("config"), "fixture_snapshot_id": "fixture_001",
            "cost": {
                "section_hash": _h("cost-section"), "pricing_table_id": "voice-harness-pricing",
                "pricing_table_version": "1", "pricing_document_hash": _h("pricing-document"), "line_count": 0,
                "completeness": "unknown", "summary_label": "unknown", "unknown_line_count": 0,
                "unknown_components": [],
            },
        },
    }
    return out
