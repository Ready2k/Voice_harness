"""Scripted customer context excludes the hidden oracle by construction."""

import random

from .engine import digest


class ScriptedSimulator:
    def __init__(self, seed, customer_context):
        if set(customer_context) - {"persona", "visible_facts", "goal"}:
            raise ValueError("HIDDEN_ORACLE_ACCESS")
        self.seed, self.context = seed, customer_context

    def select(self, observations, branches):
        eligible = [b for b in branches if b["requires"] in observations]
        if not eligible:
            raise ValueError("SIMULATOR_NO_ELIGIBLE_BRANCH")
        branch = random.Random(self.seed).choice(eligible)
        return {
            "previous_node": "start",
            "next_node": branch["next_node"],
            "observations": list(observations),
            "selected_branch": branch["id"],
            "method": "seeded_script",
            "seed": self.seed,
            "policy_hash": digest(branches),
            "output": branch["utterance"],
        }
