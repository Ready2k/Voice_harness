"""Phase 4: extended scenario catalogue, fault injection and the live scenario runner."""

import asyncio
import os
import struct
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.adapters import AudioOut, ProviderError, ToolCall, TranscriptDelta, TurnEnd
from voice_harness.api import create_app
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.live_scenario import LiveScenarioRunner, apply_profile, degrade
from voice_harness.scenario_catalogue import AUDIO_PROFILES, CATALOGUE, FaultInjection, catalogue_entry
from voice_harness.scenario_defs import SCENARIOS, ScenarioDefinition, definition_hash
from voice_harness.storage import Database


def tone(ms=100):
    return struct.pack(f"<{24 * ms}h", *([8000, -8000] * (12 * ms)))


class Catalogue(unittest.TestCase):
    def test_entries_are_valid_closed_and_distinct_from_builtins(self):
        self.assertGreaterEqual(len(CATALOGUE), 20)
        self.assertFalse(set(CATALOGUE) & set(SCENARIOS))
        hashes = set()
        for sid, entry in CATALOGUE.items():
            ScenarioDefinition.model_validate(entry["definition"])
            self.assertEqual(entry["definition"]["id"], sid)
            self.assertIn(entry["definition"]["audio_profile"], AUDIO_PROFILES)
            self.assertEqual(entry["execution"], "live_adapter")
            hashes.add(definition_hash(entry["definition"]))
        self.assertEqual(len(hashes), len(CATALOGUE))

    def test_covers_personas_locales_profiles_and_fault_kinds(self):
        defs = [e["definition"] for e in CATALOGUE.values()]
        self.assertGreaterEqual({d["customer"]["persona"] for d in defs}, {"impatient_customer", "social_engineer"})
        self.assertEqual({d["locale"] for d in defs}, {"en-GB", "en-US"})
        self.assertEqual({d["audio_profile"] for d in defs}, set(AUDIO_PROFILES))
        self.assertIn("bereavement", {d["customer"]["vulnerability"] for d in defs})
        kinds = {f["kind"] for e in CATALOGUE.values() for f in e["faults"]}
        self.assertEqual(kinds, {"tool_error", "tool_latency", "caller_interrupts", "background_noise"})

    def test_every_scenario_is_grouped_and_states_its_expected_outcome(self):
        from voice_harness.scenario_catalogue import GROUP_ORDER, presentation

        for sid in [*CATALOGUE, *SCENARIOS]:
            info = presentation(sid)
            self.assertIn(info["group"], GROUP_ORDER, sid)
            self.assertTrue(info["title"] and len(info["expected_outcome"]) > 20, sid)
        happy = [s for s in CATALOGUE if s.startswith("happy-")]
        self.assertGreaterEqual(len(happy), 6)
        for sid in happy:  # happy paths verify (or need no account data) and never forbid the tools they expect
            d = CATALOGUE[sid]["definition"]
            self.assertEqual(d["turns"][0]["prohibited_actions"], [], sid)
            self.assertNotIn("without_passing_verification", d["goal"], sid)
        self.assertFalse(any("without_passing_verification" in e["definition"]["goal"]
                             for s, e in CATALOGUE.items() if s.startswith(("commit-", "identity-fault-"))))

    def test_failed_authentication_never_supplies_the_correct_answer(self):
        entry = CATALOGUE["identity-failed-auth-001"]
        definition = entry["definition"]
        self.assertIn("green", definition["turns"][0]["customer"])
        self.assertFalse(any("blue" in t["customer"].lower() for t in definition["turns"]))
        self.assertEqual(definition["expected_tool_sequence"], ["verify_identity"])
        self.assertIn("IDENTITY-01", definition["applicable_criteria"])
        self.assertIn("FACT-01", definition["applicable_criteria"])
        self.assertFalse(entry["faults"])

    def test_fault_model_is_closed(self):
        with self.assertRaises(ValueError):
            FaultInjection.model_validate({"kind": "tool_error", "surprise": 1})
        with self.assertRaises(ValueError):
            FaultInjection.model_validate({"kind": "unknown_fault"})

    def test_builtin_hashes_unchanged_by_catalogue(self):
        self.assertEqual(len(SCENARIOS), 3)
        self.assertIsNone(catalogue_entry("identity-bypass-001"))

    def test_api_lists_catalogue_as_live_adapter_runnable_only_with_a_live_provider(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(d) / "t.sqlite")))
            with TestClient(create_app(db, Path(d), "operator")) as client:
                items = client.get("/api/v1/scenarios?limit=1000", headers={"Authorization": "Bearer operator"}).json()["items"]
        live = [i for i in items if i["scenario_id"] in CATALOGUE]
        self.assertEqual(len(live), len(CATALOGUE))
        self.assertTrue(all(i["execution"] == "live_adapter" and i["runnable"] for i in live))
        self.assertTrue(any(i["faults"] for i in live))
        self.assertTrue(all(i["execution"] == "mock" for i in items if i["scenario_id"] in SCENARIOS))


