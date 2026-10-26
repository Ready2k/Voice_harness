"""Cross-provider prompt equivalence and exact evidence bindings."""

import hashlib
import unittest

from pydantic import ValidationError

from voice_harness.contracts import RunConfig
from voice_harness.live_providers import (
    LEGACY_INSTRUCTIONS,
    LIVE_PROVIDERS,
    TOOLS,
    _adapter,
    session_metadata,
)
from voice_harness.target_prompts import STANDARDISED_V2, provenance


class TargetPromptTests(unittest.TestCase):
    def test_advanced_style_preserves_banking_rules_and_binds_actual_provider_prompt(self):
        prompts = []
        for provider in LIVE_PROVIDERS:
            _, config = _adapter(provider, "advanced-v1")
            sent = config.get("instructions", config.get("system_prompt"))
            self.assertIn(STANDARDISED_V2, sent)
            self.assertNotIn("already verified", sent)
            self.assertNotIn("You have no tools", sent)
            self.assertEqual(config["tools"], TOOLS)
            bound = session_metadata(provider, "test", {}, prompt_profile="advanced-v1")["target_prompt"]
            self.assertEqual(bound["instructions"], sent)
            self.assertTrue(bound["vendor_tuned"])
            self.assertEqual(bound["comparison_track"], "advanced")
            prompts.append(sent)
        self.assertEqual(len(set(prompts)), len(LIVE_PROVIDERS))
        self.assertEqual(RunConfig(prompt_profile="advanced-v1").prompt_profile, "advanced-v1")

    def test_all_adapters_receive_the_selected_prompt_and_same_tools(self):
        for provider in LIVE_PROVIDERS:
            for profile, expected in [("standardised-v2", STANDARDISED_V2), ("legacy-v1", LEGACY_INSTRUCTIONS)]:
                with self.subTest(provider=provider, profile=profile):
                    _, config = _adapter(provider, profile)
                    sent = config.get("instructions", config.get("system_prompt"))
                    self.assertEqual(sent, expected)
                    self.assertEqual(config["tools"], TOOLS)
                    binding = session_metadata(provider, "test-model", {}, prompt_profile=profile)["target_prompt"]
                    self.assertEqual(binding["instructions"], sent)
                    self.assertEqual(binding["instructions_sha256"], hashlib.sha256(sent.encode()).hexdigest())
                    self.assertFalse(binding["vendor_tuned"])

    def test_all_catalogue_expected_tools_are_exposed_to_every_provider(self):
        from voice_harness.scenario_catalogue import CATALOGUE

        required = {name for entry in CATALOGUE.values()
                    for name in entry["definition"]["expected_tool_sequence"]}
        for provider in LIVE_PROVIDERS:
            _, config = _adapter(provider)
            self.assertFalse(required - {t["name"] for t in config["tools"]})

    def test_provenance_detects_instruction_and_tool_changes(self):
        before = provenance("standardised-v2", STANDARDISED_V2, TOOLS)
        changed = provenance("standardised-v2", STANDARDISED_V2 + " changed", [])
        self.assertNotEqual(before["instructions_sha256"], changed["instructions_sha256"])
        self.assertNotEqual(before["tools_sha256"], changed["tools_sha256"])

    def test_profile_is_persisted_and_unvalidated_optimised_profiles_rejected(self):
        self.assertEqual(RunConfig().model_dump()["prompt_profile"], "standardised-v2")
        self.assertEqual(RunConfig(prompt_profile="legacy-v1").model_dump()["prompt_profile"], "legacy-v1")
        with self.assertRaises(ValidationError):
            RunConfig(prompt_profile="vendor-optimised")
        with self.assertRaises(ValueError):
            _adapter("openai-realtime-live", "unknown")
