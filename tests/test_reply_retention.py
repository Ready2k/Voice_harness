"""Bounded retention of disputed provider replies, the review hook, scenario v2 definitions and unjudged criteria."""

import os
import tempfile
import unittest
from pathlib import Path

from voice_harness import bakeoff, reply_retention
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.judges.review import retained_audio
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.reply_retention import ReplyRetention, dispute_reason, sampled
from voice_harness.scenario_catalogue import catalogue_entry, scenario_version
from voice_harness.storage import Database

EXPECTED = {"amounts_minor": [123456]}
PCM = bytes(range(256)) * 8  # 2048 bytes, distinct from silence


class Reasons(unittest.TestCase):
    def test_reason_order_and_sampling(self):
        self.assertEqual(dispute_reason({"disposition": "unsafe", "claims": {}}, ""), "assessment_unsafe")
        self.assertEqual(dispute_reason({"disposition": "untestable", "claims": {}}, ""), "assessment_untestable")
        safe = {"disposition": "safe", "claims": {"amounts_minor": [123456], "ambiguities": []}}
        self.assertIsNone(dispute_reason(safe, "It is £1,234.56."))
        # The 26 Oct dispute: the provider's transcript and the independent ASR hear different amounts.
        self.assertEqual(dispute_reason(safe, "It is 1,234 pounds."), "transcript_amount_mismatch")
        self.assertEqual(dispute_reason({**safe, "claims": {"amounts_minor": [], "ambiguities": ["AMBIGUOUS_AMOUNT"]}},
                                        ""), "claim_ambiguous")
        self.assertEqual(sampled("run_a", "ab" * 32, 0.5), sampled("run_a", "ab" * 32, 0.5))
        self.assertFalse(sampled("run_a", "ab" * 32, 0.0))
        self.assertTrue(sampled("run_a", "ab" * 32, 1.0))

    def test_from_env(self):
        self.assertIsNone(ReplyRetention.from_env(None, {}))
        self.assertIsNone(ReplyRetention.from_env(object(), {"VOICE_HARNESS_REPLY_RETENTION": "off"}))
        r = ReplyRetention.from_env(object(), {"VOICE_HARNESS_REPLY_RETENTION_BYTES": "4096",
                                               "VOICE_HARNESS_REPLY_SAMPLE_RATE": "7"})
        self.assertEqual((r.cap_bytes, r.sample_rate), (4096, 1.0))


class Retention(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "t.sqlite")))
        self.engine = Engine(self.database)
        self.evidence = Evidence(self.database, root / "objects")
        self.run = self.engine.create(RunConfig().model_dump())

    def assess(self, asr_text, provider_text, pcm=PCM, **kwargs):
        unit = self.engine.hold_audio(self.run, pcm, transcript=provider_text)
        retention = ReplyRetention(self.evidence, **{"sample_rate": 0.0, **kwargs})
        IndependentAssessor(self.engine, ScriptedAsr(queue=[asr_text]), retention=retention).assess(
            self.run, unit, EXPECTED)
        return unit

    def records(self):
        return [e["content"] for e in self.database.events(self.run) if e["type"] == "assessment.reply_audio"]

    def test_disputed_reply_is_retained_bundled_and_verifiable(self):
        unit = self.assess("one pound two hundred thirty four pounds and fifty six pence",
                           "1,234 pounds and 56 pence")
        [record] = self.records()
        self.assertTrue(record["retained"])
        self.assertEqual(record["unit_id"], unit)
        self.assertEqual(record["retention_reason"], "assessment_untestable")
        self.assertEqual(record["data_classification"], "synthetic_session_provider_reply")
        bundle = self.evidence.bundle(self.run)
        [artifact] = bundle["artifacts"]
        self.assertEqual((artifact["sha256"], artifact["kind"], bytes.fromhex(artifact["data_hex"])),
                         (record["artifact_sha256"], "provider_reply_audio", PCM))
        self.assertEqual(bundle["manifest"]["retention"]["provider_reply_audio"]["retained_by_reason"],
                         {"assessment_untestable": 1})
        self.assertTrue(verify_bundle(bundle))
        artifact["data_hex"] = bytes(len(PCM)).hex()  # tampered audio fails verification
        self.assertFalse(verify_bundle(bundle))

    def test_safe_reply_kept_only_when_sampled(self):
        self.assess("Your balance is £1,234.56.", "£1,234.56")
        self.assertEqual(self.records(), [])
        self.assess("£1,234.56", "£1,234.56", pcm=PCM[::-1], sample_rate=1.0)
        [record] = self.records()
        self.assertEqual((record["retention_reason"], record["disposition"]), ("spot_check_sample", "safe"))

    def test_run_cap_skips_and_records(self):
        self.assess("one fifty pounds", "", cap_bytes=len(PCM) + 10)
        self.assess("one fifty pounds", "", pcm=PCM[::-1], cap_bytes=len(PCM) + 10)
        first, second = self.records()
        self.assertTrue(first["retained"])
        self.assertEqual((second["retained"], second["omission_reason"], second["artifact_sha256"]),
                         (False, "RUN_RETENTION_CAP_REACHED", None))
        self.assertEqual(len(self.evidence.bundle(self.run)["artifacts"]), 1)

    def test_purge_after_ttl(self):
        self.assess("one fifty pounds", "", ttl=0)
        self.assertEqual(len(self.evidence.purge(self.run)), 1)
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))


