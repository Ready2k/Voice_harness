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
        original = {s["provider"]: s["composite"] for s in bakeoff.report(data)["scores"]}
        self.assertEqual(original, {s["provider"]: s["composite"] for s in saved["report"]["scores"]})
        module.correct(data)
        r = bakeoff.report(data)
        composite = {s["provider"]: round(s["composite"], 3) for s in r["scores"]}
        self.assertEqual((composite[XAI], composite[NOVA]), (0.920, 0.918))
        self.assertEqual(r["verdict"]["ranking"], "tied_on_quality")
        self.assertEqual({s["standing"] for s in r["scores"]}, {"tied"})
        latency = {row["provider"]: (row["median_ms"], row["p90_ms"]) for row in r["latency"]}
        self.assertEqual(latency[XAI], (936, 1272))
        self.assertEqual(latency[NOVA], (843, 1795))


if __name__ == "__main__":
    unittest.main()
