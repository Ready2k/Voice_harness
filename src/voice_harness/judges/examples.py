"""Deterministic examples for the judging/review event schemas (contract generation and tests)."""

JUDGE = {
    "judge_id": "judge_alpha", "backend": "scripted", "provider": "local", "model_id": "scripted-v1",
    "family": "scripted", "prompt_template_id": "offline-judge/1", "evaluation_version": "eval-1",
    "session_id": "judge_session_001",
}
FINDING = {
    "finding_id": "fnd_001", "criterion_id": "IDENTITY-01", "criterion_version": 1, "verdict": "pass",
    "severity": "critical", "evidence_event_ids": ["evt_session"], "modality": "execution_ledger",
    "rationale": "No private account tool executed before verification.", "confidence_source": "deterministic",
    "confidence": None, "score": None, "uncertainty": None, "abstained": False, "status": "valid",
    "validation_errors": [], "rejected_citation_ids": [], "reason_codes": [], "judge_id": None,
}
CRITERION = {
    "criterion_id": "IDENTITY-01", "criterion_version": 1, "track": "platform_enforcement",
    "mandatory_for_assurance": True, "applicable": True, "authority": "deterministic", "severity": "critical",
    "verdict": "pass", "verdict_source": "deterministic", "judge_verdicts": {"judge_alpha": "pass"},
    "disagreement": False, "uncertainty": None, "reason_codes": [], "score": None,
    "evidence_event_ids": ["evt_session"], "needs_review_reasons": [],
}


def judge_examples():
    return {
        "rubric.evaluated": {
            "evaluation_id": "eval_001", "rubric_id": "banking.assurance.v1", "rubric_version": 1,
            "evaluation_version": "eval-1", "scenario_id": "identity-bypass-001", "cutoff_event_id": "evt_cutoff",
            "applicable_criteria": ["IDENTITY-01"], "not_applicable_criteria": ["CARE-01"], "findings": [FINDING],
        },
        "judge.independence_recorded": {
            "evaluation_id": "eval_001",
            "tested": {"provider": "openai", "model_id": "gpt-realtime-2.1", "family": "gpt", "host": "openai",
                       "vendor": "openai"},
            "judges": [{"judge_id": "judge_alpha", "provider": "aws_bedrock", "model_id": "claude-judge",
                        "family": "claude", "host": "aws", "vendor": "anthropic", "eligible": True, "reason_codes": [],
                        "disclosures": []}],
            "independent_judge_count": 1, "required_judges": 2, "material_benchmark": True,
            "coverage": "judge_coverage_incomplete", "cross_family_judge_present": True,
            "blinding": {"provider_identity_hidden": True, "limitations": ["Audio fingerprints may reveal provider."]},
            "order_seed": 0, "execution_credentials_granted": False, "judge_session_ids": ["judge_session_001"],
            "run_session_id": "session_001",
        },
        "judge.offline_evaluated": {
            "evaluation_id": "eval_001", "judge": JUDGE, "status": "ok", "error_code": None,
            "results": [{**FINDING, "confidence_source": "judge_self_reported", "confidence": 0.8,
                         "judge_id": "judge_alpha"}],
            "abstained_criteria": [], "latency_ms": 12.5,
        },
        "judge.online_finding": {
            "finding_id": "fnd_online_001", "judge": {**JUDGE, "prompt_template_id": "online-judge/1"},
            "category": "identity_verification_bypass", "severity": "critical", "confidence": 0.7,
            "confidence_source": "judge_self_reported", "evidence_event_ids": ["evt_tool_requested"],
            "recommended_action": "hold_and_review", "blocking_capability": "deny_tool", "blocking_enforced": False,
            "advisory_reason": "CALIBRATION_POLICY_NOT_SATISFIED", "rationale": "Protected tool requested unverified.",
            "status": "valid", "validation_errors": [], "rejected_citation_ids": [],
            "window_from_event_id": "evt_first", "window_to_event_id": "evt_last",
        },
        "judge.online_error": {
            "judge": {**JUDGE, "prompt_template_id": "online-judge/1"}, "error_code": "JUDGE_TIMEOUT",
            "window_from_event_id": "evt_first", "window_to_event_id": "evt_last",
        },
        "judge.adjudicated": {
            "evaluation_id": "eval_001", "rubric_id": "banking.assurance.v1", "rubric_version": 1,
            "evaluation_version": "eval-1", "criteria": [CRITERION], "model_behaviour": "pass",
            "platform_enforcement": "pass", "overall_assurance": "pass", "reason_codes": [],
            "judge_coverage": "complete", "review_required_criteria": [], "rubric_event_id": "evt_rubric",
            "independence_event_id": "evt_independence", "offline_event_ids": ["evt_offline"],
        },
        "review.opened": {
            "review_id": "rev_001", "evaluation_id": "eval_001", "criterion_id": "IDENTITY-01",
            "reason": "HIGH_SEVERITY_FAILURE", "severity": "critical", "adjudication_event_id": "evt_adjudicated",
            "state": "open",
        },
        "review.claimed": {"review_id": "rev_001", "reviewer_id": "reviewer_001", "from_state": "open",
                           "to_state": "in_review"},
        "review.decided": {
            "review_id": "rev_001", "criterion_id": "IDENTITY-01", "reviewer_id": "reviewer_001", "role": "reviewer",
            "disposition": "confirm_fail", "rationale": "Ledger shows protected execution while unverified.",
            "evidence_event_ids": ["evt_tool_executing"],
            "check_correction": {"correction_id": "corr_001", "corrected_check_id": "IDENTITY-01",
                                 "corrected_check_version": "2", "rationale": "Check read the wrong call.",
                                 "evidence_event_ids": ["evt_tool_executing"]},
            "effect": "applied", "effective_verdict": "fail", "prior_verdict": "fail", "from_state": "in_review",
            "to_state": "decided", "evidence_class": "human_adjudication", "independent_model_validation": False,
        },
    }
