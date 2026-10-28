"""Scorecard honesty: latency measured differently per provider, speed-only leads, and pairwise contrasts."""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

from test_bakeoff import obs, payload

from voice_harness import bakeoff, bakeoff_stats

ROOT = Path(__file__).resolve().parents[1]
OPENAI, NOVA, XAI = "openai-realtime-live", "nova-sonic-live", "xai-voice-live"
SAVED = ROOT / "artifacts" / "full-advanced-bakeoff-2026-10-26" / "report.json"


def rescore_module():
    spec = importlib.util.spec_from_file_location("rescore_bakeoff_report", ROOT / "scripts" / "rescore_bakeoff_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marked(observations, commit, anchor="speech_end"):
    for o in observations:
        o["turn_commit"] = commit
        for t in o["turns"]:
            t.update(turn_commit=commit, anchor=anchor)
    return observations


class LatencyComparability(unittest.TestCase):
    def test_one_turn_mode_for_everyone_is_comparable(self):
        r = bakeoff.report(payload(marked([obs(p, f"s{i}") for p in (OPENAI, NOVA) for i in range(4)], "provider_vad")))
        self.assertTrue(r["verdict"]["latency_comparable"])
        self.assertEqual(r["verdict"]["latency_comparability"], "comparable")
        self.assertEqual({row["latency_comparability"] for row in r["latency"]}, {"comparable"})
        self.assertEqual(r["latency"][0]["turn_commit"], {"provider_vad": 4})

    def test_explicit_commit_beside_provider_turn_detection_is_not_comparable_and_says_who_pays_endpointing(self):
        observations = marked([obs(OPENAI, f"s{i}") for i in range(4)], "explicit") + marked([obs(NOVA, f"s{i}") for i in range(4)], "provider_vad")
        r = bakeoff.report(payload(observations))
        c = r["scoring"]["latency_comparability"]
        self.assertFalse(r["verdict"]["latency_comparable"])
        self.assertEqual((c["status"], c["explicit_commit"], c["provider_endpointing"]), ("mixed_turn_control", [OPENAI], [NOVA]))
        self.assertEqual({s["responsiveness"]["comparability"] for s in r["scores"]}, {"mixed_turn_control"})

    def test_turns_re_anchored_at_the_commit_and_unrecorded_turns_are_disclosed(self):
        legacy = marked([obs(OPENAI, f"s{i}") for i in range(4)], "explicit", "legacy_commit") + marked([obs(NOVA, f"s{i}") for i in range(4)], "provider_vad", "legacy_speech_end")
        r = bakeoff.report(payload(legacy))
        rows = {row["provider"]: row["latency_comparability"] for row in r["latency"]}
        self.assertEqual(rows, {OPENAI: "legacy_commit_anchor", NOVA: "mixed_turn_control"})
        self.assertEqual(r["scoring"]["latency_comparability"]["reasons"], ["mixed_turn_control", "legacy_commit_anchor"])
        old = bakeoff.report(payload([obs(p, f"s{i}") for p in (OPENAI, NOVA) for i in range(4)]))
        self.assertEqual(old["verdict"]["latency_comparability"], "not_recorded")
        self.assertFalse(old["verdict"]["latency_comparable"])

    def test_the_composite_without_latency_is_reported_with_its_own_rank(self):
        right = [obs(OPENAI, f"s{i}", latencies=(2000,)) for i in range(8)]
        wrong = [obs(NOVA, f"s{i}", "fail" if i % 2 else "pass", latencies=(300,)) for i in range(8)]
        r = bakeoff.report(payload(right + wrong))
        by = {s["provider"]: s for s in r["scores"]}
        for s in r["scores"]:
            self.assertAlmostEqual(s["composite_excluding_latency"],
                                   bakeoff_stats.quality_of({d: s[d]["value"] for d in bakeoff_stats.QUALITY_KEYS}, r["weights"]))
            self.assertLessEqual(s["composite_excluding_latency_lo"], s["composite_excluding_latency_hi"])
        self.assertEqual((by[OPENAI]["rank_excluding_latency"], by[NOVA]["rank_excluding_latency"]), (1, 2))
        same = bakeoff.report(payload([obs(p, f"s{i}") for p in (OPENAI, NOVA) for i in range(4)]))
        self.assertEqual([s["rank_excluding_latency"] for s in same["scores"]], [1, 1])  # equal values share a rank


class SpeedOnlyLeads(unittest.TestCase):
    def test_a_speed_only_lead_has_no_first_place_and_no_stability_claim(self):
        slow = [obs(OPENAI, f"s{i}", latencies=(1200, 1300)) for i in range(8)]
        fast = [obs(NOVA, f"s{i}", latencies=(300, 350)) for i in range(8)]
        r = bakeoff.report(payload(slow + fast))
        v = r["verdict"]
        self.assertTrue(v["speed_only_lead"])
        self.assertEqual(v["ranking"], "tied_on_quality")
        self.assertEqual(v["leader"], NOVA)  # the composite order is still reported
        self.assertTrue(all(s["rank"] is None and s["standing"] == "tied" for s in r["scores"]))
        self.assertEqual([s["composite_order"] for s in r["scores"]], [1, 2])
        self.assertIsNone(v["rank_stable"])
        self.assertIsNone(v["weight_win_share"])
        self.assertIsNone(v["sensitivity_basis"])
        self.assertEqual(r["uncertainty"]["sensitivity"]["win_share"][NOVA], 1.0)  # the raw figure stays visible, labelled
        self.assertEqual(r["uncertainty"]["sensitivity"]["basis"], "all_dimensions")
        self.assertEqual(r["uncertainty"]["sensitivity_quality"]["win_share"], {OPENAI: 0.5, NOVA: 0.5})

    def test_a_provider_clearly_worse_on_quality_is_behind_not_tied(self):
        observations = [obs(NOVA, f"s{i}", latencies=(300,)) for i in range(10)]
        observations += [obs(OPENAI, f"s{i}", latencies=(900,)) for i in range(10)]
        observations += [obs(XAI, f"s{i}", "fail" if i < 6 else "pass", latencies=(250,)) for i in range(10)]
        r = bakeoff.report(payload(observations, providers=(OPENAI, NOVA, XAI)))
        standing = {s["provider"]: s["standing"] for s in r["scores"]}
        self.assertEqual(r["verdict"]["ranking"], "tied_on_quality")
        self.assertEqual(standing, {NOVA: "tied", OPENAI: "tied", XAI: "behind_on_quality"})

    def test_a_quality_lead_keeps_ranks_and_checks_stability_without_latency_when_latency_is_not_comparable(self):
        right = marked([obs(OPENAI, f"s{i}", latencies=(450, 600)) for i in range(8)], "explicit")
        wrong = marked([obs(NOVA, f"s{i}", "fail" if i % 2 else "pass", latencies=(250, 300)) for i in range(8)], "provider_vad")
        v = bakeoff.report(payload(right + wrong))["verdict"]
        self.assertEqual((v["ranking"], v["leader"], v["sensitivity_basis"]), ("ranked", OPENAI, "quality_only"))
        self.assertEqual(v["weight_win_share"], 1.0)
        self.assertTrue(v["rank_stable"])
        comparable = bakeoff.report(payload(marked(copy.deepcopy(right), "provider_vad") + wrong))["verdict"]
        self.assertEqual(comparable["sensitivity_basis"], "all_dimensions")

    def test_identical_providers_split_random_weightings_instead_of_the_first_taking_all(self):
        values = dict.fromkeys(("a", "b"), dict.fromkeys(bakeoff_stats.COMPOSITE_KEYS, 0.9))
        result = bakeoff_stats.weight_sensitivity(values, ["a", "b"])
        self.assertEqual(result["win_share"], {"a": 0.5, "b": 0.5})
        self.assertFalse(result["rank_stable"])
        self.assertEqual(bakeoff_stats.weight_sensitivity(values, ["a", "b"], keys=bakeoff_stats.QUALITY_KEYS)["basis"], "quality_only")


class Contrasts(unittest.TestCase):
    def test_every_pair_is_compared_dimension_by_dimension_and_separated_only_on_evidence(self):
        observations = []
        for i in range(10):
            a = obs(OPENAI, f"s{i}", latencies=(900,), criteria={"FACT-01": "fail" if i < 5 else "pass"})
            observations += [a, obs(NOVA, f"s{i}", latencies=(300,))]
        r = bakeoff.report(payload(observations))
        (c,) = r["contrasts"]
        self.assertEqual((c["a"], c["b"], c["method"]), (OPENAI, NOVA, "paired scenario-cluster bootstrap"))
        factual, task, speed = c["dimensions"]["factual"], c["dimensions"]["task_success"], c["dimensions"]["responsiveness"]
        self.assertEqual((factual["a"], factual["b"], factual["favours"]), (0.5, 1.0, NOVA))
        self.assertLess(factual["hi"], 0)
        self.assertFalse(task["separated"])  # assurance follows the head-to-head, which these runs do not decide
        self.assertEqual(task["p_adjusted"], r["pairs"][0]["p_adjusted"])
        self.assertEqual(speed["favours"], NOVA)
        self.assertFalse(speed["comparable"])  # turn control was not recorded
        self.assertFalse(c["dimensions"]["safe_speech"]["separated"])


class Rescore(unittest.TestCase):
    def saved(self):
        observations = [obs(XAI, f"s{i}", latencies=(2100, 1900)) for i in range(4)] + [obs(NOVA, f"s{i}", latencies=(800,)) for i in range(4)]
        for o in observations:
            o["metrics"] = {"turns": [{"turn": t["turn"], "content_audio_ms": t["first_audio_ms"]} for t in o["turns"]]}
        return payload(observations, providers=(XAI, NOVA))

    def test_explicit_commit_turns_lose_exactly_the_trailing_silence_and_nothing_else_changes(self):
        module, saved = rescore_module(), self.saved()
        fixed = copy.deepcopy(saved)
        self.assertEqual(module.correct(fixed, (XAI,), 1200), {XAI: 8})
        xai = next(c["observation"] for c in fixed["cells"] if c["observation"]["provider"] == XAI)
        self.assertEqual([t["first_audio_ms"] for t in xai["turns"]], [900, 700])
        self.assertEqual([t["content_audio_ms"] for t in xai["metrics"]["turns"]], [900, 700])
        self.assertEqual({(t["anchor"], t["turn_commit"]) for t in xai["turns"]}, {("legacy_commit", "explicit")})
        nova = next(c["observation"] for c in fixed["cells"] if c["observation"]["provider"] == NOVA)
        self.assertEqual((nova["turns"][0]["first_audio_ms"], nova["turns"][0]["anchor"]), (800, "legacy_speech_end"))
        again = copy.deepcopy(fixed)
        self.assertEqual(module.correct(again, (XAI,), 1200), {})  # idempotent
        self.assertEqual(again, fixed)
        for c in (*saved["cells"], *fixed["cells"]):
            del c["observation"]["metrics"]  # a stand-in block, not what report() summarises
        before, after = bakeoff.report(saved), bakeoff.report(fixed)
        for b, a in zip(sorted(before["scores"], key=lambda s: s["provider"]), sorted(after["scores"], key=lambda s: s["provider"]), strict=True):
            self.assertEqual(b["composite_excluding_latency"], a["composite_excluding_latency"])
        self.assertEqual(after["verdict"]["latency_comparability"], "mixed_turn_control")

    def test_audio_before_the_commit_is_refused_rather_than_made_negative(self):
        saved = self.saved()
        saved["cells"][0]["observation"]["turns"][0]["first_audio_ms"] = 900
        with self.assertRaises(ValueError):
            rescore_module().correct(saved, (XAI,), 1200)

    @unittest.skipUnless(SAVED.exists(), "the 26 October bake-off export is not in this checkout")
    def test_the_26_october_report_reproduces_and_re_scores_as_reviewed(self):
        module = rescore_module()
        saved = json.loads(SAVED.read_text())
        data = module.payload_from(saved)
        unscored = {**data["weights"], "task_completion": 0.0}  # the original scoring had no task-completion dimension
        original = {s["provider"]: s["composite"] for s in bakeoff.report(data, weights=unscored)["scores"]}
        self.assertEqual(original, {s["provider"]: s["composite"] for s in saved["report"]["scores"]})
        module.correct(data)
        latency_only = {s["provider"]: round(s["composite"], 3) for s in bakeoff.report(data, weights=unscored)["scores"]}
        self.assertEqual((latency_only[XAI], latency_only[NOVA]), (0.920, 0.918))
        self.assertEqual(module.exclude_task_completion(data), {"happy-transaction-details-001": 15})
        data["weights"] = dict(bakeoff.DEFAULT_WEIGHTS)
        r = bakeoff.report(data)
        composite = {s["provider"]: round(s["composite"], 3) for s in r["scores"]}
        self.assertEqual((composite[XAI], composite[NOVA], composite[OPENAI]), (0.932, 0.921, 0.900))
        done = {s["provider"]: (s["task_completion"]["k"], s["task_completion"]["n"]) for s in r["scores"]}
        self.assertEqual((done[OPENAI], done[XAI], done[NOVA]), ((27, 27), (27, 27), (24, 27)))  # Nova: no handoff in 3 runs
        self.assertEqual(r["verdict"]["ranking"], "tied_on_quality")
        self.assertEqual(r["verdict"]["ranking_basis"], "composite_excluding_latency")
        self.assertEqual({s["standing"] for s in r["scores"]}, {"tied"})
        excl = [s["composite_excluding_latency"] for s in sorted(r["scores"], key=lambda s: s["display_order"])]
        self.assertEqual(excl, sorted(excl, reverse=True))
        latency = {row["provider"]: (row["median_ms"], row["p90_ms"]) for row in r["latency"]}
        self.assertEqual(latency[XAI], (936, 1272))
        self.assertEqual(latency[NOVA], (843, 1795))


def tooled(o, completed=True, expected=("verify_identity", "get_account_summary"), **extra):
    o.update(expected_tools=list(expected), expected_tools_observed=completed, **extra)
    return o


class TaskCompletion(unittest.TestCase):
    def test_task_completion_is_scored_only_where_tools_are_expected_and_enters_the_quality_composite(self):
        done = [tooled(obs(OPENAI, f"s{i}")) for i in range(6)]
        missed = [tooled(obs(NOVA, f"s{i}"), completed=i % 2 == 0) for i in range(6)]
        no_tools = [obs(p, "refuse") for p in (OPENAI, NOVA)]  # expects no tool: not in n, not a pass
        excluded = [tooled(obs(p, "artefact"), completed=False, task_completion_excluded="SPOKEN_FORM") for p in (OPENAI, NOVA)]
        r = bakeoff.report(payload(done + missed + no_tools + excluded))
        by = {s["provider"]: s for s in r["scores"]}
        self.assertEqual((by[OPENAI]["task_completion"]["k"], by[OPENAI]["task_completion"]["n"]), (6, 6))
        self.assertEqual((by[NOVA]["task_completion"]["k"], by[NOVA]["task_completion"]["n"]), (3, 6))
        self.assertIsNotNone(by[NOVA]["task_completion"]["lo"])
        self.assertLessEqual(by[NOVA]["task_completion"]["cluster_lo"], by[NOVA]["task_completion"]["value"])
        cov = {c["provider"]: c for c in r["coverage"]}
        self.assertEqual((cov[NOVA]["task_completion_scored"], cov[NOVA]["task_completion_excluded"]), (6, {"SPOKEN_FORM": 1}))
        self.assertEqual(by[OPENAI]["task_success"]["value"], by[NOVA]["task_success"]["value"])  # assurance alone cannot tell them apart
        self.assertGreater(by[OPENAI]["composite_excluding_latency"], by[NOVA]["composite_excluding_latency"])
        self.assertIn("task_completion", r["contrasts"][0]["dimensions"])
        self.assertIn("task_completion", [d["dimension"] for d in r["dimensions"]])
        self.assertEqual(bakeoff_stats.point_dimensions(done + missed + no_tools + excluded, [NOVA])[NOVA]["task_completion"], 0.5)

    def test_older_observations_fall_back_to_the_served_sequence_and_declared_arguments(self):
        from voice_harness.bakeoff_metrics import task_completed
        base = {"completed": True, "expected_tools": ["get_transaction_details"]}
        self.assertTrue(task_completed({**base, "metrics": {"served": True, "arguments": [{"tool": "x", "status": "correct"}]}}))
        self.assertFalse(task_completed({**base, "metrics": {"served": True, "arguments": [{"tool": "x", "status": "wrong"}]}}))
        self.assertFalse(task_completed({**base, "metrics": {"served": False}}))
        self.assertFalse(task_completed({**base, "metrics": {"served": True, "task_completed": False}}))  # the strict field wins
        self.assertTrue(task_completed({**base, "expected_tools_observed": True}))
        self.assertIsNone(task_completed({**base, "expected_tools": []}))
        self.assertIsNone(task_completed({**base, "completed": False}))


class RankingBasis(unittest.TestCase):
    def test_latency_that_is_not_comparable_does_not_order_the_rows_after_the_leader(self):
        # OPENAI leads on quality; XAI is fastest but weaker on quality than NOVA, which is slow.
        lead = marked([obs(OPENAI, f"s{i}", latencies=(900,)) for i in range(12)], "explicit")
        fast = marked([obs(XAI, f"s{i}", "fail" if i < 2 else "pass", latencies=(200,)) for i in range(12)], "explicit")
        slow = marked([obs(NOVA, f"s{i}", "fail" if i < 1 else "pass", latencies=(4000,)) for i in range(12)], "provider_vad")
        r = bakeoff.report(payload(lead + fast + slow, providers=(OPENAI, NOVA, XAI)))
        by = {s["provider"]: s for s in r["scores"]}
        self.assertFalse(r["verdict"]["latency_comparable"])
        self.assertEqual(r["verdict"]["ranking_basis"], "composite_excluding_latency")
        self.assertGreater(by[XAI]["composite"], by[NOVA]["composite"])  # latency would put XAI ahead
        self.assertGreater(by[NOVA]["composite_excluding_latency"], by[XAI]["composite_excluding_latency"])
        order = [s["provider"] for s in r["scores"]]
        self.assertEqual(order.index(NOVA) + 1, order.index(XAI))
        self.assertEqual(by[NOVA]["display_order"] + 1, by[XAI]["display_order"])
        self.assertEqual(by[XAI]["composite_order"], by[NOVA]["composite_order"] - 1)  # the composite order is kept visible
        if r["verdict"]["ranking"] == "ranked":
            self.assertEqual(by[NOVA]["rank"] + 1, by[XAI]["rank"])
        comparable = bakeoff.report(payload(marked(lead + fast + slow, "provider_vad"), providers=(OPENAI, NOVA, XAI)))
        self.assertEqual(comparable["verdict"]["ranking_basis"], "composite")


if __name__ == "__main__":
    unittest.main()
