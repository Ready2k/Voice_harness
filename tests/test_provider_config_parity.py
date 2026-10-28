"""Provider configuration parity: equal sampling policy, recorded asymmetries and their disclosure in the report."""
import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voice_harness import bakeoff, bakeoff_disclosure, live_providers
from voice_harness.adapters import elevenlabs_agent as agent
from voice_harness.adapters.nova_sonic import NovaSonicAdapter
from voice_harness.event_contracts import AdapterNativeEvent
from voice_harness.live_scenario import audio_bytes
from voice_harness.target_prompts import delta

B64 = base64.b64encode(b"\x01\x00" * 300).decode()  # 600 bytes


class Transport:
    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, msg): self.sent.append(msg)
    async def close(self): pass

    async def recv(self):
        await asyncio.sleep(0.01)
        return None


class Backend:
    name, model = "openai_audio_transcription", "gpt-transcribe"


class SamplingPolicy(unittest.IsolatedAsyncioTestCase):
    async def test_nova_sends_no_temperature_or_top_p(self):
        tr, a = Transport(), NovaSonicAdapter(pump_interval_s=0.001)
        await a.open_session({"session_id": "S", "transport": tr, "system_prompt": "x"})
        await a.close("done")
        start = next(m["event"]["sessionStart"] for m in tr.sent if "sessionStart" in m.get("event", {}))
        self.assertEqual(start["inferenceConfiguration"], {"maxTokens": 1024})

    def test_binding_records_policy_and_values_sent(self):
        nova = live_providers.sampling_binding("nova-sonic-live", {})
        self.assertEqual(nova, {"policy": "provider_default", "overrides_sent": {}, "output_limits": {"maxTokens": 1024},
                                "conforms": True})
        tuned = live_providers.sampling_binding("nova-sonic-live", {"inference": {"temperature": 0.2}})
        self.assertEqual((tuned["overrides_sent"], tuned["conforms"]), ({"temperature": 0.2}, False))
        for provider in ("openai-realtime-live", "xai-voice-live", "gemini-live", "openai-live"):
            self.assertEqual(live_providers.sampling_binding(provider, {})["overrides_sent"], {})

    def test_elevenlabs_agent_body_has_no_temperature(self):
        prompt = agent.agent_body(live_providers.TOOLS, "gemini-2.5-flash")["conversation_config"]["agent"]["prompt"]
        self.assertNotIn("temperature", prompt)


class ElevenLabsAgentState(unittest.TestCase):
    def binding(self, state, env=None):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "s.json")
            if state is not None:
                path.write_text(json.dumps(state))
            return agent.agent_binding(live_providers.TOOLS, environ={"ELEVENLABS_AGENT_STATE": str(path), **(env or {})})

    def test_legacy_agent_is_flagged_stale_with_temperature_zero(self):
        b = self.binding({"agent_id": "a1", "llm": "gemini-2.5-flash", "tools_fingerprint": agent.fingerprint(live_providers.TOOLS)})
        self.assertEqual((b["agent_llm"], b["agent_llm_vendor"], b["agent_llm_source"]), ("gemini-2.5-flash", "google", "agent_state"))
        self.assertFalse(b["agent_config_current"])
        self.assertEqual(b["agent_temperature"], 0)
        sampling = live_providers.sampling_binding("elevenlabs-live", {}, b)
        self.assertFalse(sampling["conforms"])

    def test_setup_records_a_current_provider_default_agent(self):
        class Http:
            def post(self, url, json):
                self.body = json
                return mock.Mock(status_code=200, json=lambda: {"agent_id": "a2"})

        with tempfile.TemporaryDirectory() as d:
            env = {"ELEVENLABS_API_KEY": "k", "ELEVENLABS_AGENT_STATE": f"{d}/s.json"}
            agent.setup(live_providers.TOOLS, llm="gpt-4.1-mini", environ=env, client=Http())
            b = agent.agent_binding(live_providers.TOOLS, environ=env)
        self.assertTrue(b["agent_config_current"])
        self.assertEqual((b["agent_temperature"], b["agent_llm_vendor"]), ("provider_default", "openai"))
        self.assertTrue(live_providers.sampling_binding("elevenlabs-live", {}, b)["conforms"])

    def test_foreign_agent_id_is_unknown(self):
        b = self.binding({"agent_id": "a1", "llm": "gemini-2.5-flash"}, {"ELEVENLABS_AGENT_ID": "other"})
        self.assertEqual((b["agent_llm"], b["agent_llm_source"], b["agent_temperature"]), (None, "unknown", "unknown"))
        self.assertIsNone(live_providers.sampling_binding("elevenlabs-live", {}, b)["conforms"])


class RunBinding(unittest.TestCase):
    def test_voices_are_recorded_not_changed(self):
        self.assertEqual(live_providers.voice_binding("nova-sonic-live", {}), {"id": "matthew", "name": "matthew", "source": "adapter_default"})
        self.assertEqual(live_providers.voice_binding("gemini-live", {})["source"], "provider_default")
        self.assertEqual(live_providers.voice_binding("elevenlabs-live", {})["name"], "George")
        self.assertEqual(live_providers.voice_binding("openai-realtime-live", {"voice": "coral"})["source"], "requested")

    def test_asr_relation(self):
        self.assertEqual(live_providers.asr_binding("openai-realtime-live", Backend())["relation"], "SAME_VENDOR_ASR")
        self.assertEqual(live_providers.asr_binding("nova-sonic-live", Backend())["relation"], "INDEPENDENT_VENDOR_ASR")
        self.assertEqual(live_providers.asr_binding("nova-sonic-live", None)["relation"], "NO_LIVE_ASR")
        self.assertEqual(live_providers.asr_binding("elevenlabs-live", Backend(), {"agent_llm_vendor": "openai"})["relation"],
                         "SAME_VENDOR_ASR")

    def test_parity_binding_for_elevenlabs_carries_agent_llm(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "s.json").write_text(json.dumps({"agent_id": "a1", "llm": "gemini-2.5-flash"}))
            with mock.patch.dict("os.environ", {"ELEVENLABS_AGENT_STATE": f"{d}/s.json"}, clear=True):
                b = live_providers.parity_binding("elevenlabs-live", {"tools": live_providers.TOOLS}, Backend())
        self.assertEqual(b["agent_llm"], "gemini-2.5-flash")
        self.assertEqual(b["asr"]["relation"], "INDEPENDENT_VENDOR_ASR")
        self.assertEqual(set(b), {"sampling", "output_voice", "asr", "agent_llm", "agent_llm_vendor", "agent_llm_source",
                                  "agent_config_current", "agent_temperature"})


