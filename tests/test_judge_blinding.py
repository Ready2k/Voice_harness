"""Judges cannot identify the tested provider, and independence records disclose shared host and vendor."""

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_judges import cfg, reference_judge
from test_phase4_scenarios import ScriptedAdapter, tone

from voice_harness import live_providers
from voice_harness.adapters import AudioOut, ToolCall, TranscriptDelta, TurnEnd
from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.judges import independence, service
from voice_harness.judges.backends import JudgeConfig
from voice_harness.judges.oracle import build_oracle, identity_leaks, identity_tokens, mask_text
from voice_harness.storage import Database

CARE = "identity-bereaved-en-gb-clean-001"
# Per provider: real adapter class name, endpoint host, region, returned model, provider-format call id and the
# spoken self-identification a model might produce.
PROFILES = {
    "openai-live": ("OpenAILiveAdapter", "api.openai.com", None, "gpt-live-1", "call_Ab12Cd34", "I'm ChatGPT, made by OpenAI."),
    "openai-realtime-live": ("OpenAIRealtimeAdapter", "api.openai.com", None, "gpt-realtime-2.1", "call_Xy98", "This is GPT from OpenAI."),
    "nova-sonic-live": ("NovaSonicAdapter", "bedrock-runtime.us-east-1.amazonaws.com", "us-east-1",
                        "amazon.nova-2-5-sonic", "tooluse_9f8e", "I'm NOVA, an Amazon assistant on AWS Bedrock."),
    "xai-voice-live": ("XaiVoiceAdapter", "api.x.ai", None, "grok-voice-3.0", "call_grok1", "I'm Grok, built by xAI."),
    "gemini-live": ("GeminiLiveAdapter", "us-central1-aiplatform.googleapis.com", "us-central1", None, "function-call-1",
                    "I am Gemini, a model made by Google DeepMind."),
    "elevenlabs-live": ("ElevenLabsVoiceAdapter", "api.elevenlabs.io", None, None, "toolu_eleven",
                        "Hi, ElevenLabs agent here, voice Matthew."),
}


def fake_adapter(provider_config_id):
    name, host, region, model, call_id, intro = PROFILES[provider_config_id]

    class Fake(ScriptedAdapter):
        native_sink = None

        async def open_session(self, config):
            return {"model_id": model, "provider_session_id": "sess_" + provider_config_id}

        async def close(self, reason):
            pass

        async def send_text(self, text, respond=True):
            pass

        def connection_info(self):
            return {"endpoint_host": host, "region": region, "sdk_versions": {name + "-sdk": "1.0"}}

        async def receive(self):
            if self.native_sink:
                self.native_sink(name, provider_config_id + ".session.updated", {"model": model})
            yield ToolCall(call_id=call_id + "_v", name="verify_identity",
                           arguments={"challenge_id": "challenge_001", "answer": "blue"}, ts_ms=1)
            yield ToolCall(call_id=call_id, name="get_account_summary", arguments={"account_id": "acct_1001"}, ts_ms=2)
            yield TurnEnd(turn_id="resp_" + provider_config_id, ts_ms=3)
            yield AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id="t2", ts_ms=4)
            yield TranscriptDelta(role="assistant", text=intro + " I'm sorry for your loss.", ts_ms=5)
            yield TurnEnd(turn_id="t2", ts_ms=6)

    Fake.__name__ = Fake.__qualname__ = name
    return Fake()


def raw_identity(provider_config_id):
    """Every identifier the ledger records for this provider, from the registries and the fake session."""
    tested = live_providers.TESTED_MODELS[provider_config_id]
    name, host, region, model, call_id, _ = PROFILES[provider_config_id]
    return {provider_config_id, live_providers.LIVE_PROVIDERS[provider_config_id],
            live_providers.FAMILIES[provider_config_id], tested["provider"], tested["model_id"], tested["family"],
            name, host, model, call_id} - {None}


