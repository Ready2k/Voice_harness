import unittest

from voice_harness.coverage import default_registry
from voice_harness.events import Ledger
from voice_harness.golden import run_identity_bypass
from voice_harness.playback import PlaybackGate
from voice_harness.policy import authorize_tool, valid_identity_digits
from voice_harness.tool_lifecycle import ToolLifecycle


class CoreTests(unittest.TestCase):
    def test_ledger_hash_chain_detects_tampering(self):
        ledger = Ledger("run_1")
        ledger.append("orchestrator", "one", {"x": 1})
        ledger.append("orchestrator", "two", {"x": 2})
        self.assertTrue(ledger.verify())
        ledger.events[0].content["x"] = 99
        self.assertFalse(ledger.verify())

    def test_identity_bypass_denies_before_bank_execution(self):
        result = run_identity_bypass()
        self.assertEqual(result["bank_read_count"], 0)
        self.assertEqual(result["bank_mutation_count"], 0)
        self.assertEqual(result["platform_enforcement"], "pass")
        self.assertEqual(result["overall_assurance"], "fail")
        self.assertTrue(result["ledger_verified"])

    def test_identity_digits_preserve_leading_zeroes(self):
        self.assertTrue(
            valid_identity_digits({"accountNumber": "00123456", "sortCode": "010203"})
        )
        self.assertFalse(
            valid_identity_digits({"accountNumber": 12345678, "sortCode": "010203"})
        )

    def test_unverified_account_tool_is_denied(self):
        decision = authorize_tool(
            "get_account_summary",
            {"account_id": "acct_1001"},
            allowed_tools={"get_account_summary"},
            verified=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "IDENTITY_REQUIRED")

    def test_stale_playback_token_cannot_release_after_interrupt(self):
        gate = PlaybackGate("client_1")
        unit = gate.hold("resp_1", "safe fallback")
        token = gate.approve(unit)
        gate.interrupt()
        with self.assertRaises(ValueError):
            gate.release(token)

    def test_coverage_registry_keeps_all_requested_families_visible(self):
        registry = default_registry()
        self.assertEqual(len(registry), 5)
        self.assertTrue(
            all(item.coverage_state == "not_implemented" for item in registry)
        )

    def test_tool_lifecycle_settles_once(self):
        lifecycle = ToolLifecycle()
        self.assertTrue(lifecycle.accept("c1", "get_account_summary", "run:c1"))
        self.assertTrue(lifecycle.settle("c1", "denied", "IDENTITY_REQUIRED"))
        self.assertFalse(lifecycle.settle("c1", "completed"))


if __name__ == "__main__":
    unittest.main()
