"""Paired prompt-profile comparison: pairing, direction of the difference, and honest handling of unmatched runs."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAVED = ROOT / "artifacts" / "full-advanced-bakeoff-2026-10-26" / "report.json"


def module():
    spec = importlib.util.spec_from_file_location("compare_prompt_profiles", ROOT / "scripts" / "compare_prompt_profiles.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def run(provider, scenario, rep, passed, first_audio=1000, completed=True):
    return {"provider": provider, "scenario_id": scenario, "repetition": rep, "completed": completed,
            "outcome": "pass" if passed else "fail", "turns": [{"turn": 0, "first_audio_ms": first_audio}],
            "cost": {"priced": True, "known_usd": "0.04"}}


class ComparePromptProfiles(unittest.TestCase):
    def test_difference_reads_tested_minus_baseline_and_counts_discordant_pairs(self):
        a = [run("p", f"s{i}", 1, passed) for i, passed in enumerate([1, 1, 0, 0, 1, 1])]
        b = [run("p", f"s{i}", 1, passed) for i, passed in enumerate([1, 1, 1, 1, 1, 0])]
        row = module().compare(a, b)["providers"][0]
        self.assertEqual((row["n_pairs"], row["a_only"], row["b_only"]), (6, 1, 2))
        self.assertAlmostEqual(row["pass_rate_diff"], 1 / 6)  # B passes 5/6, A 4/6
        self.assertEqual(row["p_value"], 1.0)

    def test_only_pairs_completed_in_both_are_compared_and_unmatched_providers_named(self):
        a = [run("p", "s1", 1, True), run("p", "s2", 1, True, completed=False), run("q", "s1", 1, True)]
        b = [run("p", "s1", 1, False), run("p", "s2", 1, False), run("r", "s1", 1, True)]
        result = module().compare(a, b)
        self.assertEqual(result["providers"][0]["n_pairs"], 1)
        self.assertEqual((result["a_only_providers"], result["b_only_providers"]), (["q"], ["r"]))

    def test_identical_inputs_report_no_difference_and_are_deterministic(self):
        a = [run("p", f"s{i}", 1, i % 2) for i in range(6)]
        first, second = module().compare(a, a), module().compare(a, a)
        self.assertEqual(first, second)
        self.assertEqual((first["providers"][0]["pass_rate_diff"], first["providers"][0]["p_value"]), (0.0, 1.0))

    @unittest.skipUnless(SAVED.exists(), "saved report not present")
    def test_runs_on_a_saved_report(self):
        m = module()
        observations = m.load(SAVED)
        result = m.compare(observations, observations)
        self.assertTrue(result["providers"])
        self.assertIn("| Provider |", m.markdown(result, "A", "B"))


if __name__ == "__main__":
    unittest.main()
