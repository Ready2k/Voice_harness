"""Scripted/seeded customer simulator with durable branch-decision records.

The simulator sees only persona, customer-visible facts and goal; the hidden
oracle is rejected at construction.  Selection is a pure function of the
declared policy, the observed predicates and the seed, so a recorded decision
reproduces without re-querying anything.
"""

import random

from .engine import digest

POLICY_ID = "scripted-banking-v1"
POLICY_VERSION = "1"


class ScriptedSimulator:
    def __init__(self, seed, customer_context, *, method="seeded"):
        if set(customer_context) - {"persona", "visible_facts", "goal"}:
            raise ValueError("HIDDEN_ORACLE_ACCESS")
        if method not in {"scripted", "seeded"}:
            raise ValueError("UNSUPPORTED_SELECTION_METHOD")
        self.seed, self.context, self.method = seed, customer_context, method

    @staticmethod
    def policy_hash(branches, fallback=None):
        return "sha256:" + digest(
            {
                "policy_id": POLICY_ID,
                "policy_version": POLICY_VERSION,
                "branches": branches,
                "fallback": fallback,
            }
        )

    @staticmethod
    def _eligible(branches, observations):
        # Precedence (higher first) then branch id is the declared tie-break rule.
        return sorted(
            (b for b in branches if b["requires"] in observations),
            key=lambda b: (-b.get("precedence", 0), b["id"]),
        )

    def select(
        self,
        observations,
        branches,
        *,
        previous_node="start",
        observed_event_ids=(),
        fallback=None,
    ):
        observations = list(observations)
        eligible = self._eligible(branches, observations)
        fallback_used = False
        draw = None
        if eligible:
            if self.method == "seeded":
                draw = random.Random(self.seed).random()
                branch = eligible[min(len(eligible) - 1, int(draw * len(eligible)))]
            else:
                branch = eligible[0]
        elif fallback is not None:
            branch, fallback_used = fallback, True
        else:
            raise ValueError("SIMULATOR_NO_ELIGIBLE_BRANCH")
        state_hash = "sha256:" + digest(
            {"node": previous_node, "observations": observations}
        )
        record = {
            "previous_node": previous_node,
            "previous_state_hash": state_hash,
            "branch_policy_id": POLICY_ID,
            "branch_policy_version": POLICY_VERSION,
            "branch_policy_hash": self.policy_hash(branches, fallback),
            "observed_event_ids": list(observed_event_ids),
            "eligible_branch_ids": [b["id"] for b in eligible],
            "predicate_results": {
                name: name in observations
                for name in sorted({b["requires"] for b in branches})
            },
            "selected_branch_id": branch["id"],
            "selection_method": self.method,
            "selector_model_config_hash": None,
            "seed": self.seed if self.method == "seeded" else None,
            "random_draw": draw,
            "next_node": branch["next_node"],
            "terminate": bool(branch.get("terminate", False)),
            "fallback_used": fallback_used,
        }
        record["decision_id"] = "simdec_" + digest(record)[:16]
        return record

    @staticmethod
    def utterance(decision, branch):
        """Output record appended after (and linked to) the selection."""
        text = branch["utterance"]
        return {
            "decision_id": decision["decision_id"],
            "output_text": text,
            "output_text_sha256": digest(text),
            "output_audio_sha256": None,
            "audio_capture": "not_generated_fixture",
        }

    @classmethod
    def reproduce(cls, decision, branches, fallback=None, observations=()):
        """Recompute selection from the durable record plus policy and observations."""
        if decision["branch_policy_hash"] != cls.policy_hash(branches, fallback):
            return False
        replay = cls(
            decision["seed"],
            {"persona": "replay", "visible_facts": {}, "goal": "replay"},
            method=decision["selection_method"],
        )
        again = replay.select(
            observations,
            branches,
            previous_node=decision["previous_node"],
            observed_event_ids=decision["observed_event_ids"],
            fallback=fallback,
        )
        return again == decision
