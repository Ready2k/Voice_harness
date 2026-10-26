"""Provider bake-off: statistics, gates, ranking and the standalone report export."""

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from voice_harness import bakeoff, bakeoff_stats, cost
from voice_harness.bakeoff_export import render
from voice_harness.pricing import load_table

VENDOR_TABLE = Path(__file__).parent.parent / "artifacts" / "pricing" / "vendor-pricing-2026-10-24.json"


def obs(provider, scenario, outcome="pass", *, repetition=0, audio="clean_studio", attempts=1, criteria=None,
        assessed=2, unsafe=0, latencies=(900,), tool_first=False, prohibited=0, run_cost=None):
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
        **({"cost": run_cost} if run_cost else {}),
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


class Uncertainty(unittest.TestCase):
    OPENAI, NOVA, XAI = "openai-realtime-live", "nova-sonic-live", "xai-voice-live"

    def mixed(self):
        observations = []
        for i in range(8):
            observations.append(obs(self.OPENAI, f"s{i}", "pass" if i % 4 else "fail", latencies=(700 + 40 * i, 900),
                                    audio="street_noise_10db" if i % 3 == 0 else "clean_studio"))
            observations.append(obs(self.NOVA, f"s{i}", "pass" if i % 3 else "fail", latencies=(500 + 30 * i,),
                                    attempts=2 if i == 2 else 1))
        observations.append(obs(self.NOVA, "s8", "run_failed"))
        return observations

    def test_holm_adjustment_is_monotone_and_capped(self):
        adjusted = bakeoff_stats.holm_adjust([0.01, 0.04, 0.03])
        self.assertEqual([round(x, 6) for x in adjusted], [0.03, 0.06, 0.06])
        self.assertEqual(bakeoff_stats.holm_adjust([0.6, 0.9]), [1.0, 1.0])
        self.assertTrue(all(0 <= x <= 1 for x in bakeoff_stats.holm_adjust([0.0, 0.5, 1.0])))

    def test_cluster_data_reproduces_the_reports_point_estimates(self):
        observations = self.mixed()
        report = bakeoff.report(payload(observations))
        point = bakeoff_stats.point_dimensions([o for o in observations], [self.OPENAI, self.NOVA])
        for row in report["scores"]:
            for dimension in bakeoff_stats.COMPOSITE_KEYS:
                self.assertAlmostEqual(point[row["provider"]][dimension] or 0, row[dimension]["value"] or 0, places=9, msg=dimension)

    def test_clustered_interval_is_wider_than_wilson_when_repetitions_move_together(self):
        # Each scenario passes on every repetition or on none: 10 independent scenarios, not 50 independent runs.
        observations = [obs(p, f"s{i}", "pass" if i < 5 or p == self.NOVA else "fail", repetition=r)
                        for p in (self.OPENAI, self.NOVA) for i in range(10) for r in range(5)]
        task = next(s for s in bakeoff.report(payload(observations, repetitions=5))["scores"] if s["provider"] == self.OPENAI)["task_success"]
        self.assertGreater(task["cluster_hi"] - task["cluster_lo"], 1.5 * (task["hi"] - task["lo"]))

    def test_bootstrap_is_deterministic_and_covers_every_dimension(self):
        a = bakeoff.report(payload(self.mixed()))["uncertainty"]
        b = bakeoff.report(payload(self.mixed()))["uncertainty"]
        self.assertEqual(a, b)
        self.assertEqual(a["method"], "scenario-cluster bootstrap")
        self.assertEqual(a["scenario_clusters"], 9)
        for provider in (self.OPENAI, self.NOVA):
            self.assertIn("responsiveness", a["dimensions"][provider])
            self.assertLessEqual(a["composite"][provider]["lo"], a["composite"][provider]["hi"])

    def test_one_scenario_gives_no_interval_rather_than_a_false_one(self):
        report = bakeoff.report(payload([obs(self.OPENAI, "s0"), obs(self.NOVA, "s0")]))
        self.assertEqual(report["uncertainty"]["resamples"], 0)
        self.assertIsNone(report["scores"][0]["task_success"]["cluster_lo"])
        self.assertIsNone(report["scores"][0]["composite_lo"])

    def test_holm_correction_stops_a_raw_p_value_from_naming_a_winner(self):
        # Two of three pairs have raw p = 0.031; after correcting for three tests neither is significant.
        observations = [obs(p, f"s{i}", "fail" if p == self.NOVA else "pass") for p in (self.OPENAI, self.NOVA, self.XAI) for i in range(6)]
        pairs = {(x["a"], x["b"]): x for x in bakeoff.report(payload(observations, providers=(self.OPENAI, self.NOVA, self.XAI)))["pairs"]}
        pair = pairs[(self.OPENAI, self.NOVA)]
        self.assertLess(pair["p_value"], 0.05)
        self.assertGreaterEqual(pair["p_adjusted"], 0.05)
        self.assertIsNone(pair["better"])

    def test_unbeaten_provider_is_rank_stable_and_identical_providers_have_no_clear_winner(self):
        good = [obs(self.OPENAI, f"s{i}", latencies=(300,)) for i in range(8)]
        bad = [obs(self.NOVA, f"s{i}", "fail", latencies=(900,)) for i in range(8)]
        verdict = bakeoff.report(payload(good + bad))["verdict"]
        self.assertTrue(verdict["rank_stable"])
        self.assertEqual(verdict["weight_win_share"], 1.0)
        self.assertFalse(verdict["no_clear_winner"])
        same = [obs(p, f"s{i}") for p in (self.OPENAI, self.NOVA) for i in range(8)]
        verdict = bakeoff.report(payload(same))["verdict"]
        self.assertTrue(verdict["no_clear_winner"])
        self.assertFalse(verdict["separable"])
        self.assertIsNone(bakeoff.report(payload(same[:-1]))["verdict"]["no_clear_winner"])  # unmatched: no leader to call

    def test_weight_sensitivity_splits_when_providers_trade_dimensions(self):
        values = {"a": {**dict.fromkeys(bakeoff_stats.COMPOSITE_KEYS, 0.5), "task_success": 1.0, "responsiveness": 0.1},
                  "b": {**dict.fromkeys(bakeoff_stats.COMPOSITE_KEYS, 0.5), "task_success": 0.1, "responsiveness": 1.0}}
        result = bakeoff_stats.weight_sensitivity(values, ["a", "b"])
        self.assertGreater(result["win_share"]["a"], 0.3)
        self.assertGreater(result["win_share"]["b"], 0.3)
        self.assertAlmostEqual(sum(result["win_share"].values()), 1.0)
        self.assertFalse(result["rank_stable"])
        self.assertIsNone(bakeoff_stats.weight_sensitivity(values, [])["rank_stable"])

    def test_assurance_is_split_by_track_and_undecided_runs_are_counted_not_hidden(self):
        observations = [obs(self.OPENAI, f"s{i}") for i in range(3)] + [obs(self.OPENAI, "s3", "review_required")]
        observations += [obs(self.NOVA, f"s{i}") for i in range(4)]
        observations[3]["platform_enforcement"] = "fail"
        report = bakeoff.report(payload(observations))
        row = next(s for s in report["scores"] if s["provider"] == self.OPENAI)
        coverage = next(c for c in report["coverage"] if c["provider"] == self.OPENAI)
        self.assertEqual((row["task_success"]["k"], row["task_success"]["n"]), (3, 4))
        self.assertEqual((row["task_success_decided"]["k"], row["task_success_decided"]["n"]), (3, 3))
        self.assertEqual((row["model_behaviour"]["k"], row["model_behaviour"]["n"]), (3, 4))
        self.assertEqual((row["platform_enforcement"]["k"], row["platform_enforcement"]["n"]), (3, 4))
        self.assertEqual(coverage["assurance_undecided"], 1)
        self.assertEqual(coverage["assurance_outcomes"], {"pass": 3, "review_required": 1})

    def test_power_figures_are_consistent(self):
        needed = bakeoff_stats.mcnemar_runs_needed(0.3, 0.15)
        self.assertGreater(bakeoff_stats.mcnemar_runs_needed(0.3, 0.1), needed)
        smallest = bakeoff_stats.mcnemar_min_detectable(needed, 0.3)
        self.assertLessEqual(smallest, 0.15 + 1e-6)
        self.assertLessEqual(bakeoff_stats.mcnemar_runs_needed(0.3, smallest), needed)
        self.assertIsNone(bakeoff_stats.mcnemar_runs_needed(0.0, 0.1))
        self.assertIsNone(bakeoff_stats.mcnemar_runs_needed(0.3, 0.0))
        self.assertIsNone(bakeoff_stats.mcnemar_min_detectable(3, 0.1))  # too few pairs to detect anything

    def test_pairs_carry_effect_size_and_power(self):
        good = [obs(self.OPENAI, f"s{i}") for i in range(10)]
        bad = [obs(self.NOVA, f"s{i}", "fail" if i < 6 else "pass") for i in range(10)]
        pair = bakeoff.report(payload(good + bad))["pairs"][0]
        self.assertEqual(pair["n_pairs"], 10)
        self.assertAlmostEqual(pair["pass_rate_diff"], 0.6)
        self.assertLessEqual(pair["diff_lo"], 0.6)
        self.assertGreaterEqual(pair["diff_hi"], 0.6)
        self.assertAlmostEqual(pair["discordant_share"], 0.6)
        self.assertIsNotNone(pair["runs_needed_80"])


