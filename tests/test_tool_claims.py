"""Regression: a sourced transaction amount/date must not be flagged as an invented balance."""
import unittest

from voice_harness.provider_failures import failure_code
from voice_harness.semantic_checks import semantic_check
from voice_harness.tool_claims import allowed_tool_claims


class TransactionClaims(unittest.TestCase):
    def expected(self, status="success", account="acct_1001", nested=True):
        row = {"account_id": account, "transaction_id": "txn_001", "amount_minor": -1000,
               "currency": "GBP", "booked_at": "2026-10-16T09:00:00Z"}
        result = {"status": status, "structured_content": {"transactions": [row]} if nested else row}
        amounts, dates = allowed_tool_claims(result, "call1", 1)
        return {"allowed_facts": amounts, "allowed_date_facts": dates, "verified_account_id": "acct_1001"}

    def test_nested_and_direct_transaction_facts_allow_a_correct_spoken_reply(self):
        for nested in (True, False):
            check = semantic_check("Your recent transaction was a grocery purchase of £10, booked on 16 October 2026.",
                                   self.expected(nested=nested))
            self.assertEqual(check["disposition"], "safe", check)

    def test_allowed_dates_are_optional_not_required_in_every_reply(self):
        self.assertEqual(semantic_check("Your transaction was £10.", self.expected())["disposition"], "safe")
        self.assertEqual(semantic_check("I'll check that now.", self.expected())["disposition"], "safe")

    def test_wrong_amount_date_field_or_account_is_still_blocked(self):
        for text, expected in [
            ("Your transaction was £11.", self.expected()),
            ("Your transaction was booked on 17 October 2026.", self.expected()),
            ("Your balance is £10.", self.expected()),
            ("Your transaction was £10, booked on 16 October 2026.", self.expected(account="acct_2002")),
            ("Your transaction was £10, booked on 16 October 2026.", self.expected(status="failed")),
        ]:
            self.assertEqual(semantic_check(text, expected)["disposition"], "unsafe", text)


class SafeFailureCodes(unittest.TestCase):
    def test_status_is_preserved_without_copying_arbitrary_exception_text(self):
        self.assertEqual(failure_code(ValueError("PROVIDER_HTTP_403")), "PROVIDER_HTTP_403")
        self.assertEqual(failure_code(ValueError("PROVIDER_UNREACHABLE:OSError")), "PROVIDER_UNREACHABLE")
        self.assertEqual(failure_code(ValueError("request failed with secret sk-do-not-store")), "WORKER_FAILED")
        self.assertEqual(failure_code(ValueError("PROVIDER_HTTP_403 token-secret")), "WORKER_FAILED")
