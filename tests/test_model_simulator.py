"""AC-16: model-driven simulator picks branches itself, rejects the oracle, owns its failures."""

import os
import tempfile
import unittest
from pathlib import Path

from voice_harness.contracts import RunConfig, SimulatorBranchSelected
from voice_harness.engine import Engine
from voice_harness.model_simulator import ModelSimulator
from voice_harness.storage import Database

CONTEXT = {"persona": "anxious customer", "visible_facts": {"has_account": True}, "goal": "ask about balance"}
CONFIG = {"provider": "scripted", "model": "unit-chooser", "temperature": 0}
BRANCHES = [
    {"id": "ask-balance", "requires": "session_opened", "next_node": "balance", "utterance": "What is my balance?"},
    {"id": "give-answer", "requires": "session_opened", "next_node": "idv", "utterance": "The answer is blue.",
     "precedence": 1},
    {"id": "hang-up", "requires": "never_observed", "next_node": "end", "utterance": "Bye.", "terminate": True},
]
FALLBACK = {"id": "repeat", "next_node": "balance", "utterance": "Sorry, could you repeat that?"}


def sim(chooser):
    return ModelSimulator(7, CONTEXT, chooser, model_config=CONFIG)


class ModelSimulatorTests(unittest.TestCase):
    def select(self, chooser):
        return sim(chooser).select(["session_opened"], BRANCHES, fallback=FALLBACK)

    def test_model_picks_the_branch_not_the_seed(self):
        for pick in ("ask-balance", "give-answer"):
            record = self.select(lambda _p, pick=pick: pick)
            self.assertEqual((record["selected_branch_id"], record["selection_method"]), (pick, "model"))
            self.assertIsNone(record["simulator_failure"])
            self.assertFalse(record["fallback_used"])
            self.assertIsNone(record["seed"])
        SimulatorBranchSelected.model_validate(record)

    def test_prompt_has_only_customer_visible_material(self):
        seen = []
        self.select(lambda p: seen.append(p) or "ask-balance")
        self.assertEqual(set(seen[0]), {"persona", "visible_facts", "goal", "observations", "branches"})
        self.assertEqual([b["id"] for b in seen[0]["branches"]], ["give-answer", "ask-balance"])  # eligible only

    def test_hidden_oracle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HIDDEN_ORACLE_ACCESS"):
            ModelSimulator(1, {**CONTEXT, "fixture_oracle": {}}, lambda _p: "x", model_config=CONFIG)
        leaky = ModelSimulator(1, {**CONTEXT, "goal": "follow expected_tool_sequence"}, lambda _p: "x", model_config=CONFIG)
        with self.assertRaisesRegex(ValueError, "HIDDEN_ORACLE_ACCESS"):
            leaky.select(["session_opened"], BRANCHES, fallback=FALLBACK)

    def test_bad_model_output_is_a_simulator_failure_with_fallback(self):
        cases = {"nonsense": "SIMULATOR_UNKNOWN_CHOICE", "hang-up": "SIMULATOR_INELIGIBLE_CHOICE"}
        for raw, code in cases.items():
            record = self.select(lambda _p, raw=raw: raw)
            self.assertEqual(record["simulator_failure"], code)
            self.assertEqual(record["failure_attribution"], "simulator")
            self.assertTrue(record["fallback_used"])
            self.assertEqual(record["selected_branch_id"], "repeat")

    def test_chooser_error_is_a_simulator_failure_not_a_crash(self):
        def boom(_p):
            raise TimeoutError

        record = self.select(boom)
        self.assertEqual(record["simulator_failure"], "SIMULATOR_CHOOSER_ERROR")
        self.assertIsNone(record["raw_choice_hash"])
        with self.assertRaisesRegex(ValueError, "SIMULATOR_CHOOSER_ERROR"):
            sim(boom).select(["session_opened"], BRANCHES)  # no fallback declared: fail loudly

    def test_record_is_hashed_and_event_is_accepted_by_the_ledger(self):
        record = self.select(lambda _p: "ask-balance")
        self.assertTrue(record["prompt_hash"].startswith("sha256:"))
        self.assertTrue(record["selector_model_config_hash"].startswith("sha256:"))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        engine = Engine(db)
        run = engine.create(RunConfig().model_dump())
        with db.transaction(run) as tx:
            tx.event("orchestrator", "simulator.branch_selected", record)
        stored = [e for e in db.events(run) if e["type"] == "simulator.branch_selected"][0]
        self.assertEqual(stored["content"]["selection_method"], "model")


if __name__ == "__main__":
    unittest.main()