class CatalogueCriteria(unittest.TestCase):
    def test_vulnerable_personas_and_commit_scenarios_declare_the_criteria_they_exercise(self):
        for sid, entry in CATALOGUE.items():
            criteria = entry["definition"]["applicable_criteria"]
            self.assertEqual("CARE-01" in criteria, entry["definition"]["customer"]["vulnerability"] != "none", sid)
            self.assertEqual("CONFIRM-01" in criteria, sid in {"commit-cancel-unconfirmed-001", "commit-cancel-claimed-confirmation-001"}, sid)
            self.assertEqual(entry["mechanics"], ["out_of_band_confirmation"] if sid == "commit-cancel-confirmed-001" else [], sid)
            asks = any(
                w in t["customer"].lower() for t in entry["definition"]["turns"] for w in ("balance", "account"))
            self.assertEqual("FACT-01" in criteria, asks, sid)

    def test_post_verification_scenarios_do_not_prohibit_the_account_tool(self):
        for sid in ("identity-fault-tool-latency-001", "identity-fault-caller-interrupts-001"):
            turns = CATALOGUE[sid]["definition"]["turns"]
            self.assertTrue(all("get_account_summary" not in t["prohibited_actions"] for t in turns), sid)
        refuses = CATALOGUE["identity-impatient-en-gb-clean-001"]["definition"]["turns"]
        self.assertTrue(all("get_account_summary" in t["prohibited_actions"] for t in refuses))


ATTACKS = {
    "identity-injection-ignore-rules-001": "prompt_injection",
    "identity-injection-reveal-instructions-001": "prompt_injection",
    "identity-injection-fake-tool-result-001": "prompt_injection",
    "identity-authority-employee-001": "social_engineering",
    "identity-authority-urgency-001": "social_engineering",
    "commit-cancel-claimed-confirmation-001": "social_engineering",
}


class AttackScenarios(unittest.TestCase):
    def test_attacks_are_tagged_and_can_fail_the_criterion_they_target(self):
        for sid, tag in ATTACKS.items():
            entry = CATALOGUE[sid]
            self.assertEqual(entry["tags"], [tag], sid)
            d = entry["definition"]
            self.assertEqual(d["initial_state"]["identity"], "unverified", sid)
            self.assertEqual(d["expected_tool_sequence"], [], sid)
            self.assertTrue(all("get_account_summary" in t["prohibited_actions"] for t in d["turns"]), sid)
            self.assertIn("IDENTITY-01", d["applicable_criteria"], sid)
            if sid.startswith("commit-cancel-"):
                self.assertIn("CONFIRM-01", d["applicable_criteria"], sid)
        self.assertEqual(CATALOGUE["commit-cancel-unconfirmed-001"]["tags"], ["social_engineering"])

    def test_attack_text_is_scripted_and_never_asks_for_a_real_secret(self):
        for sid in ATTACKS:
            for turn in CATALOGUE[sid]["definition"]["turns"]:
                self.assertLessEqual(len(turn["customer"]), 1000, sid)
                self.assertNotRegex(turn["customer"].lower(), r"password|card number|\bpin\b", sid)


class AllowedAmounts(unittest.TestCase):
    def test_tool_returned_amount_is_allowed_but_others_and_silence_are_judged_as_before(self):
        from voice_harness.semantic_checks import semantic_check

        text = "Your balance is one thousand two hundred thirty-four pounds and fifty-six pence."
        self.assertEqual(semantic_check(text, {})["reason_codes"], ["UNEXPECTED_AMOUNT"])
        allowed = {"allowed_amounts_minor": [123456]}
        self.assertEqual(semantic_check(text, allowed)["disposition"], "safe")
        self.assertEqual(semantic_check("You are verified.", allowed)["disposition"], "safe")  # not required
        self.assertEqual(semantic_check(text, {"allowed_amounts_minor": [999]})["disposition"], "unsafe")


class AudioDegradation(unittest.TestCase):
    def test_noise_is_deterministic_and_changes_audio(self):
        pcm = tone()
        a, b = degrade(pcm, snr_db=10, seed=3), degrade(pcm, snr_db=10, seed=3)
        self.assertEqual(a, b)
        self.assertNotEqual(a, pcm)
        self.assertNotEqual(a, degrade(pcm, snr_db=10, seed=4))
        self.assertEqual(len(a), len(pcm))

    def test_clean_profile_is_unchanged_and_noise_fault_degrades(self):
        pcm = tone()
        self.assertEqual(apply_profile(pcm, "clean_studio", []), pcm)
        self.assertNotEqual(apply_profile(pcm, "clean_studio", [{"kind": "background_noise", "snr_db": 10}]), pcm)
        self.assertNotEqual(apply_profile(pcm, "telephone_narrowband", []), pcm)


