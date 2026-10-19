"""Provider bake-off: statistics, gates, ranking and the standalone report export."""

import json
import re
import unittest

from pydantic import ValidationError

from voice_harness import bakeoff
from voice_harness.bakeoff_export import render


def obs(provider, scenario, outcome="pass", *, repetition=0, audio="clean_studio", attempts=1, criteria=None,
        assessed=2, unsafe=0, latencies=(900,), tool_first=False, prohibited=0):
    completed = outcome != "run_failed"
    return {
        "scenario_id": scenario, "provider": provider, "repetition": repetition, "scenario": scenario.title(),
        "group": "Happy path · legitimate requests", "group_order": 0, "audio_profile": audio, "run_id": f"run_{provider}_{scenario}",
        "attempts": attempts, "earlier_failures": [], "completed": completed, "outcome": outcome,
        "model_behaviour": outcome if completed else None, "platform_enforcement": "pass" if completed else None,
        "failure": None if completed else "TURN_TIMEOUT",
        "criteria": criteria if criteria is not None else ({"FACT-01": "pass" if outcome == "pass" else "fail"} if completed else {}),
        "prohibited_executed": prohibited, "replies_assessed": assessed if completed else 0, "replies_unsafe": unsafe,
        "turns": [{"turn": i, "overlapped": False, "first_audio_ms": ms, "tool_first": tool_first, "estimated": False}
                  for i, ms in enumerate(latencies)] if completed else [],
    }


def payload(observations, providers=("openai-realtime-live", "nova-sonic-live"), repetitions=1):
    return {"name": "Test bake-off", "providers": list(providers), "weights": dict(bakeoff.DEFAULT_WEIGHTS),
            "repetitions": repetitions, "cells": [{"observation": o, "done": True} for o in observations]}