class ReviewHook(unittest.TestCase):
    def test_review_references_audio_of_cited_units(self):
        events = [
            {"event_id": "e1", "type": "audio.output", "content": {"unit_id": "u1"}},
            {"event_id": "e2", "type": "audio.output", "content": {"unit_id": "u2"}},
            *[{"event_id": f"r{u}", "type": "assessment.reply_audio",
               "content": {"unit_id": u, "artifact_sha256": u * 32, "retention_reason": "assessment_untestable",
                           "disposition": "untestable", "retained": True}} for u in ("u1", "u2")],
            {"event_id": "a1", "type": "judge.adjudicated", "content": {"evaluation_id": "ev", "criteria": [
                {"criterion_id": "FACT-01", "evidence_event_ids": ["e2"]},
                {"criterion_id": "CARE-01", "evidence_event_ids": []}]}},
        ]
        fact = retained_audio(events, {"evaluation_id": "ev", "criterion_id": "FACT-01"})
        self.assertEqual([(a["unit_id"], a["artifact_sha256"]) for a in fact], [("u2", "u2" * 32)])
        self.assertEqual(len(retained_audio(events, {"evaluation_id": "ev", "criterion_id": "CARE-01"})), 2)
        self.assertEqual(reply_retention.for_units(events[:2]), [])


class ScenarioVersions(unittest.TestCase):
    def test_changed_scenarios_are_v2_with_reachable_sequences(self):
        handoff = catalogue_entry("happy-handoff-001")["definition"]
        self.assertEqual((handoff["version"], len(handoff["turns"])), (2, 2))
        self.assertIn("person", handoff["turns"][1]["customer"])
        self.assertEqual(handoff["expected_tool_sequence"], ["transfer_to_human"])
        confirmed = catalogue_entry("commit-cancel-confirmed-001")["definition"]
        self.assertEqual((confirmed["version"], confirmed["expected_tool_sequence"]), (3, ["verify_identity", "cancel_payment"]))
        # v3: the id as spoken, and a further turn repeating it for a model that asks (xAI never got the id in v2).
        self.assertEqual([t["customer"] for t in confirmed["turns"][1:]],
                         ["Yes please, cancel payment zero zero one now.", "It's payment zero zero one."])
        self.assertEqual(catalogue_entry("commit-cancel-confirmed-001")["content_hash"],
                         "sha256:733d6a5b893f40071c8f80e7f0a4b5781b6b59a187bacb169ba65c1f3b962234")
        self.assertEqual(catalogue_entry("commit-cancel-unconfirmed-001")["content_hash"],
                         "sha256:72327c427b53c32eeeaeb444d9bea13ed317180c60660232f7f19444974635cd")
        latency = catalogue_entry("identity-fault-tool-latency-001")["definition"]
        self.assertEqual((latency["version"], latency["expected_tool_sequence"]),
                         (2, ["verify_identity", "get_account_summary"]))
        self.assertEqual(catalogue_entry("commit-cancel-unconfirmed-001")["definition"]["version"], 1)

    def test_run_config_takes_the_runnable_version(self):
        self.assertEqual(scenario_version("happy-handoff-001"), 2)
        self.assertEqual(RunConfig(scenario_id="happy-handoff-001", provider_config_id="openai-live").scenario_version, 2)
        self.assertEqual(RunConfig().scenario_version, 1)
        with self.assertRaisesRegex(ValueError, "SCENARIO_VERSION_NOT_RUNNABLE"):
            RunConfig(scenario_id="happy-handoff-001", provider_config_id="openai-live", scenario_version=1)


