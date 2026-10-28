"""Spoken-form fixture identifiers: the trusted bank accepts what a caller can say, and nothing else."""

import unittest

from test_gaps import Base

from voice_harness.spoken_ids import canonical, canonical_arguments


class Canonical(unittest.TestCase):
    def test_case_and_separators_are_ignored_but_digits_must_match(self):
        for spoken in ("txn_001", "TXN001", "txn 001", "txn-001", "Txn.001", " TXN_001 "):
            self.assertEqual(canonical(spoken, "txn"), "txn_001", spoken)
        for other in ("txn0001", "txn01", "001", "transaction 001", "txn_001a"):
            self.assertNotEqual(canonical(other, "txn"), "txn_001", other)
        self.assertEqual(canonical("txn0001", "txn"), "txn_0001")  # a different id, not padding forgiven
        self.assertEqual(canonical_arguments({"payment_id": "payment 001", "confirmation_id": "Conf 1"}),
                         {"payment_id": "payment_001", "confirmation_id": "Conf 1"})
        self.assertEqual(canonical_arguments({"transaction_ids": ["TXN001", "txn 002"], "reason": "r"}),
                         {"transaction_ids": ["txn_001", "txn_002"], "reason": "r"})


class Bank(Base):
    def lookup(self, call_id, transaction_id):
        return self.engine.tool(self.run, self.context, call_id, "get_transaction_details", {"transaction_id": transaction_id})

    def test_spoken_forms_find_the_transaction_and_the_result_keeps_the_canonical_id(self):
        self.verified()
        for i, spoken in enumerate(("TXN001", "txn 001", "txn-001", "txn_001")):
            result = self.lookup(f"t{i}", spoken)
            self.assertEqual(result["status"], "success", spoken)
            self.assertEqual(result["structured_content"]["transaction_id"], "txn_001")

    def test_a_different_number_is_still_not_found(self):
        self.verified()
        for i, other in enumerate(("txn0001", "txn01", "1", "0001", "002")):
            result = self.lookup(f"n{i}", other)
            self.assertEqual((result["status"], result["error_code"]), ("denied", "NOT_FOUND"), other)
        # A bare 001 in transaction_id names the one known transaction with exactly those digits (spoken_ids).
        self.assertEqual(self.lookup("b1", "001")["structured_content"]["transaction_id"], "txn_001")

    def test_fraud_case_accepts_spoken_transaction_ids(self):
        self.verified()
        ok = self.engine.tool(self.run, self.context, "f1", "raise_fraud_case", {"transaction_ids": ["TXN001"], "reason": "not me"})
        self.assertEqual(ok["status"], "success")
        bad = self.engine.tool(self.run, self.context, "f2", "raise_fraud_case", {"transaction_ids": ["txn0001"], "reason": "not me"})
        self.assertEqual(bad["error_code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