class Statistics(unittest.TestCase):
    def test_wilson_interval_matches_the_published_formula(self):
        lo, hi = bakeoff.wilson(33, 33)
        self.assertAlmostEqual(lo, 0.8957, places=3)
        self.assertEqual(hi, 1.0)
        lo, hi = bakeoff.wilson(30, 33)
        self.assertAlmostEqual(lo, 0.7643, places=3)
        self.assertAlmostEqual(hi, 0.9686, places=3)
        self.assertEqual(bakeoff.wilson(0, 0), (None, None))

    def test_exact_mcnemar_uses_only_discordant_pairs(self):
        self.assertEqual(bakeoff.mcnemar_exact(0, 0), 1.0)
        self.assertAlmostEqual(bakeoff.mcnemar_exact(3, 0), 0.25)
        self.assertAlmostEqual(bakeoff.mcnemar_exact(3, 1), 0.625)
        self.assertLess(bakeoff.mcnemar_exact(10, 0), 0.01)

    def test_quantile_interpolates_like_d3(self):
        self.assertEqual(bakeoff.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertAlmostEqual(bakeoff.quantile(list(range(11)), 0.9), 9.0)
        self.assertIsNone(bakeoff.quantile([], 0.5))


class Report(unittest.TestCase):
    def test_a_prohibited_tool_or_safety_failure_eliminates_a_provider_whatever_it_scores(self):
        observations = [obs("openai-realtime-live", f"s{i}") for i in range(5)]
        observations += [obs("nova-sonic-live", f"s{i}", latencies=(300,)) for i in range(4)]
        observations.append(obs("nova-sonic-live", "s4", prohibited=1, latencies=(300,)))
        report = bakeoff.report(payload(observations))
        gates = {g["provider"]: g for g in report["gates"]}
        self.assertTrue(gates["openai-realtime-live"]["advances"])
        self.assertFalse(gates["nova-sonic-live"]["advances"])
        self.assertEqual(report["verdict"]["eliminated"], ["nova-sonic-live"])
        # Nova is faster, but an eliminated provider ranks after every provider that advanced.
        self.assertEqual([s["provider"] for s in report["scores"]], ["openai-realtime-live", "nova-sonic-live"])

    def test_completion_below_the_gate_eliminates(self):
        observations = [obs("openai-realtime-live", f"s{i}") for i in range(10)]
        observations += [obs("nova-sonic-live", f"s{i}") for i in range(9)] + [obs("nova-sonic-live", "s9", "run_failed")]
        gates = {g["provider"]: g for g in bakeoff.report(payload(observations))["gates"]}
        self.assertEqual(gates["nova-sonic-live"]["completion_rate"], 0.9)
        self.assertFalse(gates["nova-sonic-live"]["advances"])

    def test_tied_outcomes_are_not_separable_and_latency_decides_the_order(self):
        slow = [obs("openai-realtime-live", f"s{i}", latencies=(1000, 1100)) for i in range(6)]
        fast = [obs("nova-sonic-live", f"s{i}", latencies=(500, 600)) for i in range(6)]
        report = bakeoff.report(payload(slow + fast))
        self.assertEqual(report["verdict"]["leader"], "nova-sonic-live")
        self.assertFalse(report["verdict"]["separable"])
        self.assertEqual(report["verdict"]["leader_vs_runner_up_p"], 1.0)
        pair = report["pairs"][0]
        self.assertEqual((pair["both"], pair["a_only"], pair["b_only"], pair["better"]), (6, 0, 0, None))

    def test_slow_tails_count_in_responsiveness_and_the_median_only_ranking_is_reported(self):
        # Nova: fast typical replies, slow tool-first ones. OpenAI: steady.
        nova = [obs("nova-sonic-live", f"s{i}", latencies=(500, 500, 2500)) for i in range(5)]
        openai = [obs("openai-realtime-live", f"s{i}", latencies=(900, 950, 1000)) for i in range(5)]
        report = bakeoff.report(payload(nova + openai))
        scores = {s["provider"]: s for s in report["scores"]}
        self.assertGreater(scores["openai-realtime-live"]["composite"], scores["nova-sonic-live"]["composite"])
        self.assertEqual(report["verdict"]["median_only_leader"], "nova-sonic-live")

    def test_a_significant_head_to_head_names_the_better_provider(self):
        good = [obs("openai-realtime-live", f"s{i}") for i in range(12)]
        bad = [obs("nova-sonic-live", f"s{i}", "fail") for i in range(10)] + [obs("nova-sonic-live", f"s{i}") for i in (10, 11)]
        report = bakeoff.report(payload(good + bad))
        self.assertEqual(report["pairs"][0]["better"], "openai-realtime-live")
        self.assertTrue(report["verdict"]["separable"])

    def test_repetitions_pair_by_scenario_and_repetition(self):
        observations = [obs(p, "s0", "pass" if (p == "openai-realtime-live" or r == 0) else "fail", repetition=r)
                        for p in ("openai-realtime-live", "nova-sonic-live") for r in range(3)]
        pair = bakeoff.report(payload(observations, repetitions=3))["pairs"][0]
        self.assertEqual(pair["both"] + pair["a_only"] + pair["b_only"] + pair["neither"], 3)

    def test_weights_are_normalised_and_rates_carry_wilson_intervals(self):
        observations = [obs("openai-realtime-live", f"s{i}") for i in range(4)] + [obs("nova-sonic-live", f"s{i}") for i in range(4)]
        report = bakeoff.report(payload(observations), weights={**bakeoff.DEFAULT_WEIGHTS, "task_success": 3.0})
        self.assertAlmostEqual(sum(report["weights"].values()), 1.0)
        task = report["scores"][0]["task_success"]
        self.assertEqual((task["k"], task["n"]), (4, 4))
        self.assertLess(task["lo"], 1.0)
        self.assertLessEqual(report["scores"][0]["composite"], 1.0)

    def test_pending_cells_make_the_report_provisional(self):
        p = payload([obs("openai-realtime-live", "s0"), obs("nova-sonic-live", "s0")])
        p["cells"].append({"scenario_id": "s1", "provider_config_id": "nova-sonic-live", "repetition": 0, "attempts": []})
        report = bakeoff.report(p)
        self.assertEqual(report["progress"], {"cells": 3, "observed": 2, "pending": 1})
        self.assertFalse(report["verdict"]["final"])


class Requests(unittest.TestCase):
    def test_a_plan_needs_two_distinct_live_providers_and_catalogue_scenarios(self):
        with self.assertRaises(ValidationError):
            bakeoff.BakeoffRequest(name="x", provider_config_ids=["openai-realtime-live"])
        with self.assertRaises(ValidationError):
            bakeoff.BakeoffRequest(name="x", provider_config_ids=["openai-realtime-live", "openai-realtime-live"])
        with self.assertRaises(ValidationError):
            bakeoff.BakeoffRequest(name="x", provider_config_ids=["openai-realtime-live", "gemini-live"],
                                   scenario_ids=["identity-bypass-001"])  # a mock fixture, not a live scenario
        ok = bakeoff.BakeoffRequest(name="x", provider_config_ids=["openai-realtime-live", "gemini-live"], repetitions=2)
        self.assertEqual(ok.weights.model_dump(), bakeoff.DEFAULT_WEIGHTS)

    def test_oversized_plans_and_all_zero_weights_are_refused(self):
        with self.assertRaises(ValidationError):
            bakeoff.BakeoffRequest(name="x", provider_config_ids=list(bakeoff.NAMES), repetitions=10)
        with self.assertRaises(ValidationError):
            bakeoff.Weights(**dict.fromkeys(bakeoff.DEFAULT_WEIGHTS, 0))


class Export(unittest.TestCase):
    def test_the_report_is_one_self_contained_file_and_embedded_text_cannot_close_its_script(self):
        o = obs("openai-realtime-live", "s0")
        o["scenario"] = "</script><script>alert(1)</script>"
        html = render(payload([o, obs("nova-sonic-live", "s0")]), title="Bake <off>", generated_at="2026-10-19 09:00 UTC")
        self.assertIn("<title>Bake &lt;off&gt;</title>", html)
        self.assertNotIn("</script><script>alert(1)", html)
        data = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', html, re.S).group(1)
        parsed = json.loads(data)
        self.assertEqual(parsed["observations"][0]["scenario"], "</script><script>alert(1)</script>")
        self.assertEqual(parsed["report"]["verdict"]["final"], True)
        self.assertNotRegex(html, r'<(script|link)[^>]+(src|href)="https?:')  # nothing loads from the network


if __name__ == "__main__":
    unittest.main()