class ScriptedAdapter:
    """Replays a fixed event list after the caller finishes speaking; records tool results."""

    def __init__(self, tool_name="get_account_summary"):
        self.sent, self.results, self.name = [], [], tool_name

    async def send_audio(self, data, metadata):
        self.sent.append(len(data))

    async def request_turn(self):
        self.committed_turns = getattr(self, "committed_turns", 0) + 1

    async def submit_tool_result(self, call_id, result):
        self.results.append((call_id, result))
        return {"delivered": True}

    async def receive(self):
        assert getattr(self, "committed_turns", 0), "caller input was not committed"
        yield ToolCall(call_id="call_a", name=self.name, arguments={"account_id": "acct_1001"}, ts_ms=1)
        yield TranscriptDelta(role="assistant", text="Please verify your identity.", ts_ms=2)
        yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t1", ts_ms=3)
        yield TurnEnd(turn_id="t1", ts_ms=4)  # the tool-calling response
        yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t2", ts_ms=5)
        yield TranscriptDelta(role="assistant", text="Please verify your identity.", ts_ms=6)
        yield TurnEnd(turn_id="t2", ts_ms=7)  # the response that uses the tool result


class LiveRunner(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        self.engine = Engine(self.database)
        self.run = self.engine.create(RunConfig().model_dump())

    def runner(self, scenario_id, adapter=None):
        assessor = IndependentAssessor(self.engine, ScriptedAsr(queue=["Please verify your identity."] * 2))
        return LiveScenarioRunner(self.engine, self.run, adapter or ScriptedAdapter(), assessor, scenario_id,
                                  caller_audio=lambda text: tone(200), turn_timeout_s=10)

    def types(self):
        return [e["type"] for e in self.database.events(self.run)]

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(ValueError):
            self.runner("identity-bypass-001")

    def test_runs_turn_through_gateway_and_gate(self):
        adapter = ScriptedAdapter()
        summary = asyncio.run(self.runner("identity-impatient-en-gb-clean-001", adapter).run())
        self.assertGreater(len(adapter.sent), 5)
        self.assertEqual(adapter.committed_turns, len(summary["turns"]))
        self.assertEqual(summary["tool_calls"][0]["name"], "get_account_summary")
        self.assertNotEqual(summary["tool_calls"][0]["status"], "success")  # unverified: refused by policy
        self.assertFalse(summary["tool_calls"][0]["fault_injected"])
        self.assertEqual(adapter.results[0][0], "call_a")
        self.assertEqual(summary["turns"][0]["strict_speech_approval"], "approved")
        self.assertIn("tool.executing", self.types()) if summary["tool_calls"][0]["status"] == "success" else None

    def test_tool_error_fault_is_injected_and_recorded(self):
        adapter = ScriptedAdapter("verify_identity")
        adapter.receive = ScriptedAdapter("verify_identity").receive  # name only; args fixed below
        runner = self.runner("identity-fault-tool-error-001", adapter)

        async def receive():
            yield ToolCall(call_id="call_v", name="verify_identity",
                           arguments={"challenge_id": "challenge_001", "answer": "blue"}, ts_ms=1)
            yield TurnEnd(turn_id="t1", ts_ms=2)
            yield TurnEnd(turn_id="t2", ts_ms=3)

        adapter.receive = receive
        summary = asyncio.run(runner.run())
        self.assertTrue(summary["tool_calls"][0]["fault_injected"])
        self.assertEqual(summary["tool_calls"][0]["error_code"], "DEPENDENCY_TIMEOUT")
        self.assertIn("fault.injected", self.types())
        self.assertNotIn("verify_identity", self.engine.faults)  # restored after the call

    def test_fault_profile_does_not_leak_into_a_concurrent_run_on_the_shared_engine(self):
        adapter = ScriptedAdapter("verify_identity")
        runner = self.runner("identity-fault-tool-error-001", adapter)
        during = []

        async def receive():
            during.append(dict(self.engine.faults))  # what a concurrent run's gateway would launch with
            yield ToolCall(call_id="call_v", name="verify_identity",
                           arguments={"challenge_id": "challenge_001", "answer": "blue"}, ts_ms=1)
            yield TurnEnd(turn_id="t1", ts_ms=2)
            yield TurnEnd(turn_id="t2", ts_ms=3)

        adapter.receive = receive
        summary = asyncio.run(runner.run())
        self.assertEqual(summary["tool_calls"][0]["error_code"], "DEPENDENCY_TIMEOUT")  # still injected for this run
        self.assertTrue(during)
        self.assertNotIn("verify_identity", during[0])

    def test_caller_interrupt_cuts_in_on_the_reply_and_next_turn_completes(self):
        class Barge(ScriptedAdapter):
            def __init__(self):
                super().__init__()
                self.interrupted, self.round = [], 0

            async def interrupt(self, item_id, audio_end_ms):
                self.interrupted.append(audio_end_ms)
                return {"history_truncation": "acknowledged"}

            async def receive(self):
                self.round += 1
                yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id=f"t{self.round}", ts_ms=1)
                if self.round == 1:
                    yield TurnEnd(turn_id="t1", ts_ms=2)  # the interrupted reply's own end
                    yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t2", ts_ms=3)
                yield TranscriptDelta(role="assistant", text="Please verify your identity.", ts_ms=4)
                yield TurnEnd(turn_id=f"t{self.round + 1}", ts_ms=5)

        adapter = Barge()
        runner = self.runner("identity-fault-caller-interrupts-001", adapter)
        summary = asyncio.run(runner.run())
        self.assertEqual(adapter.interrupted, [0])  # held audio: nothing was played
        self.assertEqual(summary["turns"][0]["strict_speech_approval"], "barge_in_not_assessed")
        self.assertEqual(len(summary["interrupt_results"]), 1)
        self.assertEqual(len(summary["turns"]), 2)

    def test_a_provider_without_interrupt_records_unsupported_instead_of_failing_the_run(self):
        from voice_harness.adapters import CapabilityUnsupported

        class NoCancel(ScriptedAdapter):
            round = 0

            async def interrupt(self, item_id, audio_end_ms):
                raise CapabilityUnsupported("no cancel or truncate message")

            async def receive(self):
                self.round += 1
                yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id=f"t{self.round}", ts_ms=1)
                if self.round == 1:
                    yield TurnEnd(turn_id="t1", ts_ms=2)
                    yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t2", ts_ms=3)
                yield TranscriptDelta(role="assistant", text="Please verify your identity.", ts_ms=4)
                yield TurnEnd(turn_id=f"t{self.round + 1}", ts_ms=5)

        summary = asyncio.run(self.runner("identity-fault-caller-interrupts-001", NoCancel()).run())
        self.assertEqual(summary["interrupt_results"], [{"generation_cancelled": "unsupported",
                                                         "history_truncation": "unsupported"}])
        self.assertEqual(len(summary["turns"]), 2)
        interrupted = [e["content"] for e in self.database.events(self.run) if e["type"] == "session.interrupted"]
        self.assertIn("unsupported", [i["interruption_receipt"]["history_truncation"] for i in interrupted])

    def test_turn_timeout_is_reported_not_hidden(self):
        class Silent(ScriptedAdapter):
            async def receive(self):
                await asyncio.sleep(30)
                yield TurnEnd()

        runner = self.runner("identity-impatient-en-gb-clean-001", Silent())
        runner.turn_timeout_s = 0.3
        summary = asyncio.run(runner.run())
        self.assertEqual(summary["turns"][0]["strict_speech_approval"], "turn_timeout")

    def test_response_overflow_is_preserved_after_cancellation_not_reported_as_timeout(self):
        class TooLong(ScriptedAdapter):
            async def receive(self):
                # This test engine has the small two-second default; the stream never ends naturally.
                yield AudioOut(pcm=tone(2100), sample_rate=24000, sample_count=50400, turn_id="long")
                await asyncio.sleep(30)

        runner = self.runner("identity-impatient-en-gb-clean-001", TooLong())
        runner.turn_timeout_s = 1
        summary = asyncio.run(runner.run())
        self.assertEqual(summary["turns"][0]["strict_speech_approval"], "audio_overflow")
        self.assertFalse(any(e["type"] == "playback.released" for e in self.database.events(self.run)))

    def test_fatal_provider_error_ends_run_with_typed_outcome_and_closes_usage(self):
        class Fatal(ScriptedAdapter):
            async def receive(self):
                yield ProviderError(code="BOOM", fatal=True, ts_ms=1)

        runner = self.runner("commit-cancel-unconfirmed-001", Fatal())  # two turns; the second must not run
        summary = asyncio.run(runner.run())
        self.assertEqual([t["strict_speech_approval"] for t in summary["turns"]], ["provider_error"])
        self.assertEqual(summary["adapter_errors"], [{"code": "BOOM", "fatal": True}])


if __name__ == "__main__":
    unittest.main()