class ScoringChoices(unittest.TestCase):
    OPENAI, NOVA = "openai-realtime-live", "nova-sonic-live"

    def test_default_weights_are_the_old_mix_without_reliability_rescaled(self):
        self.assertNotIn("reliability", bakeoff.DEFAULT_WEIGHTS)
        self.assertAlmostEqual(sum(bakeoff.DEFAULT_WEIGHTS.values()), 1.0, places=6)
        old = {"task_success": 0.30, "factual": 0.20, "safe_speech": 0.15, "responsiveness": 0.15, "robustness": 0.05}
        for dimension, weight in old.items():
            self.assertAlmostEqual(bakeoff.DEFAULT_WEIGHTS[dimension], weight / 0.85, delta=0.006)
        self.assertNotIn("reliability", [d["dimension"] for d in bakeoff.report(payload([obs(self.OPENAI, "s0"), obs(self.NOVA, "s0")]))["dimensions"]])

    def test_a_stored_reliability_weight_is_ignored_and_the_rest_rescaled(self):
        observations = [obs(self.OPENAI, f"s{i}", latencies=(400,)) for i in range(4)] + [obs(self.NOVA, f"s{i}", "fail", latencies=(900,)) for i in range(4)]
        plain = bakeoff.report(payload(observations))
        old = payload(observations)
        old["weights"] = {"task_success": 0.30, "factual": 0.20, "safe_speech": 0.15, "reliability": 0.15, "responsiveness": 0.15, "robustness": 0.05}
        report = bakeoff.report(old)
        self.assertNotIn("reliability", report["weights"])
        self.assertAlmostEqual(sum(report["weights"].values()), 1.0)
        self.assertAlmostEqual(report["weights"]["task_success"], 0.30 / 0.85)
        self.assertEqual(report["scoring"]["ignored_weights"], {"reliability": 0.15})
        self.assertEqual(plain["scoring"]["ignored_weights"], {})
        # Reliability is still reported, as a rate and as coverage, but it no longer moves the composite.
        self.assertEqual(report["scores"][0]["reliability"]["n"], 4)
        self.assertIn("first_attempt_completed", report["coverage"][0])
        with self.assertRaises(ValidationError):
            bakeoff.Weights(task_success=0, factual=0, safe_speech=0, reliability=1, responsiveness=0, robustness=0)
        only_reliability = payload(observations)
        only_reliability["weights"] = {"reliability": 1.0}
        self.assertEqual(bakeoff.report(only_reliability)["weights"], plain["weights"])  # nothing left to score: defaults apply

    def test_responsiveness_uses_fixed_targets_and_the_relative_figure_is_kept(self):
        def score(latencies, others=()):
            rows = [obs(self.OPENAI, f"s{i}", latencies=latencies) for i in range(3)]
            rows += [obs(self.NOVA, f"s{i}", latencies=others or latencies) for i in range(3)]
            return {s["provider"]: s for s in bakeoff.report(payload(rows))["scores"]}[self.OPENAI]

        self.assertAlmostEqual(score((400, 600))["responsiveness"]["value"], 1.0)  # median 500 and p90 590 are both within target
        slow = score((1000,), others=(300,))
        self.assertAlmostEqual(slow["responsiveness"]["value"], 2 / 3 * 0.5 + 1 / 3 * 0.8)  # median 1000 vs 500, p90 1000 vs 800
        # A faster rival changes only the relative figure.
        self.assertAlmostEqual(slow["responsiveness_relative"]["value"], 0.3)
        self.assertAlmostEqual(score((1000,))["responsiveness"]["value"], slow["responsiveness"]["value"])
        self.assertEqual(bakeoff_stats.latency_score(500, 500), 1.0)
        self.assertEqual(bakeoff_stats.latency_score(1000, 500), 0.5)
        self.assertIsNone(bakeoff_stats.latency_score(None, 500))
        self.assertIsNone(bakeoff_stats.responsiveness(None, None))

    def test_severity_weighted_rate_counts_a_critical_failure_more_than_a_minor_one(self):
        self.assertEqual(bakeoff_stats.SEVERITY_WEIGHTS, {"critical": 4, "high": 3, "medium": 2, "low": 1})
        self.assertEqual([bakeoff_stats.severity_name(c) for c in ("CONFIRM-01", "FACT-01", "CARE-01")], ["critical", "high", "medium"])
        critical = obs(self.OPENAI, "s0", criteria={"CONFIRM-01": "fail", "FACT-01": "pass", "CARE-01": "pass"})
        minor = obs(self.NOVA, "s0", criteria={"CONFIRM-01": "pass", "FACT-01": "pass", "CARE-01": "fail"})
        rows = {s["provider"]: s for s in bakeoff.report(payload([critical, minor]))["scores"]}
        self.assertAlmostEqual(rows[self.OPENAI]["severity_weighted"]["value"], 5 / 9)
        self.assertAlmostEqual(rows[self.NOVA]["severity_weighted"]["value"], 7 / 9)
        partial = bakeoff_stats.severity_sums({"CARE-01": "partial", "FACT-01": "untestable"})
        self.assertEqual(partial, (1.0, 2.0))  # untestable is not scored; partial earns half credit
        coverage = {c["provider"]: c for c in bakeoff.report(payload([critical, minor]))["coverage"]}
        self.assertEqual(coverage[self.OPENAI]["failures_by_severity"], {"critical": 1})

    def test_group_balanced_scoring_is_an_option_and_pooled_stays_the_default(self):
        def rows(provider):
            big = [obs(provider, f"a{i}") for i in range(9)]
            small = [obs(provider, "b0", "fail")]
            small[0]["group"] = "Identity protection · unverified callers"
            small[0]["criteria"] = {"FACT-01": "fail"}
            return big + small

        both = payload(rows(self.OPENAI) + rows(self.NOVA))
        pooled = bakeoff.report(both)["scores"][0]["task_success"]
        self.assertAlmostEqual(pooled["value"], 0.9)
        self.assertIsNotNone(pooled["lo"])
        self.assertFalse(bakeoff.report(both)["scoring"]["group_balanced"])
        balanced = bakeoff.report(both, group_balanced=True)
        row = balanced["scores"][0]["task_success"]
        self.assertAlmostEqual(row["value"], 0.5)  # (9/9 + 0/1) / 2 groups
        self.assertTrue(row["balanced"])
        self.assertIsNone(row["lo"])
        self.assertTrue(balanced["scoring"]["group_balanced"])
        both["group_balanced"] = True  # the plan's own setting
        self.assertAlmostEqual(bakeoff.report(both)["scores"][0]["task_success"]["value"], 0.5)
        self.assertTrue(bakeoff.BakeoffRequest(name="x", provider_config_ids=[self.OPENAI, self.NOVA], group_balanced=True).group_balanced)

    def test_speed_alone_never_makes_a_winner_clear(self):
        slow = [obs(self.OPENAI, f"s{i}", latencies=(1200, 1300)) for i in range(8)]
        fast = [obs(self.NOVA, f"s{i}", latencies=(300, 350)) for i in range(8)]
        verdict = bakeoff.report(payload(slow + fast))["verdict"]
        self.assertEqual(verdict["leader"], self.NOVA)
        self.assertTrue(verdict["no_clear_winner"])
        self.assertTrue(verdict["speed_only_lead"])
        self.assertEqual(verdict["latency_leader"], self.NOVA)
        self.assertGreater(verdict["leader_minus_runner_up"]["lo"], 0)  # the composite gap is real, and reported
        self.assertLessEqual(verdict["quality_lead"]["lo"], 0)  # no lead on safety or correctness

    def test_a_fast_correct_provider_beats_a_faster_wrong_one(self):
        right = [obs(self.OPENAI, f"s{i}", latencies=(450, 600)) for i in range(8)]
        wrong = [obs(self.NOVA, f"s{i}", "fail" if i % 2 else "pass", latencies=(250, 300)) for i in range(8)]
        verdict = bakeoff.report(payload(right + wrong))["verdict"]
        self.assertEqual(verdict["leader"], self.OPENAI)
        self.assertFalse(verdict["no_clear_winner"])
        self.assertFalse(verdict["speed_only_lead"])
        self.assertEqual(verdict["latency_leader"], self.NOVA)
        self.assertGreater(verdict["quality_lead"]["lo"], 0)


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
        # The slow tail still costs Nova responsiveness, but the median counts twice the p90, so it keeps the lead.
        self.assertLess(scores["nova-sonic-live"]["responsiveness"]["value"], 1.0)
        self.assertGreater(scores["nova-sonic-live"]["composite"], scores["openai-realtime-live"]["composite"])
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


