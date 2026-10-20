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