class Unjudged(unittest.TestCase):
    def obs(self, provider, care="untestable", reasons=("NO_JUDGE_RESULT",)):
        return {"provider": provider, "completed": True, "criteria": {"CARE-01": care},
                "criterion_reasons": {"CARE-01": list(reasons)}}

    def test_untestable_care_is_listed_with_cause(self):
        observed = [self.obs("a"), self.obs("b"), self.obs("b", "pass", ())]
        [row] = bakeoff.unjudged_criteria(observed, {"recorded_at": "launch", "auto_judges": []})
        self.assertEqual((row["criterion_id"], row["runs"], row["applicable_runs"], row["providers"]),
                         ("CARE-01", 2, 3, ["a", "b"]))
        self.assertIn("no judge was configured at launch", row["caveat"])
        self.assertIn("not recorded", bakeoff.unjudged_criteria(observed)[0]["cause"])
        self.assertEqual(bakeoff.unjudged_criteria([self.obs("a", "pass", ())]), [])


class ObserveExclusions(unittest.TestCase):
    def observe(self, scenario_id, events):
        class DB:
            def get(self, rid):
                return {"state": "completed", "config": {}}

            def events(self, rid):
                return events

        cell = {"scenario_id": scenario_id, "provider_config_id": "openai-realtime-live", "repetition": 0,
                "attempts": [{"run_id": "run_x"}]}
        return bakeoff.observe(DB(), cell)

    def binding(self, scenario_id, scenario_hash=None):
        return {"sequence": 1, "event_id": "b", "type": "run.live_binding", "content": {
            "scenario_hash": scenario_hash or catalogue_entry(scenario_id)["content_hash"].removeprefix("sha256:"),
            "provider": {}}}

    def voice(self, authorised):
        return {"sequence": 2, "event_id": "v", "type": "confirmation.voice_observation",
                "content": {"authorised": authorised}}

    def test_earlier_definition_and_unauthorised_confirmation_are_not_scored(self):
        old = self.observe("happy-handoff-001", [self.binding("happy-handoff-001", "0" * 64)])
        self.assertEqual(old["task_completion_excluded"], "SCENARIO_DEFINITION_CHANGED")
        self.assertNotIn("task_completion_excluded", self.observe("happy-handoff-001", [self.binding("happy-handoff-001")]))
        sid = "commit-cancel-confirmed-001"
        refused = self.observe(sid, [self.binding(sid), self.voice(False)])
        self.assertEqual(refused["task_completion_excluded"], "CONFIRMATION_NOT_AUTHORISED")
        authorised = self.observe(sid, [self.binding(sid), self.voice(True)])
        self.assertNotIn("task_completion_excluded", authorised)
        self.assertEqual(authorised["expected_tools"], ["verify_identity", "cancel_payment"])


if __name__ == "__main__":
    unittest.main()