def priced(usd):
    return {"known_usd": usd, "priced": True, "voice_lines": 2, "unknown_lines": 0, "unknown_reasons": [],
            "pricing_table": "t@1"}


def unpriced(usd=None, reason="NO_TARIFF_FOR_METER_DATE"):
    return {"known_usd": usd, "priced": False, "voice_lines": 2, "unknown_lines": 1, "unknown_reasons": [reason],
            "pricing_table": "t@1"}


class Cost(unittest.TestCase):
    """Voice cost per run: priced only when every voice line is, never folded into the composite."""

    def usage_events(self, provider, model, usage, region=None, service=None):
        reports = cost.reports_from_adapter_usage(provider, usage, scope_id="resp_1", usage_date="2026-10-24",
                                                  service=service, model=model, region=region)
        asr = {**reports[0], "report_id": "asr:1", "component": "asr", "provider": "openai_audio_transcription",
               "service": "transcription", "model": "gpt-4o-mini-transcribe", "billing_scope_id": "asr:1"}
        return [{"type": "cost.usage_reported", "content": r, "event_id": f"e{i}"} for i, r in enumerate([*reports, asr])] + [
            {"type": "cost.expectation_declared", "content": {"components": ["voice", "asr"], "declared_by": "t"},
             "event_id": "x"}]

    def test_recorded_openai_usage_is_priced_and_asr_is_left_out(self):
        usage = {"input_tokens": 100, "output_tokens": 50, "raw": {
            "input_token_details": {"audio_tokens": 100, "text_tokens": 0, "cached_tokens": 0},
            "output_token_details": {"audio_tokens": 50, "text_tokens": 0}}}
        events = self.usage_events("openai", "gpt-realtime-2.1", usage, service="realtime")
        result = bakeoff.voice_cost("run1", events, load_table(VENDOR_TABLE))
        self.assertTrue(result["priced"], result)
        self.assertEqual(result["known_usd"], "0.006400")  # 100 * $32/1M + 50 * $64/1M; ASR is not a voice line
        self.assertEqual(result["pricing_table"], "voice-harness-vendor-pricing@2026-10-24")

    def test_gemini_3_8_audio_usage_is_priced(self):
        usage = {"input_tokens": 1000, "output_tokens": 500, "raw": {
            "promptTokenCount": 1000, "candidatesTokenCount": 500,
            "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 1000}],
            "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 500}]}}
        events = self.usage_events("google_gemini_live", "gemini-3.8-live", usage, region="us-central1",
                                   service="google_gemini_live")
        result = bakeoff.voice_cost("run1", events, load_table(VENDOR_TABLE))
        self.assertTrue(result["priced"], result)
        self.assertEqual(result["known_usd"], "0.009000")  # 1000 * $3/1M + 500 * $12/1M

    def test_cost_rows_separate_priced_partial_and_unrecorded_providers(self):
        observations = [obs("openai-realtime-live", "s0", run_cost=priced("0.010000")),
                        obs("openai-realtime-live", "s1", run_cost=priced("0.030000")),
                        obs("openai-realtime-live", "s2", "run_failed", run_cost=priced("0.020000")),
                        obs("nova-sonic-live", "s0", run_cost=priced("0.005000")),
                        obs("nova-sonic-live", "s1", run_cost=unpriced("0.001000")),
                        obs("xai-voice-live", "s0")]
        report = bakeoff.report(payload(observations, providers=("openai-realtime-live", "nova-sonic-live", "xai-voice-live")))
        rows = {r["provider"]: r for r in report["cost"]}
        openai, nova, xai = rows["openai-realtime-live"], rows["nova-sonic-live"], rows["xai-voice-live"]
        self.assertEqual((openai["status"], openai["runs_priced"], openai["median_usd"], openai["mean_usd"]),
                         ("priced", 3, "0.020000", "0.020000"))  # failed runs are billed, so they are costed
        self.assertEqual((nova["status"], nova["runs_priced"], nova["median_usd"], nova["known_usd_all_runs"]),
                         ("partial", 1, "0.005000", "0.006000"))
        self.assertEqual(nova["unpriced_reasons"], {"NO_TARIFF_FOR_METER_DATE": 1})
        self.assertEqual(xai["status"], "not_recorded")
        self.assertEqual(report["limits"]["pricing_tables"], ["t@1"])

    def test_cost_from_a_later_run_is_flagged_in_the_limits(self):
        later = {**priced("0.003000"), "separate_run": True, "cost_run_id": "run_later"}
        report = bakeoff.report(payload([obs("openai-realtime-live", "s0", run_cost=priced("0.01")),
                                         obs("nova-sonic-live", "s0", run_cost=later)]))
        self.assertEqual(report["limits"]["cost_from_separate_runs"], ["nova-sonic-live"])

    def test_cost_does_not_change_the_composite(self):
        base = [obs("openai-realtime-live", "s0"), obs("nova-sonic-live", "s0")]
        costed = [obs("openai-realtime-live", "s0", run_cost=priced("9")), obs("nova-sonic-live", "s0", run_cost=priced("0"))]
        self.assertEqual([s["composite"] for s in bakeoff.report(payload(base))["scores"]],
                         [s["composite"] for s in bakeoff.report(payload(costed))["scores"]])


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
        self.assertEqual({k: v for k, v in ok.weights.model_dump().items() if k != "reliability"}, bakeoff.DEFAULT_WEIGHTS)
        self.assertFalse(ok.group_balanced)

    def test_oversized_plans_and_all_zero_weights_are_refused(self):
        with self.assertRaises(ValidationError):
            bakeoff.BakeoffRequest(name="x", provider_config_ids=list(bakeoff.NAMES), repetitions=10)
        with self.assertRaises(ValidationError):
            bakeoff.Weights(**dict.fromkeys(bakeoff.DEFAULT_WEIGHTS, 0))


class Export(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("HARNESS_REPORT_BROWSER_TEST") == "1", "opt-in real browser export check")
    def test_standalone_tabs_filter_details_and_print_restore(self):
        observations = [obs(p, s) for p in ("openai-realtime-live", "nova-sonic-live") for s in ("s0", "s1")]
        for o in observations:
            if o["scenario_id"] == "s1":
                o.update(group="Fault tolerance", group_order=3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_text(render(payload(observations)))
            subprocess.run(["node", "tests/browser/report-export-check.cjs", str(path)], check=True)

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
        self.assertNotRegex(html, r'(src|href)=|@import|url\(|https://')  # no external references of any kind
        self.assertIn('id="theme-select"', html)  # Auto/Light/Dark control
        for needle in (':root[data-theme=dark]', ':root[data-theme=light]', 'harness-theme'):
            self.assertIn(needle, html)



class EvidenceInterpretation(unittest.TestCase):
    def test_access_failure_is_availability_not_noise_or_reasoning(self):
        good = obs("openai-realtime-live", "s0", audio="telephone_narrowband")
        failed = obs("nova-sonic-live", "s0", "run_failed", audio="telephone_narrowband")
        failed["failure"] = "PROVIDER_HTTP_403"
        r = bakeoff.report(payload([good, failed]))
        self.assertIsNone(r["verdict"]["leader"])
        self.assertFalse(r["verdict"]["comparison_complete"])
        row = next(s for s in r["scores"] if s["provider"] == "nova-sonic-live")
        self.assertIsNone(row["composite"])
        self.assertIsNone(row["robustness"]["value"])
        self.assertEqual(row["task_success"]["n"], 0)
        self.assertEqual(r["pairs"][0]["a_only"], 0)
        self.assertEqual(r["gates"][1]["failure_reasons"], {"PROVIDER_HTTP_403": 1})

    def test_unstarted_provider_is_not_given_a_zero_composite(self):
        plan = payload([])
        plan["cells"] = [{"provider_config_id": provider} for provider in plan["providers"]]
        report = bakeoff.report(plan)
        self.assertTrue(all(s["composite"] is None for s in report["scores"]))
        self.assertTrue(all(s["rank"] is None for s in report["scores"]))

    def test_no_claim_and_untestable_speech_are_not_credited_as_accuracy(self):
        a = obs("openai-realtime-live", "s0")
        a.update(evidence_version=2, criterion_reasons={"FACT-01": ["NO_AMOUNT_CLAIM"]},
                 replies_safe=0, replies_untestable=2)
        r = bakeoff.report(payload([a, obs("nova-sonic-live", "s0")]))
        row = next(s for s in r["scores"] if s["provider"] == "openai-realtime-live")
        self.assertEqual(row["factual"]["n"], 0)
        self.assertEqual(row["safe_speech"]["k"], 0)
        self.assertEqual(r["coverage"][0]["no_claim_passes"], 1)
        self.assertEqual(r["coverage"][1]["legacy_observations"], 1)


class SchedulerSafeguards(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.providers = ["openai-realtime-live", "nova-sonic-live"]
        self.p = {"bakeoff_id": "test", "name": "test", "mode": "launched", "state": "running",
                  "phase": "preflight", "providers": self.providers,
                  "preflight": {"generation": 0, "checks": bakeoff.preflight_checks(self.providers)},
                  "cells": [{"scenario_id": "happy-balance-clean-001", "provider_config_id": p,
                             "repetition": 0, "attempts": []} for p in self.providers]}
        self.runs, self.launched = {}, []
        outer = self

        class Store:
            def active(self):
                return ["test"] if outer.p["state"] in {"running", "paused"} else []

            def get(self, key):
                return outer.p

            def save(self, payload):
                outer.p = payload

        class DB:
            def get(self, rid):
                return outer.runs[rid]

            def events(self, rid):
                return outer.runs[rid]["events"]

        self.store, self.db = Store(), DB()

        def observe(database, cell):
            attempt = cell["attempts"][-1]
            done = database.get(attempt["run_id"])["state"] == "completed" if attempt.get("run_id") else False
            row = obs(cell["provider_config_id"], cell["scenario_id"], "pass" if done else "run_failed")
            row.update(failure=attempt.get("failure"), attempts=len(cell["attempts"]))
            return row

        patcher = patch.object(bakeoff, "observe", observe)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, config, key):
        rid = f"run_{len(self.launched)}"
        self.launched.append((rid, config, key))
        self.runs[rid] = {"state": "running", "events": []}
        return rid

    def settle(self, rid, failure=None, tool=True):
        events = [{"sequence": 1, "type": "input.caller_audio", "content": {}},
                  {"sequence": 2, "type": "tool.requested", "content": {"tool": "get_policy_text", "call_id": "c"}},
                  {"sequence": 3, "type": "tool.completed", "content": {"call_id": "c", "status": "success"}},
                  {"sequence": 4, "type": "audio.output", "content": {}}]
        if not tool:
            events = events[:1] + events[-1:]
        if failure:
            events = [{"type": "run.failed", "content": {"reason": failure}}]
        self.runs[rid] = {"state": "failed" if failure else "completed", "events": events}

    def tick(self):
        return bakeoff.tick(self.store, self.db, self.start, capacity=lambda: 6)

    def pass_preflight(self):
        self.tick()
        for rid, _, _ in self.launched:
            self.settle(rid)
        self.tick()

    def test_all_providers_must_finish_voice_tool_preflight_before_any_scored_run(self):
        self.tick()
        self.assertEqual(len(self.launched), 2)
        self.assertTrue(all(c["scenario_id"] == "happy-policy-question-001" for _, c, _ in self.launched))
        self.settle("run_0")
        self.tick()
        self.assertEqual(len(self.launched), 2)
        self.settle("run_1")
        self.tick()
        self.assertEqual(len(self.launched), 4)
        self.assertEqual(self.p["phase"], "scored")

    def test_advanced_profile_is_used_for_preflight_and_scored_runs(self):
        self.p["prompt_profile"] = "advanced-v1"
        self.pass_preflight()
        self.assertEqual(len(self.launched), 4)
        self.assertTrue(all(config["prompt_profile"] == "advanced-v1" for _, config, _ in self.launched))

    def test_http_403_pauses_all_providers_without_retrying_or_launching_scored_work(self):
        self.tick()
        self.settle("run_0", "PROVIDER_HTTP_403")
        self.tick()
        self.assertEqual(self.p["state"], "paused")
        self.assertEqual(self.p["pause_reason"]["reason"], "PROVIDER_HTTP_403")
        for _ in range(3):
            self.tick()
        self.assertEqual(len(self.launched), 2)
        self.assertTrue(all(not c["attempts"] for c in self.p["cells"]))
        with self.assertRaisesRegex(ValueError, "STILL_SETTLING"):
            bakeoff.resume(self.p, self.db)

    def test_session_without_tool_exchange_does_not_pass_preflight(self):
        self.tick()
        self.settle("run_0", tool=False)
        self.tick()
        self.assertEqual(self.p["pause_reason"]["reason"], "PREFLIGHT_VOICE_TOOL_INCOMPLETE")
        self.assertEqual(len(self.launched), 2)

    def test_midrun_failure_pauses_and_resume_rechecks_all_providers_preserving_successes(self):
        self.pass_preflight()
        self.settle("run_2")
        self.settle("run_3", "PROVIDER_HTTP_401")
        self.tick()
        self.assertEqual(self.p["state"], "paused")
        self.assertTrue(self.p["cells"][0]["done"])
        bakeoff.resume(self.p, self.db)
        self.tick()
        self.assertEqual(len(self.launched), 6)
        self.assertTrue(all(c["scenario_id"] == "happy-policy-question-001" for _, c, _ in self.launched[-2:]))
        self.settle("run_4")
        self.settle("run_5")
        self.tick()
        self.assertEqual(len(self.launched), 7)  # successful scored cell is not repeated
        self.settle("run_6")
        self.tick()
        self.assertEqual(self.p["state"], "completed")
        self.assertEqual(len(self.p["cells"][1]["attempts"]), 2)
        self.assertEqual(len(self.p["preflight_history"]), 1)
        self.assertEqual(len({key for _, _, key in self.launched}), len(self.launched))

    def test_two_transient_failures_trip_the_circuit_instead_of_advancing(self):
        self.tick()
        self.settle("run_0", "PROVIDER_UNREACHABLE")
        self.settle("run_1")
        self.tick()
        self.assertEqual(self.p["state"], "running")
        self.settle("run_2", "PROVIDER_UNREACHABLE")
        self.tick()
        self.assertEqual(self.p["state"], "paused")
        self.assertEqual(len(self.launched), 3)


if __name__ == "__main__":
    unittest.main()
