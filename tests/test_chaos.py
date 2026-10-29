"""Chaos tests: injected evidence/telemetry faults must never produce partial evidence or an unrecorded release.

Complements test_release AC-07 (fault present before the call) with faults that strike mid-transaction, during
retries, under concurrency, and with an unreachable telemetry collector. No network spend; runs on SQLite and PostgreSQL.
"""

import os
import random
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from voice_harness import telemetry
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.storage import Database, RunTransaction

BALANCE = ("get_account_summary", {"account_id": "acct_1001"})


class ChaosTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "chaos.sqlite")))
        self.engine = Engine(self.database)
        self.evidence = Evidence(self.database, root / "objects")
        self.run = self.engine.create(RunConfig().model_dump())
        self.context = self.engine.context(self.run)
        self.addCleanup(setattr, self.database, "fail_writes", False)
        result = self.engine.tool(self.run, self.context, "idv", "verify_identity", {"challenge_id": "challenge_001", "answer": "blue"})
        self.assertEqual(result["status"], "success")

    def call(self, call_id):
        return self.engine.tool(self.run, self.context, call_id, *BALANCE)

    def types(self):
        return [e["type"] for e in self.database.events(self.run)]

    def assert_ledger_intact(self):
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))
        sequences = [e["sequence"] for e in self.database.events(self.run)]
        self.assertEqual(sequences, list(range(sequences[0], sequences[0] + len(sequences))), "gap or duplicate in the ledger")

    def test_failure_after_first_event_leaves_no_partial_evidence_and_retry_is_exactly_once(self):
        before = self.types()
        real_event = RunTransaction.event

        def event_then_outage(tx, *args, **kwargs):
            result = real_event(tx, *args, **kwargs)
            tx.database.fail_writes = True  # the storage dies after this event was staged, before commit
            return result

        with mock.patch.object(RunTransaction, "event", event_then_outage), self.assertRaises(OSError):
            self.call("balance")
        self.database.fail_writes = False
        self.assertEqual(self.types(), before, "a half-written transaction leaked into the ledger")
        self.assertNotIn("balance", self.database.get(self.run)["calls"])
        self.assert_ledger_intact()

        first = self.call("balance")
        self.assertEqual(first["status"], "success")
        self.assertEqual(self.call("balance"), first, "retry must be idempotent")
        added = self.types()[len(before):]
        self.assertEqual(added.count("tool.requested"), 1)
        self.assertEqual(added.count("tool.executing"), 1)
        self.assert_ledger_intact()

    def test_outage_during_release_does_not_consume_the_token(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="fixture")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        self.database.fail_writes = True
        with self.assertRaises(OSError):
            self.engine.release_audio(self.run, token)
        self.database.fail_writes = False
        self.assertNotIn("playback.released", self.types())
        self.engine.release_audio(self.run, token)
        with self.assertRaises(ValueError):
            self.engine.release_audio(self.run, token)
        self.assertEqual(self.types().count("playback.released"), 1)
        self.assert_ledger_intact()

    def test_outage_cannot_complete_or_create_runs_silently(self):
        self.database.fail_writes = True
        with self.assertRaises(OSError):
            self.engine.create(RunConfig().model_dump())
        self.database.fail_writes = False

    def test_random_outages_under_concurrency_never_break_the_ledger(self):
        rng = random.Random(20261029)
        stop = threading.Event()

        def outages():
            while not stop.is_set():
                self.database.fail_writes = True
                time.sleep(rng.uniform(0.0005, 0.002))
                self.database.fail_writes = False
                time.sleep(rng.uniform(0.02, 0.06))

        acknowledged, failed = {}, []

        def worker(index):
            for turn in range(20):
                call_id = f"w{index}-{turn}"
                while call_id not in acknowledged:  # a client retries the same call id until storage is back
                    try:
                        result = self.call(call_id)
                    except OSError:
                        failed.append(call_id)
                        time.sleep(0.001)
                    else:
                        self.assertEqual(result["status"], "success")
                        acknowledged[call_id] = result["evidence_event_ids"]

        chaos = threading.Thread(target=outages, daemon=True)
        chaos.start()
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(worker, range(6)))
        stop.set()
        chaos.join(timeout=5)
        self.database.fail_writes = False

        self.assertEqual(len(acknowledged), 120)
        self.assertTrue(failed, "the schedule never injected a failure into a call")
        events = self.database.events(self.run)
        recorded = {e["event_id"] for e in events}
        for call_id, event_ids in acknowledged.items():
            self.assertTrue(event_ids and set(event_ids) <= recorded, f"{call_id} was acknowledged without evidence")
        requested = [e["content"]["call_id"] for e in events if e["type"] == "tool.requested"]
        self.assertEqual(sorted(c for c in requested if c.startswith("w")), sorted(acknowledged), "a retried call was recorded twice or lost")
        self.assert_ledger_intact()


class UnreachableCollector(unittest.TestCase):
    def test_dead_otlp_endpoint_neither_blocks_nor_loses_evidence(self):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "collector.sqlite")))
            engine = Engine(database)
            evidence = Evidence(database, root / "objects")
            run = engine.create(RunConfig().model_dump())
            context = engine.context(run)

            provider = TracerProvider(shutdown_on_exit=False)
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://127.0.0.1:9/v1/traces", timeout=1)))
            observer = telemetry.Telemetry(traces=provider)
            telemetry.install(observer)
            self.addCleanup(telemetry.uninstall, observer)
            started = time.monotonic()
            engine.tool(run, context, "idv", "verify_identity", {"challenge_id": "challenge_001", "answer": "blue"})
            for turn in range(25):
                self.assertEqual(engine.tool(run, context, f"c{turn}", *BALANCE)["status"], "success")
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 10, "telemetry export is blocking the evidence path")
            self.assertTrue(verify_bundle(evidence.bundle(run)))
            self.assertEqual(sum(e["type"] == "tool.requested" for e in database.events(run)), 26)


if __name__ == "__main__":
    unittest.main()
