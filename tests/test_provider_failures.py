"""Billing exhaustion is an access failure, not a retryable generic worker error."""

import unittest
from types import SimpleNamespace

from voice_harness.provider_failures import ACCESS_FAILURES, failure_code, stream_failure


class ProviderFailures(unittest.TestCase):
    def test_elevenlabs_early_websocket_quota_rejection_is_classified_without_message_leak(self):
        error = RuntimeError("provider diagnostic must not be copied")
        error.rcvd = SimpleNamespace(code=3000, reason="[quota_exceeded] You've run out of credits.")
        self.assertEqual(failure_code(error), "PROVIDER_QUOTA_EXCEEDED")
        self.assertIn(failure_code(error), ACCESS_FAILURES)
        error.rcvd = SimpleNamespace(code=3000, reason="unrecognised private provider message")
        self.assertEqual(failure_code(error), "WORKER_FAILED")

    def test_quota_error_event_uses_the_same_safe_category(self):
        self.assertEqual(stream_failure([{"code": "quota_exceeded"}]), "PROVIDER_QUOTA_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