class LiveOracleBlinding(unittest.TestCase):
    def run_provider(self, provider_config_id):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        engine = Engine(database)
        run = engine.create(RunConfig(scenario_id=CARE, provider_config_id=provider_config_id).model_dump())
        adapter = fake_adapter(provider_config_id)
        intro = PROFILES[provider_config_id][5]
        with mock.patch.object(live_providers, "_adapter", lambda p, prompt_profile="standardised-v2": (adapter, {"tools": []})), \
                mock.patch.object(live_providers, "select_backend", lambda: ScriptedAsr(queue=[intro] * 6)), \
                mock.patch("voice_harness.adapters.openai_realtime.synth_caller_pcm", lambda text, trail_ms=0, **kw: tone(200)):
            asyncio.run(live_providers.run_live(engine, run, provider_config_id, CARE, prompt_profile="advanced-v1"))
        with database.transaction(run) as tx:
            tx.state["state"] = "evaluating"
            tx.event("orchestrator", "run.evaluating", {})
        judge = reference_judge(cfg("blind"))
        service.assess_run(database, run, tested=live_providers.TESTED_MODELS[provider_config_id], backends=[judge],
                           extra_applicable=["CARE-01", "IDENTITY-01"], while_evaluating=True)
        return database.events(run), judge.requests[0].user_payload

    def test_no_provider_identity_survives_in_the_oracle_for_any_live_provider(self):
        everyone = set().union(*(raw_identity(p) for p in PROFILES))
        for provider_config_id in live_providers.LIVE_PROVIDERS:
            with self.subTest(provider_config_id):
                events, payload = self.run_provider(provider_config_id)
                ledger = json.dumps([e["content"] for e in events])
                lowered = payload.lower()
                for token in raw_identity(provider_config_id):
                    self.assertIn(token.lower(), ledger.lower(), token)  # the identity really was in the evidence
                for token in everyone:
                    self.assertIsNone(re.search(r"(?<![a-z0-9])" + re.escape(token.lower()), lowered), token)
                self.assertEqual(identity_leaks(payload), [])
                oracle = json.loads(payload)
                types = {e["type"] for e in oracle["events"]}
                self.assertFalse(types & {"adapter.native_event", "adapter.session_ready", "cost.usage_reported"})
                binding = next(e["content"] for e in oracle["events"] if e["type"] == "run.live_binding")
                self.assertNotIn("provider", binding)
                self.assertNotIn("adapter", binding)
                # Rubric evidence survives: tool trace with linked results, transcripts, session state.
                self.assertTrue({"session.opened", "tool.requested", "mcp.result_received", "audio.output"} <= types)
                requested = [e["content"] for e in oracle["events"] if e["type"] == "tool.requested"]
                self.assertEqual({r["tool"] for r in requested}, {"verify_identity", "get_account_summary"})
                results = {e["content"]["native_envelope"]["structuredContent"]["call_id"]
                           for e in oracle["events"] if e["type"] == "mcp.result_received"}
                self.assertEqual(results, {r["call_id"] for r in requested})
                self.assertTrue(all(r["call_id"].startswith("call_") for r in requested))
                spoken = " ".join(e["content"].get("transcript") or "" for e in oracle["events"] if e["type"] == "audio.output")
                self.assertIn("sorry for your loss", spoken)
                self.assertIn("[provider]", spoken)
                record = next(e["content"] for e in events if e["type"] == "judge.independence_recorded")
                self.assertTrue(record["blinding"]["provider_identity_hidden"])


