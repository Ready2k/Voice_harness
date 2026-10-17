"""Model-driven customer simulator (AC-16).

A ``chooser`` (a model call, injected) picks the next branch from what the customer can
see.  The prompt carries persona, customer-visible facts, goal, the observations so far and
the *declared* branch menu; the hidden oracle is rejected at construction and scanned for in
the prompt.  Whatever the model returns is validated: an unknown or ineligible choice, a
chooser error or an oracle leak is recorded as a **simulator failure** (``simulator_failure``)
and falls back to the declared fallback branch, so it is never attributed to the assistant.
"""

from .engine import digest
from .simulator import POLICY_ID, POLICY_VERSION, ScriptedSimulator

ORACLE_KEYS = ("expected_tool_sequence", "expected_tool_order", "applicable_criteria",
               "prohibited_actions", "allowed_actions", "fixture_oracle", "expected_intent")


class ModelSimulator(ScriptedSimulator):
    def __init__(self, seed, customer_context, chooser, *, model_config):
        super().__init__(seed, customer_context, method="seeded")
        self.method, self.chooser, self.model_config = "model", chooser, dict(model_config)

    def build_prompt(self, observations, menu):
        prompt = {"persona": self.context["persona"], "visible_facts": self.context["visible_facts"],
                  "goal": self.context["goal"], "observations": list(observations), "branches": menu}
        text = repr(prompt)
        if any(key in text for key in ORACLE_KEYS):
            raise ValueError("HIDDEN_ORACLE_ACCESS")
        return prompt

    def select(self, observations, branches, *, previous_node="start", observed_event_ids=(), fallback=None):
        observations = list(observations)
        eligible = self._eligible(branches, observations)
        menu = [{"id": b["id"], "utterance": b["utterance"]} for b in eligible]
        prompt = self.build_prompt(observations, menu)
        failure, choice, raw = None, None, None
        try:
            raw = self.chooser(prompt)
            choice = next((b for b in eligible if b["id"] == raw), None)
            if choice is None:
                failure = "SIMULATOR_INELIGIBLE_CHOICE" if any(b["id"] == raw for b in branches) \
                    else "SIMULATOR_UNKNOWN_CHOICE"
        except Exception:  # noqa: BLE001 -- any chooser fault is a simulator failure, recorded not raised
            failure = "SIMULATOR_CHOOSER_ERROR"
        fallback_used = False
        if choice is None:
            if fallback is None:
                raise ValueError(failure or "SIMULATOR_NO_ELIGIBLE_BRANCH")
            choice, fallback_used = fallback, True
        record = {
            "previous_node": previous_node,
            "previous_state_hash": "sha256:" + digest({"node": previous_node, "observations": observations}),
            "branch_policy_id": POLICY_ID, "branch_policy_version": POLICY_VERSION,
            "branch_policy_hash": self.policy_hash(branches, fallback),
            "observed_event_ids": list(observed_event_ids),
            "eligible_branch_ids": [b["id"] for b in eligible],
            "predicate_results": {n: n in observations for n in sorted({b["requires"] for b in branches})},
            "selected_branch_id": choice["id"], "selection_method": "model",
            "selector_model_config_hash": "sha256:" + digest(self.model_config),
            "prompt_hash": "sha256:" + digest(prompt),
            "raw_choice_hash": None if raw is None else "sha256:" + digest(str(raw)),
            "seed": None, "random_draw": None,
            "next_node": choice["next_node"], "terminate": bool(choice.get("terminate", False)),
            "fallback_used": fallback_used,
            "simulator_failure": failure,
            "failure_attribution": "simulator" if failure else None,
        }
        record["decision_id"] = "simdec_" + digest(record)[:16]
        return record

    @classmethod
    def reproduce(cls, *_args, **_kwargs):
        raise NotImplementedError("MODEL_DECISIONS_REPLAY_FROM_RECORD_NOT_RE-QUERY")
