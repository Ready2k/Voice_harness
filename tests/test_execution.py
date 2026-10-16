import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from voice_harness.execution import ExecutionStore, MockResultReceiver


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "bank.sqlite"
        self.store = ExecutionStore(self.path)
        self.store.create_run("run")

    def request(self, call="c1", delta=1):
        return self.store.request(
            "run",
            call,
            "fixture_adjust_balance",
            {"account_id": "acct_1001", "delta_minor": delta},
        )

    def test_commit_restart_delivery_retry_is_one_mutation(self):
        self.request()
        first = self.store.execute("run", "c1", owner_id="customer_001", verified=True)
        restarted = ExecutionStore(self.path)
        self.assertEqual(restarted.reconcile("run", "c1")["state"], "completed")
        second = restarted.execute("run", "c1", owner_id="customer_001", verified=True)
        self.assertEqual(first, second)
        self.assertEqual(second["structured_content"]["balance_minor"], 123457)
        self.assertEqual(restarted.cancel("run", "c1"), first)
        receiver = MockResultReceiver()
        receiver.deliver(first)
        receiver.deliver(second)
        self.assertEqual(len(receiver.results), 1)
        with self.assertRaisesRegex(ValueError, "RESULT_IDENTITY_CONFLICT"):
            receiver.deliver({**first, "status": "cancelled"})

    def test_cancel_before_dispatch_prevents_execution(self):
        self.request()
        cancelled = self.store.cancel("run", "c1")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(
            self.store.execute("run", "c1", owner_id="customer_001", verified=True),
            cancelled,
        )
        self.assertNotIn("executing", [e["state"] for e in self.store.audit("run")])

    def test_process_exits_after_commit_before_delivery(self):
        self.request()
        code = """
import os, sys
from voice_harness.execution import ExecutionStore
store = ExecutionStore(sys.argv[1])
store.execute('run', 'c1', owner_id='customer_001', verified=True)
os._exit(73)
"""
        process = subprocess.run(
            [sys.executable, "-c", code, str(self.path)],
            capture_output=True,
            check=False,
        )
        self.assertEqual(process.returncode, 73, process.stderr.decode())
        restarted = ExecutionStore(self.path)
        self.assertEqual(restarted.reconcile("run", "c1")["state"], "completed")
        result = restarted.execute("run", "c1", owner_id="customer_001", verified=True)
        self.assertEqual(result["structured_content"]["balance_minor"], 123457)
        self.assertEqual(
            sum(e["state"] == "completed" for e in restarted.audit("run")), 1
        )

    def test_interleaved_cancel_and_execute_has_one_authoritative_outcome(self):
        self.request()
        with ThreadPoolExecutor(max_workers=2) as pool:
            cancel = pool.submit(self.store.cancel, "run", "c1")
            execute = pool.submit(
                self.store.execute, "run", "c1", owner_id="customer_001", verified=True
            )
            self.assertEqual(cancel.result(), execute.result())
        states = [e["state"] for e in self.store.audit("run")]
        self.assertEqual(
            sum(s in {"completed", "cancelled_before_execution"} for s in states), 1
        )

    def test_conflicting_duplicate_request_rejected(self):
        self.request()
        self.assertEqual(self.request(), "requested")
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            self.request(delta=2)

    def test_schema_identity_and_ownership_denials(self):
        cases = [
            (
                {"account_id": "acct_1001", "extra": 1},
                True,
                "customer_001",
                "INVALID_ARGUMENTS",
            ),
            ({"account_id": "other"}, True, "customer_001", "OWNERSHIP_MISMATCH"),
            ({"account_id": "acct_1001"}, True, "other", "OWNERSHIP_MISMATCH"),
            ({"account_id": "acct_1001"}, False, "customer_001", "IDENTITY_REQUIRED"),
        ]
        for index, (args, verified, owner, expected) in enumerate(cases):
            call = str(index)
            self.store.request("run", call, "get_account_summary", args)
            result = self.store.execute("run", call, owner_id=owner, verified=verified)
            self.assertEqual(result["error_code"], expected)

    def test_twenty_concurrent_namespaces_and_duplicate_dispatch(self):
        for index in range(20):
            self.store.create_run(f"r{index}")
            self.store.request(
                f"r{index}",
                "c1",
                "fixture_adjust_balance",
                {"account_id": "acct_1001", "delta_minor": index},
            )

        def execute(index):
            return self.store.execute(
                f"r{index}", "c1", owner_id="customer_001", verified=True
            )

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(execute, list(range(20)) * 2))
        for index, result in enumerate(results):
            self.assertEqual(
                result["structured_content"]["balance_minor"], 123456 + index % 20
            )

    def test_audit_failure_rolls_back_mutation_and_outcome(self):
        self.request()
        with self.store.connect() as db:
            db.execute(
                "CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'sink unavailable'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.execute("run", "c1", owner_id="customer_001", verified=True)
        self.assertEqual(self.store.reconcile("run", "c1")["state"], "requested")
        with self.store.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT balance_minor FROM accounts WHERE run_id='run'"
                ).fetchone()[0],
                123456,
            )