class Masking(unittest.TestCase):
    def test_review_examples_are_masked_case_insensitively(self):
        for text in ("elevenlabs_agents", "gpt-realtime-2.1", "google_gemini_live", "aws_nova_sonic", "NOVA", "OpenAI",
                     "x.ai", "XaiVoiceAdapter", "us-central1-aiplatform.googleapis.com", "Matthew", "marin", "Ara"):
            self.assertEqual(identity_leaks(mask_text(text)), [], text)
            self.assertNotEqual(identity_leaks(text), [], text)
        self.assertEqual(mask_text("eleven pounds for Alexander at the bank"), "eleven pounds for Alexander at the bank")

    def test_registry_and_adapter_defaults_are_covered_automatically(self):
        tokens = identity_tokens()
        for pid, tested in live_providers.TESTED_MODELS.items():
            for value in (pid, tested["model_id"], tested["family"], live_providers.FAMILIES[pid]):
                self.assertEqual(identity_leaks(mask_text(value)), [], value)
        source = "".join(p.read_text() for p in Path(live_providers.__file__).parent.joinpath("adapters").glob("*.py"))
        for voice in re.findall(r'get\("voice", "([^"]+)"\)', source):
            self.assertIn(voice.lower(), tokens["voice"])

    def test_blinding_claim_is_measured(self):
        oracle = build_oracle([{"event_id": "e1", "sequence": 1, "source": "provider", "type": "x", "content": {
            "note": "Gemini said hi"}}], scenario=None, fixture={}, policy_version="p", criteria=[])
        self.assertEqual(identity_leaks(oracle.payload), [])
        self.assertFalse(independence.measured_blinding(["gemini"])["provider_identity_hidden"])
        self.assertTrue(independence.measured_blinding([])["provider_identity_hidden"])
        unmeasured = independence.assess({"provider": "openai", "model_id": "m", "family": "f"}, [], required_judges=1,
                                          material_benchmark=False, order_seed=0, run_session_id="s")
        self.assertFalse(unmeasured["blinding"]["provider_identity_hidden"])


class HostAndVendor(unittest.TestCase):
    NOVA = live_providers.TESTED_MODELS["nova-sonic-live"]
    OPENAI = live_providers.TESTED_MODELS["openai-realtime-live"]

    def record(self, tested, *configs, material=False):
        out = independence.assess(tested, list(configs), required_judges=1, material_benchmark=material,
                                  order_seed=0, run_session_id="run")
        return out, {j["judge_id"]: j for j in out["judges"]}

    def test_bedrock_claude_judge_shares_aws_host_with_nova_but_not_its_vendor(self):
        claude = JudgeConfig("bedrock-claude", "bedrock", "aws_bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                             "anthropic")
        out, by = self.record(self.NOVA, claude)
        self.assertEqual((by["bedrock-claude"]["host"], by["bedrock-claude"]["vendor"]), ("aws", "anthropic"))
        self.assertEqual((out["tested"]["host"], out["tested"]["vendor"]), ("aws", "amazon"))
        self.assertTrue(by["bedrock-claude"]["eligible"])
        self.assertIn("SAME_HOST_AS_TESTED_MODEL", by["bedrock-claude"]["disclosures"])
        self.assertIn("SAME_PROVIDER_AS_TESTED_MODEL", by["bedrock-claude"]["disclosures"])
        self.assertNotIn("SAME_VENDOR_AS_TESTED_MODEL", by["bedrock-claude"]["disclosures"])
        self.assertTrue(out["cross_family_judge_present"])

    def test_same_vendor_judge_is_disclosed_and_excluded_from_material_benchmarks(self):
        gpt = JudgeConfig("openai-judge", "openai", "openai", "gpt-6-luna", "gpt")
        nova_judge = JudgeConfig("bedrock-nova", "bedrock", "aws_bedrock", "us.amazon.nova-pro-v1:0", "amazon")
        out, by = self.record(self.OPENAI, gpt)
        self.assertTrue(by["openai-judge"]["eligible"])
        self.assertIn("SAME_VENDOR_AS_TESTED_MODEL", by["openai-judge"]["disclosures"])
        self.assertFalse(out["cross_family_judge_present"])
        out, by = self.record(self.OPENAI, gpt, material=True)
        self.assertFalse(by["openai-judge"]["eligible"])
        self.assertEqual(by["openai-judge"]["reason_codes"], ["SAME_VENDOR_IN_MATERIAL_BENCHMARK"])
        self.assertEqual(out["independent_judge_count"], 0)
        _, by = self.record(self.NOVA, nova_judge)
        self.assertIn("SAME_VENDOR_AS_TESTED_MODEL", by["bedrock-nova"]["disclosures"])

    def test_tested_model_is_matched_by_vendor_not_raw_label(self):
        self_judge = JudgeConfig("nova-self", "bedrock", "aws_bedrock", self.NOVA["model_id"], "amazon")
        _, by = self.record(self.NOVA, self_judge)
        self.assertEqual(by["nova-self"]["reason_codes"], ["JUDGE_IS_TESTED_MODEL"])


if __name__ == "__main__":
    unittest.main()
