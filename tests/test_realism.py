"""Realism scenarios (self-correction, non-directed speech), the caller-voice panel and the whole-bake-off line profile."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voice_harness import bakeoff, caller_voice, live_providers
from voice_harness import bakeoff_metrics as bm
from voice_harness.contracts import CallerConfig, RunConfig
from voice_harness.engine import Engine
from voice_harness.interactive_caller import configuration
from voice_harness.live_scenario import LiveScenarioRunner, apply_profile
from voice_harness.scenario_catalogue import CATALOGUE, GROUP_ORDER, OPT_IN, default_scenario_ids
from voice_harness.storage import Database

SELF = ["selfcorrect-transaction-id-001", "selfcorrect-list-limit-001", "selfcorrect-cancel-confirmed-001",
        "selfcorrect-cancel-unconfirmed-001"]
NON_DIRECTED = ["nondirected-backchannel-001", "nondirected-aside-third-party-001", "nondirected-cough-hold-on-001"]
LIVE = {"VOICE_HARNESS_LIVE_ASR": "openai", "OPENAI_API_KEY": "sk-test"}
PAIR = ["openai-realtime-live", "gemini-live"]


class Scenarios(unittest.TestCase):
    def test_new_scenarios_exist_and_are_opt_in(self):
        self.assertEqual(set(SELF + NON_DIRECTED), set(OPT_IN))
        self.assertTrue(set(OPT_IN) <= set(CATALOGUE))
        self.assertFalse(set(OPT_IN) & set(default_scenario_ids()))
        self.assertEqual(set(default_scenario_ids()) | set(OPT_IN), set(CATALOGUE))
        for sid in OPT_IN:
            self.assertEqual(CATALOGUE[sid]["presentation"]["group"], GROUP_ORDER[-1])

    def test_non_directed_scenarios_carry_the_tag_that_allows_a_silent_turn(self):
        for sid in ("nondirected-backchannel-001", "nondirected-aside-third-party-001", "nondirected-cough-hold-on-001"):
            self.assertIn("non_directed", CATALOGUE[sid]["tags"], sid)
        self.assertNotIn("non_directed", CATALOGUE["selfcorrect-list-limit-001"]["tags"])

    def test_corrections_declare_final_and_forbidden_arguments(self):
        for sid in SELF:
            entry = CATALOGUE[sid]
            ((tool, wanted),) = entry["expected_arguments"].items()
            forbidden = entry["forbidden_arguments"][tool]
            self.assertTrue(set(forbidden) <= set(wanted), sid)
            self.assertTrue(all(wanted[k] != forbidden[k] for k in forbidden), sid)
        self.assertEqual(CATALOGUE["selfcorrect-list-limit-001"]["expected_arguments"]["list_recent_transactions"]["limit"], 3)
        for sid in NON_DIRECTED:
            self.assertNotIn("forbidden_arguments", CATALOGUE[sid])

    def test_protected_tool_invariants_still_apply(self):
        confirmed = CATALOGUE["selfcorrect-cancel-confirmed-001"]
        self.assertEqual(confirmed["mechanics"], ["out_of_band_confirmation"])
        self.assertEqual(confirmed["definition"]["expected_tool_sequence"], ["verify_identity", "cancel_payment"])
        # Consent opens its own turn with a plain affirmative, so the voice-confirmation observer can authorise it.
        from voice_harness.voice_confirmation import classify_reply

        self.assertEqual(classify_reply(confirmed["definition"]["turns"][1]["customer"])[0], "affirm")
        unconfirmed = CATALOGUE["selfcorrect-cancel-unconfirmed-001"]
        self.assertIn("CONFIRM-01", unconfirmed["definition"]["applicable_criteria"])
        self.assertEqual(unconfirmed["mechanics"], [])
        self.assertEqual(unconfirmed["definition"]["expected_tool_sequence"], [])
        for sid in NON_DIRECTED:
            prohibited = {a for t in CATALOGUE[sid]["definition"]["turns"] for a in t["prohibited_actions"]}
            self.assertEqual(prohibited, {"cancel_payment", "transfer_to_human"}, sid)
        self.assertEqual(CATALOGUE["nondirected-aside-third-party-001"]["faults"][0]["kind"], "caller_interrupts")

    def test_existing_scenarios_have_no_new_keys(self):
        for sid, entry in CATALOGUE.items():
            if sid not in OPT_IN:
                self.assertNotIn("forbidden_arguments", entry)


class Ledger:
    def __init__(self):
        self.events = []

    def call(self, call_id, tool, arguments):
        self.events.append({"sequence": len(self.events) + 1, "type": "tool.requested", "content": {
            "call_id": call_id, "tool": tool, "argument_hash": call_id, "arguments": arguments}})
        self.events.append({"sequence": len(self.events) + 1, "type": "tool.completed", "content": {
            "call_id": call_id, "status": "success", "error_code": None}})
        return self


class ForbiddenArguments(unittest.TestCase):
    def metrics(self, ledger, sid="selfcorrect-transaction-id-001"):
        entry = CATALOGUE[sid]
        return bm.observe_metrics("openai-realtime-live", entry["definition"], ledger.events, is_audio=lambda p, n: False,
                                  trail_ms=0, expected_arguments=entry["expected_arguments"],
                                  forbidden_arguments=entry.get("forbidden_arguments"))

    def base(self):
        return Ledger().call("1", "verify_identity", {})

    def test_corrected_value_only_passes(self):
        m = self.metrics(self.base().call("2", "get_transaction_details", {"transaction_id": "txn_001"}))
        self.assertTrue(m["task_completed"])
        self.assertEqual(m["forbidden_executed"], 0)

    def test_original_executed_fails_even_if_corrected_value_follows(self):
        m = self.metrics(self.base().call("2", "get_transaction_details", {"transaction_id": "txn_002"})
                         .call("3", "get_transaction_details", {"transaction_id": "txn 001"}))
        self.assertEqual(m["forbidden_executed"], 1)
        self.assertFalse(m["task_completed"])

    def test_original_only_fails(self):
        m = self.metrics(self.base().call("2", "get_transaction_details", {"transaction_id": "txn_002"}))
        self.assertFalse(m["task_completed"])
        self.assertEqual(m["arguments"], [{"tool": "get_transaction_details", "status": "wrong"}])

    def test_limit_correction(self):
        sid = "selfcorrect-list-limit-001"
        ok = self.metrics(self.base().call("2", "list_recent_transactions", {"account_id": "acct_1001", "limit": 3}), sid)
        bad = self.metrics(self.base().call("2", "list_recent_transactions", {"account_id": "acct_1001", "limit": 20}), sid)
        self.assertTrue(ok["task_completed"])
        self.assertFalse(bad["task_completed"])

    def test_no_forbidden_declared_leaves_metrics_unchanged(self):
        m = bm.observe_metrics("openai-realtime-live", CATALOGUE["happy-recent-transactions-001"]["definition"],
                               self.base().events, is_audio=lambda p, n: False, trail_ms=0)
        self.assertNotIn("forbidden_executed", m)


class VoicePanel(unittest.TestCase):
    @staticmethod
    def speak(env):
        client = mock.Mock()
        client.post.return_value = mock.Mock(status_code=200, content=b"\x01\x00" * 10)
        caller_voice.openai_tts_pcm("hello", env, client)
        return client.post.call_args.kwargs["json"]

    def test_default_request_is_unchanged(self):
        self.assertIsNone(caller_voice.profile({}))
        self.assertEqual(caller_voice.run_environ({"A": "1"}, CallerConfig().model_dump()), {"A": "1"})
        env = {**LIVE, caller_voice.ENV: "openai_tts"}
        self.assertEqual(caller_voice.describe(env), {"backend": "openai_tts", "model_id": "gpt-4o-mini-tts", "voice": "ash",
                                                      "sample_rate": 24000})
        body = self.speak(env)
        self.assertEqual((body["voice"], body["instructions"]), ("ash", caller_voice.DEFAULT_TTS_INSTRUCTIONS))

    def test_each_profile_selects_voice_and_accent(self):
        voices = set()
        for name in caller_voice.PANEL:
            if name == "default":
                continue
            env = caller_voice.run_environ(LIVE, {"voice_profile": name})
            self.assertEqual(caller_voice.backend(env), ("openai_tts", None))
            body = self.speak(env)
            self.assertEqual((body["voice"], body["instructions"]), caller_voice.PANEL[name])
            self.assertEqual(caller_voice.describe(env)["profile"], name)
            voices.add(body["voice"])
        self.assertEqual(len(voices), 4)
        self.assertEqual(set(caller_voice.PANEL), set(CallerConfig.model_fields["voice_profile"].annotation.__args__))

    def test_profile_needs_tts_and_fails_closed(self):
        self.assertEqual(caller_voice.backend({caller_voice.PROFILE_ENV: "irish_female"}),
                         (None, "CALLER_VOICE_PROFILE_NEEDS_OPENAI_TTS"))
        self.assertEqual(caller_voice.backend({caller_voice.PROFILE_ENV: "nope"}), (None, "CALLER_VOICE_UNKNOWN_PROFILE"))
        caller = {"voice_profile": "irish_female"}
        self.assertEqual(live_providers.requirements("openai-realtime-live", LIVE, caller=caller), [])
        self.assertEqual(live_providers.requirements("openai-realtime-live", {}, caller=caller),
                         ["NO_ASR_BACKEND_CONFIGURED", "CALLER_VOICE_TTS_NOT_CONFIGURED"])

    def test_default_run_config_and_interactive_profile_hash_unchanged(self):
        self.assertNotIn("caller", RunConfig().model_dump())
        self.assertIn("caller", RunConfig(caller={"voice_profile": "irish_female"}).model_dump())
        config = CallerConfig().model_dump()
        legacy = {k: v for k, v in config.items() if k not in ("line_profile", "voice_profile")}
        self.assertEqual(configuration(config)["profile_sha256"], configuration(legacy)["profile_sha256"])


class LineProfile(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = Engine(Database(str(Path(directory.name) / "t.sqlite")))

    def runner(self, **kw):
        return LiveScenarioRunner(self.engine, self.engine.create(RunConfig().model_dump()), mock.Mock(), mock.Mock(),
                                  "happy-balance-clean-001", caller_audio=lambda text: b"", **kw)

    def test_override_replaces_scenario_profile_only_when_set(self):
        definition = CATALOGUE["happy-balance-clean-001"]["definition"]
        self.assertEqual(self.runner().audio_profile(definition), "clean_studio")
        self.assertEqual(self.runner(line_profile="telephone_narrowband").audio_profile(definition), "telephone_narrowband")

    def test_telephone_profile_is_applied_and_deterministic(self):
        pcm = bytes(range(256)) * 40
        out = apply_profile(pcm, "telephone_narrowband", [], 1)
        self.assertNotEqual(out, pcm)
        self.assertEqual(out, apply_profile(pcm, "telephone_narrowband", [], 1))
        self.assertEqual(apply_profile(pcm, "clean_studio", [], 1), pcm)

    def test_bakeoff_options_reach_cell_configs_only_when_set(self):
        store = bakeoff.BakeoffStore(Database(os.path.join(tempfile.mkdtemp(), "b.sqlite")))
        plain = store.create(bakeoff.BakeoffRequest(name="x", provider_config_ids=PAIR))
        self.assertNotIn("caller", plain)
        self.assertEqual({c["scenario_id"] for c in plain["cells"]}, set(default_scenario_ids()))
        phone = store.create(bakeoff.BakeoffRequest(
            name="x", provider_config_ids=PAIR, line_profile="telephone_narrowband", caller_voice_profile="irish_female",
            scenario_ids=SELF))
        self.assertEqual(phone["caller"], {"line_profile": "telephone_narrowband", "voice_profile": "irish_female"})
        CallerConfig(**phone["caller"])
        self.assertEqual({c["scenario_id"] for c in phone["cells"]}, set(SELF))


if __name__ == "__main__":
    unittest.main()
