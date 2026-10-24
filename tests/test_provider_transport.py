"""Input-source profiles preserve native turn semantics and disclose their settings."""

import unittest

from voice_harness.live_providers import LIVE_PROVIDERS, _adapter, configure_transport
from voice_harness.live_scenario import TURN_TIMEOUT_S


class ProviderTransportTests(unittest.TestCase):
    def test_fixed_and_adaptive_inputs_commit_only_where_provider_supports_it(self):
        for provider in LIVE_PROVIDERS:
            for mode in ("fixed", "live_ai"):
                with self.subTest(provider=provider, mode=mode):
                    _, config = _adapter(provider)
                    prompt = config.get("instructions", config.get("system_prompt"))
                    tools = config["tools"]
                    profile = configure_transport(provider, config, mode)
                    if provider == "nova-sonic-live":
                        self.assertNotIn("turn_detection", config)
                        self.assertEqual(config["endpointing"], "LOW" if mode == "live_ai" else "MEDIUM")
                        self.assertEqual(profile["turn_commit"], "provider_vad")
                    else:
                        self.assertEqual(config["turn_detection"], "manual")
                        self.assertEqual(profile["turn_commit"], "explicit")
                    self.assertEqual(config.get("instructions", config.get("system_prompt")), prompt)
                    self.assertIs(config["tools"], tools)
                    self.assertEqual(profile["turn_timeout_seconds"], TURN_TIMEOUT_S)
                    self.assertEqual(profile["provider_input_rate"], 16000 if provider in {
                        "gemini-live", "nova-sonic-live"} else 24000)

    def test_human_microphone_keeps_automatic_detection(self):
        for provider in LIVE_PROVIDERS:
            _, config = _adapter(provider)
            profile = configure_transport(provider, config, "human_microphone")
            self.assertEqual(profile["turn_commit"], "provider_vad" if provider == "nova-sonic-live" else "automatic")
            if provider != "nova-sonic-live":
                detection = config["turn_detection"]
                if provider in ("openai-realtime-live", "xai-voice-live"):  # a room mic gets the less sensitive VAD
                    self.assertEqual((detection["type"], detection["threshold"]), ("server_vad", 0.8))
                else:
                    self.assertEqual(detection, "server_vad")
            self.assertIsNone(profile["turn_timeout_seconds"])
            self.assertIsNone(profile["caller_trailing_silence_ms"])

    def test_unsupported_truncation_is_disclosed(self):
        for provider in LIVE_PROVIDERS:
            profile = configure_transport(provider, {}, "fixed")
            self.assertEqual(profile["client_truncation"], provider in {"openai-realtime-live", "xai-voice-live"})

    def test_unknown_provider_or_input_mode_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "NOT_A_LIVE_PROVIDER"):
            configure_transport("unknown", {}, "fixed")
        with self.assertRaisesRegex(ValueError, "UNKNOWN_CALLER_MODE"):
            configure_transport("gemini-live", {}, "unknown")