class ExactAudio(unittest.TestCase):
    def test_audio_bytes_by_provider_shape(self):
        self.assertEqual(audio_bytes({"serverContent": {"modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": B64}}]}}}), 600)
        self.assertEqual(audio_bytes({"serverContent": {"outputTranscription": {"text": "x" * 5000}}}), 0)
        self.assertEqual(audio_bytes({"type": "response.output_audio.delta", "delta": B64}), 600)
        self.assertEqual(audio_bytes({"audioOutput": {"content": B64}}), 600)
        self.assertEqual(audio_bytes({"type": "audio", "audio_event": {"audio_base_64": B64}}), 600)
        self.assertEqual(audio_bytes({"type": "agent_response"}), 0)
        AdapterNativeEvent.model_validate({"adapter": "g", "native_type": "serverContent", "payload_sha256": "0" * 64,
                                           "payload_bytes": 1, "payload_retained": False, "omission_reason": None,
                                           "received_ms": 1, "clock_domain_id": "c", "audio_bytes": 600})

    def test_gemini_first_audio_is_exact_when_flagged(self):
        def events(flagged):
            native = lambda ms, size, audio: {"type": "adapter.native_event", "content": {  # noqa: E731
                "native_type": "serverContent", "payload_bytes": size, "received_ms": ms, **({"audio_bytes": audio} if flagged else {})}}
            return [{"type": "input.caller_audio", "content": {"stream_started_ms": 0, "stream_ended_ms": 2200, "speech_ended_ms": 1000,
                                                                "turn_commit": "explicit", "commit_delay_ms": 500}},
                    native(1300, 5000, 0), native(1600, 900, 600)]
        exact = bakeoff.first_audio_turns("gemini-live", events(True))[0]
        self.assertEqual((exact["first_audio_ms"], exact["estimated"]), (600, False))
        legacy = bakeoff.first_audio_turns("gemini-live", events(False))[0]
        self.assertEqual((legacy["first_audio_ms"], legacy["estimated"]), (300, True))  # the large transcript event


class Disclosure(unittest.TestCase):
    def obs(self, provider, **binding):
        base = {"sampling": live_providers.sampling_binding(provider, {}), "output_voice": live_providers.voice_binding(provider, {}),
                "asr": live_providers.asr_binding(provider, Backend()), "harness_transport": {"turn_commit": "explicit"}}
        return {"provider": provider, "parity": bakeoff_disclosure.parity({**base, **binding})}

    def test_differences_and_limitations(self):
        providers = ["nova-sonic-live", "openai-realtime-live", "gemini-live"]
        out = bakeoff_disclosure.disclose(providers, [self.obs(p) for p in providers])
        self.assertIn("voice", out["differs"])
        self.assertIn("output_limits", out["differs"])
        self.assertNotIn("turn_commit", out["differs"])
        self.assertTrue(any("same vendor as openai-realtime-live" in t for t in out["limitations"]))
        nova = out["providers"][0]
        self.assertEqual((nova["sampling_overrides"], nova["output_limits"], nova["sampling_conforms"]), ([], ["maxTokens=1024"], True))

    def test_legacy_runs_and_advanced_prompt_deltas(self):
        legacy = {"provider": "xai-voice-live", "parity": bakeoff_disclosure.parity({"harness_transport": {}})}
        out = bakeoff_disclosure.disclose(["nova-sonic-live", "xai-voice-live"], [self.obs("nova-sonic-live"), legacy], "advanced-v1")
        self.assertEqual(out["providers"][1]["runs_recorded"], 0)
        self.assertTrue(any("not recorded for runs of xai-voice-live" in t for t in out["limitations"]))
        self.assertEqual(out["providers"][0]["prompt_delta"], [
            "Give a direct answer first, then only the explanation needed for listening comprehension."])
        self.assertIn("prompt_delta", out["differs"])

    def test_report_limits_carry_the_disclosure(self):
        from tests.test_bakeoff import obs, payload

        rows = [{**obs(p, "s0"), **self.obs(p)} for p in ("openai-realtime-live", "nova-sonic-live")]
        disclosed = bakeoff.report(payload(rows))["limits"]["provider_disclosures"]
        self.assertEqual([r["voice"] for r in disclosed["providers"]], ["marin", "matthew"])
        self.assertEqual(disclosed["sampling_policy"], "provider_default")

    def test_prompt_delta_is_empty_for_shared_profiles(self):
        for provider in live_providers.LIVE_PROVIDERS:
            self.assertEqual(delta("standardised-v2", provider), [])
            self.assertEqual(delta("conversational-v1", provider), [])
            self.assertTrue(delta("advanced-v1", provider))


if __name__ == "__main__":
    unittest.main()
