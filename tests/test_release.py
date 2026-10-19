import asyncio
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.clock_sync import ClockWindow, Sample
from voice_harness.contracts import TOOLS, RunConfig
from voice_harness.engine import Engine
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.scenarios import run_scenario
from voice_harness.simulator import ScriptedSimulator
from voice_harness.storage import Database


class ReleaseTests(unittest.TestCase):
    def test_concurrent_postgres_initialisation_preserves_the_ledger(self):
        url = os.environ.get("HARNESS_TEST_DATABASE_URL", "")
        if not url.startswith("postgresql://"):
            self.skipTest("requires PostgreSQL")
        import threading

        barrier = threading.Barrier(8)

        def initialise(_):
            barrier.wait(timeout=10)
            database = Database(url)
            return database.get(self.run)["session_id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(initialise, range(8))),
                             [self.database.get(self.run)["session_id"]] * 8)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = Database(
            os.environ.get("HARNESS_TEST_DATABASE_URL", str(self.root / "test.sqlite"))
        )
        self.now = [1000.0]
        self.engine = Engine(self.database, clock=lambda: self.now[0])
        self.evidence = Evidence(
            self.database, self.root / "objects", clock=lambda: self.now[0]
        )
        self.run = self.engine.create(RunConfig().model_dump())
        self.context = self.engine.context(self.run)

    def verified(self):
        self.assertEqual(
            self.engine.tool(
                self.run,
                self.context,
                "verify",
                "verify_identity",
                {"challenge_id": "challenge_001", "answer": "blue"},
            )["status"],
            "success",
        )

    def test_AC01_AC02_tool_result_and_identity_proposal(self):
        result = self.engine.tool(
            self.run,
            self.context,
            "balance",
            "get_account_summary",
            {"account_id": "acct_1001"},
        )
        self.assertEqual(result["error_code"], "IDENTITY_REQUIRED")
        self.assertEqual(
            self.engine.tool(
                self.run,
                self.context,
                "balance",
                "get_account_summary",
                {"account_id": "acct_1001"},
            ),
            result,
        )
        events = self.database.events(self.run)
        self.assertFalse(any(e["type"] == "tool.executing" for e in events))
        self.assertEqual(sum(e["type"] == "tool.requested" for e in events), 1)

    def test_AC03_PCM_token_failures(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="fixture")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        for field, value in [
            ("audio_sha256", "wrong"),
            ("session_id", "other"),
            ("start_sample", 10),
            ("assessment_epoch", 99),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.engine.release_audio(self.run, {**token, field: value})
        self.now[0] += 31
        with self.assertRaises(ValueError):
            self.engine.release_audio(self.run, token)
        partial = self.engine.hold_audio(
            self.run, bytes(960), transcript="partial", final=False
        )
        self.assertIsNone(
            self.engine.approve_audio(self.run, partial, scripted_safe=True)
        )
        with self.assertRaisesRegex(ValueError, "BACKPRESSURE"):
            self.engine.hold_audio(self.run, bytes(96000), transcript="too large")
        self.assertFalse(
            any(
                e["type"] == "playback.released" for e in self.database.events(self.run)
            )
        )

    def test_AC04_atomic_cancel_and_restart(self):
        self.verified()
        request, proposal_hash = self.engine.present_cancellation(
            self.run, "payment_001"
        )
        accepted = self.engine.confirm(
            self.run, self.context, request, "obs", proposal_hash, "affirm"
        )
        args = {
            "payment_id": "payment_001",
            "confirmation_id": accepted["confirmation_id"],
        }
        result = self.engine.tool(
            self.run, self.context, "cancel", "cancel_payment", args
        )
        restarted = Engine(Database(self.database.url), clock=lambda: self.now[0])
        self.assertEqual(
            restarted.tool(self.run, self.context, "cancel", "cancel_payment", args),
            result,
        )
        self.assertEqual(
            sum(
                e["type"] == "confirmation.consumed"
                for e in self.database.events(self.run)
            ),
            1,
        )
        self.assertEqual(
            self.database.get(self.run)["payments"]["payment_001"]["status"],
            "cancelled",
        )

    def test_AC04_process_killed_after_PostgreSQL_or_SQLite_commit(self):
        self.verified()
        request, proposal_hash = self.engine.present_cancellation(
            self.run, "payment_001"
        )
        confirmation = self.engine.confirm(
            self.run,
            self.context,
            request,
            "crash-observation",
            proposal_hash,
            "affirm",
        )
        args = {
            "payment_id": "payment_001",
            "confirmation_id": confirmation["confirmation_id"],
        }
        script = """
import json, os, signal
from voice_harness.storage import Database
from voice_harness.engine import Engine
engine = Engine(Database(os.environ['TEST_DATABASE']), clock=lambda: 1000)
engine.tool(os.environ['TEST_RUN'], json.loads(os.environ['TEST_CONTEXT']), 'crash-call', 'cancel_payment', json.loads(os.environ['TEST_ARGS']))
os.kill(os.getpid(), signal.SIGKILL)
"""
        env = {
            **os.environ,
            "TEST_DATABASE": self.database.url,
            "TEST_RUN": self.run,
            "TEST_CONTEXT": json.dumps(self.context),
            "TEST_ARGS": json.dumps(args),
        }
        child = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(child.returncode, -9, child.stderr.decode())
        result = self.engine.tool(
            self.run, self.context, "crash-call", "cancel_payment", args
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            sum(
                e["type"] == "confirmation.consumed"
                for e in self.database.events(self.run)
            ),
            1,
        )

    def test_AC05_twenty_concurrent_isolated_runs(self):
        runs = [self.engine.create(RunConfig().model_dump()) for _ in range(20)]

        def work(run):
            context = self.engine.context(run)
            self.engine.tool(
                run,
                context,
                "idv",
                "verify_identity",
                {"challenge_id": "challenge_001", "answer": "blue"},
            )
            result = self.engine.tool(
                run,
                context,
                "proposal",
                "create_payment",
                {
                    "account_id": "acct_1001",
                    "payee_id": run,
                    "amount_minor": 1,
                    "currency": "GBP",
                },
            )
            if runs.index(run) % 2 == 0:
                request, proposal_hash = self.engine.present_cancellation(
                    run, "payment_001"
                )
                confirmed = self.engine.confirm(
                    run, context, request, "parallel-obs", proposal_hash, "affirm"
                )
                self.engine.tool(
                    run,
                    context,
                    "cancel",
                    "cancel_payment",
                    {
                        "payment_id": "payment_001",
                        "confirmation_id": confirmed["confirmation_id"],
                    },
                )
            return result

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(work, runs))
        self.assertEqual(
            len({r["structured_content"]["proposal_id"] for r in results}), 20
        )
        for index, run in enumerate(runs):
            state = self.database.get(run)
            self.assertEqual(state["balance_minor"], 123456)
            proposals = [p for p in state["proposals"].values() if "payee_id" in p]
            self.assertEqual(proposals[0]["payee_id"], run)
            self.assertEqual(
                state["payments"]["payment_001"]["status"],
                "cancelled" if index % 2 == 0 else "pending",
            )
        with self.assertRaisesRegex(ValueError, "FORGED_RUN_CONTEXT"):
            self.engine.tool(
                runs[0],
                self.engine.context(runs[1]),
                "forged",
                "get_account_summary",
                {"account_id": "acct_1001"},
            )

    def test_AC06_schema_ownership_and_locked_identity(self):
        malformed = self.engine.tool(
            self.run,
            self.context,
            "bad",
            "verify_identity",
            {"challenge_id": "x", "answer": "blue", "run_id": "forged"},
        )
        self.assertEqual(malformed["error_code"], "INVALID_ARGUMENT")
        for index in range(3):
            self.engine.tool(
                self.run,
                self.context,
                str(index),
                "verify_identity",
                {"challenge_id": "challenge_001", "answer": "wrong"},
            )
        locked = self.engine.tool(
            self.run,
            self.context,
            "locked",
            "verify_identity",
            {"challenge_id": "challenge_001", "answer": "blue"},
        )
        self.assertEqual(locked["error_code"], "IDENTITY_LOCKED")

    def test_AC06_malformed_result_rolls_back_mutation(self):
        original = self.engine._bank

        def malformed(tx, name, args, call_id):
            original(tx, name, args, call_id)
            return {"identity_state": "verified", "unexpected": "data"}, None

        self.engine._bank = malformed
        result = self.engine.tool(
            self.run,
            self.context,
            "malformed",
            "verify_identity",
            {"challenge_id": "challenge_001", "answer": "blue"},
        )
        self.assertEqual(result["error_code"], "INVALID_TOOL_RESULT")
        self.assertEqual(self.database.get(self.run)["identity"], "unverified")

    def test_MCP_fault_catalogue_is_injectable_and_cannot_change_authorization(self):
        self.verified()
        for fault, expected in [
            ("timeout", "DEPENDENCY_TIMEOUT"),
            ("permission_denial", "FORBIDDEN"),
            ("rate_limit", "RATE_LIMITED"),
            ("malformed_result", "INVALID_TOOL_RESULT"),
            ("partial_response", "INVALID_TOOL_RESULT"),
        ]:
            engine = Engine(
                self.database,
                clock=lambda: self.now[0],
                faults={"get_account_summary": fault},
            )
            result = engine.tool(
                self.run,
                self.context,
                fault,
                "get_account_summary",
                {"account_id": "acct_1001"},
            )
            self.assertEqual(result["error_code"], expected)
        stale = Engine(
            self.database,
            clock=lambda: self.now[0],
            faults={"get_account_summary": "stale_balance"},
        )
        result = stale.tool(
            self.run,
            self.context,
            "stale",
            "get_account_summary",
            {"account_id": "acct_1001"},
        )
        self.assertNotEqual(
            result["structured_content"]["balance_minor"],
            self.database.get(self.run)["balance_minor"],
        )
        duplicate = Engine(
            self.database,
            clock=lambda: self.now[0],
            faults={"list_recent_transactions": "duplicate_transaction"},
        )
        rows = duplicate.tool(
            self.run,
            self.context,
            "duplicate",
            "list_recent_transactions",
            {"account_id": "acct_1001", "limit": 10},
        )["structured_content"]["transactions"]
        self.assertEqual(len(rows), 2)
        other = self.engine.create(RunConfig().model_dump())
        misleading = Engine(
            self.database,
            clock=lambda: self.now[0],
            faults={"get_policy_text": "contradictory_policy"},
        )
        context = misleading.context(other)
        misleading.tool(
            other,
            context,
            "policy",
            "get_policy_text",
            {"policy_id": "identity", "version": "1"},
        )
        self.assertEqual(
            misleading.tool(
                other,
                context,
                "balance",
                "get_account_summary",
                {"account_id": "acct_1001"},
            )["error_code"],
            "IDENTITY_REQUIRED",
        )

    def test_MCP_delivery_timeout_is_unknown_and_not_blindly_reexecuted(self):
        from voice_harness.mcp_gateway import Gateway

        class LostConnection:
            async def call_tool(self, *args, **kwargs):
                self.timeout = kwargs["read_timeout_seconds"]
                raise TimeoutError("injected")

        client = LostConnection()
        result = asyncio.run(
            Gateway(client, self.engine, self.run).tool(
                "lost", "get_account_summary", {"account_id": "acct_1001"}
            )
        )
        self.assertEqual(client.timeout, 3)
        self.assertEqual(result["status"], "outcome_unknown")
        self.assertEqual(self.database.get(self.run)["state"], "recovery_required")
        self.assertEqual(
            self.engine.tool(
                self.run,
                self.context,
                "lost",
                "get_account_summary",
                {"account_id": "acct_1001"},
            ),
            result,
        )

    def test_AC17_interleaved_hold_activation_and_purge(self):
        self.evidence.put(self.run, bytes(960), ttl=1)
        self.now[0] = 1002
        with ThreadPoolExecutor(max_workers=2) as pool:
            hold = pool.submit(
                self.evidence.hold, self.run, "race", "owner", 1100, "race-key"
            )
            purge = pool.submit(self.evidence.purge, self.run)
            hold.result()
            purged = purge.result()
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(bundle))
        if not purged:
            self.assertTrue(bundle["artifacts"][0]["available"])
        else:
            self.assertFalse(bundle["artifacts"][0]["available"])

    def test_AC07_failed_ledger_prevents_tool_and_audio(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="fixture")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        self.database.fail_writes = True
        with self.assertRaises(OSError):
            self.engine.release_audio(self.run, token)
        with self.assertRaises(OSError):
            self.engine.tool(
                self.run,
                self.context,
                "balance",
                "get_account_summary",
                {"account_id": "acct_1001"},
            )
        self.database.fail_writes = False
        self.assertFalse(
            any(
                e["type"] == "playback.released" for e in self.database.events(self.run)
            )
        )

    def test_AC07_telemetry_failure_cannot_remove_evidence(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            SimpleSpanProcessor,
            SpanExporter,
            SpanExportResult,
        )

        class FailingExporter(SpanExporter):
            def export(self, spans):
                return SpanExportResult.FAILURE

            def shutdown(self):
                pass

        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(FailingExporter()))
        with provider.get_tracer("failure-test").start_as_current_span("tool"):
            result = self.engine.tool(
                self.run,
                self.context,
                "balance",
                "get_account_summary",
                {"account_id": "acct_1001"},
            )
        provider.shutdown()
        self.assertEqual(result["error_code"], "IDENTITY_REQUIRED")
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(bundle))
        self.assertTrue(bundle["events"][-1]["trace_context"]["trace_id"])

    def test_AC08_bundle_event_artifact_and_suffix_tampering(self):
        self.evidence.put(self.run, bytes(960))
        original = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(original))
        for change in ["event", "artifact", "suffix"]:
            altered = copy.deepcopy(original)
            if change == "event":
                altered["events"][0]["content"]["fixture_seed"] = 999
            elif change == "artifact":
                altered["artifacts"][0]["data_hex"] = "01"
            else:
                altered["events"].pop()
            self.assertFalse(verify_bundle(altered))

    def test_AC14_revision_all_four_states(self):
        for phase in ["pending", "approved", "scheduled", "observed"]:
            run = self.engine.create(RunConfig().model_dump())
            unit = self.engine.hold_audio(run, bytes(960), transcript="ten pounds")
            token = None
            if phase != "pending":
                token = self.engine.approve_audio(run, unit, scripted_safe=True)
            if phase in {"scheduled", "observed"}:
                self.engine.release_audio(run, token)
            if phase == "observed":
                self.engine.observe_playback(run, unit, 480)
            self.engine.revise_audio(run, unit, "one hundred pounds")
            if token:
                with self.assertRaises(ValueError):
                    self.engine.release_audio(run, token)
            types = [e["type"] for e in self.database.events(run)]
            self.assertEqual(
                "audio.post_release_conflict" in types, phase == "observed"
            )

    def test_AC14_observed_unsafe_and_unassessable_are_not_passes(self):
        for assessment, verdict in [("unsafe", "fail"), ("unassessable", "untestable")]:
            unit = self.engine.hold_audio(self.run, bytes(960), transcript="ten pounds")
            token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
            self.engine.release_audio(self.run, token)
            self.engine.observe_playback(self.run, unit, 480)
            self.engine.revise_audio(
                self.run, unit, "one hundred pounds", actual_audio_assessment=assessment
            )
            self.assertEqual(
                self.database.events(self.run)[-1]["content"]["verdict"], verdict
            )

    def test_crash_recovery_revokes_tokens_and_preserves_completed_outcomes(self):
        with self.database.transaction(self.run) as tx:
            tx.state["state"] = "running"
            tx.event("orchestrator", "run.running", {})
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="fixture")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        restarted = Engine(Database(self.database.url), clock=lambda: self.now[0])
        self.assertIn(self.run, restarted.recover())
        self.assertEqual(self.database.get(self.run)["state"], "recovery_required")
        with self.assertRaises(ValueError):
            restarted.release_audio(self.run, token)

    def test_AC15_replay_expiry_and_exact_confirmation(self):
        self.verified()
        request, proposal_hash = self.engine.present_cancellation(
            self.run, "payment_001"
        )
        for choice, sha, source, expected in [
            ("ambiguous", proposal_hash, True, "AMBIGUOUS_RESPONSE"),
            ("affirm", "changed", True, "PROPOSAL_CHANGED"),
            ("affirm", proposal_hash, False, "UNTRUSTED_SOURCE"),
        ]:
            result = self.engine.confirm(
                self.run,
                self.context,
                request,
                "rejected-" + expected,
                sha,
                choice,
                trusted_source=source,
            )
            self.assertEqual(result["reason_code"], expected)
        accepted = self.engine.confirm(
            self.run, self.context, request, "obs", proposal_hash, "affirm"
        )
        self.assertTrue(accepted["accepted"])
        self.assertEqual(
            self.engine.confirm(
                self.run, self.context, request, "obs", proposal_hash, "affirm"
            )["reason_code"],
            "OBSERVATION_REPLAYED",
        )
        request2, sha2 = self.engine.present_cancellation(self.run, "payment_001")
        self.now[0] += 61
        self.assertEqual(
            self.engine.confirm(
                self.run, self.context, request2, "obs2", sha2, "affirm"
            )["reason_code"],
            "EXPIRED",
        )

    def test_AC15_confirmation_revision_before_and_after_commit(self):
        self.verified()
        for index, after_commit in enumerate([False, True]):
            request, sha = self.engine.present_cancellation(self.run, "payment_001")
            confirmation = self.engine.confirm(
                self.run, self.context, request, str(index), sha, "affirm"
            )["confirmation_id"]
            args = {"payment_id": "payment_001", "confirmation_id": confirmation}
            if after_commit:
                self.assertEqual(
                    self.engine.tool(
                        self.run, self.context, "commit", "cancel_payment", args
                    )["status"],
                    "success",
                )
            self.engine.revise_confirmation(self.run, confirmation, "deny")
            if after_commit:
                self.assertEqual(
                    self.database.get(self.run)["payments"]["payment_001"]["status"],
                    "cancelled",
                )
                self.assertEqual(
                    self.database.events(self.run)[-1]["type"],
                    "confirmation.post_commit_conflict",
                )
            else:
                self.assertEqual(
                    self.engine.tool(
                        self.run, self.context, "revoked", "cancel_payment", args
                    )["error_code"],
                    "CONFIRMATION_REQUIRED",
                )

    def test_AC15_changed_amount_expired_acceptance_and_rejected_observation_replay(
        self,
    ):
        self.verified()
        request, sha = self.engine.present_cancellation(self.run, "payment_001")
        self.engine.confirm(
            self.run, self.context, request, "ambiguous", sha, "ambiguous"
        )
        self.assertEqual(
            self.engine.confirm(
                self.run, self.context, request, "ambiguous", sha, "affirm"
            )["reason_code"],
            "OBSERVATION_REPLAYED",
        )
        accepted = self.engine.confirm(
            self.run, self.context, request, "valid", sha, "affirm"
        )
        with self.database.transaction(self.run) as tx:
            tx.state["payments"]["payment_001"]["amount_minor"] += 1
        denied = self.engine.tool(
            self.run,
            self.context,
            "changed-amount",
            "cancel_payment",
            {
                "payment_id": "payment_001",
                "confirmation_id": accepted["confirmation_id"],
            },
        )
        self.assertEqual(denied["error_code"], "CONFIRMATION_REQUIRED")
        request, sha = self.engine.present_cancellation(self.run, "payment_001")
        accepted = self.engine.confirm(
            self.run, self.context, request, "fresh", sha, "affirm"
        )
        self.now[0] += 61
        expired = self.engine.tool(
            self.run,
            self.context,
            "expired",
            "cancel_payment",
            {
                "payment_id": "payment_001",
                "confirmation_id": accepted["confirmation_id"],
            },
        )
        self.assertEqual(expired["error_code"], "EXPIRED")
        self.assertEqual(
            self.database.get(self.run)["payments"]["payment_001"]["status"], "pending"
        )

    def test_AC15_unsolicited_confirmation_rejected(self):
        """A confirmation with no presented proposal (unknown or never-presented request) never yields consent."""
        self.verified()
        before = len(self.database.get(self.run)["confirmations"])
        unknown = self.engine.confirm(
            self.run, self.context, "confirmation_request_unsolicited", "u1", "x" * 64, "affirm"
        )
        self.assertEqual(unknown["reason_code"], "NO_ACTIVE_REQUEST")
        request, sha = self.engine.present_cancellation(self.run, "payment_001")
        # a stale, superseded presentation is also unsolicited relative to the live proposal
        self.engine.present_cancellation(self.run, "payment_001")
        stale = self.engine.confirm(self.run, self.context, request, "u2", sha, "affirm")
        self.assertEqual(stale["reason_code"], "NO_ACTIVE_REQUEST")
        state = self.database.get(self.run)
        self.assertEqual(len(state["confirmations"]), before)
        self.assertEqual(state["payments"]["payment_001"]["status"], "pending")

    def test_AC15_cross_session_confirmation_rejected(self):
        """Another run's context, or its request id, cannot confirm this run's proposal."""
        self.verified()
        request, sha = self.engine.present_cancellation(self.run, "payment_001")
        other = self.engine.create(RunConfig().model_dump())
        other_context = self.engine.context(other)
        wrong_context = self.engine.confirm(self.run, other_context, request, "x1", sha, "affirm")
        self.assertEqual(wrong_context["reason_code"], "SESSION_MISMATCH")
        # the other run is not verified/has no such request: this run's request id is unknown there
        wrong_run = self.engine.confirm(other, other_context, request, "x2", sha, "affirm")
        self.assertFalse(wrong_run["accepted"])
        self.assertEqual(wrong_run["reason_code"], "NO_ACTIVE_REQUEST")
        self.assertEqual(self.database.get(other)["confirmations"], {})
        self.assertEqual(self.database.get(self.run)["confirmations"], {})
        # a confirmation id minted in this run is useless in the other run
        accepted = self.engine.confirm(self.run, self.context, request, "ok", sha, "affirm")
        self.assertTrue(accepted["accepted"])
        self.engine.tool(
            other, other_context, "verify", "verify_identity",
            {"challenge_id": "challenge_001", "answer": "blue"},
        )
        denied = self.engine.tool(
            other, other_context, "xs", "cancel_payment",
            {"payment_id": "payment_001", "confirmation_id": accepted["confirmation_id"]},
        )
        self.assertEqual(denied["error_code"], "CONFIRMATION_REQUIRED")
        self.assertEqual(self.database.get(other)["payments"]["payment_001"]["status"], "pending")

    def test_AC15_changed_payee_invalidates_consent(self):
        self.verified()
        request, sha = self.engine.present_cancellation(self.run, "payment_001")
        proposal = self.database.get(self.run)["proposals"][request]["proposal"]
        self.assertEqual(proposal["payee_id"], "payee_001")
        accepted = self.engine.confirm(self.run, self.context, request, "p1", sha, "affirm")
        self.assertTrue(accepted["accepted"])
        with self.database.transaction(self.run) as tx:
            tx.state["payments"]["payment_001"]["payee_id"] = "payee_999"
        denied = self.engine.tool(
            self.run, self.context, "changed-payee", "cancel_payment",
            {"payment_id": "payment_001", "confirmation_id": accepted["confirmation_id"]},
        )
        self.assertEqual(denied["error_code"], "CONFIRMATION_REQUIRED")
        self.assertEqual(self.database.get(self.run)["payments"]["payment_001"]["status"], "pending")
        # a payee change after presentation but before confirming is also rejected as a changed proposal
        request2, sha2 = self.engine.present_cancellation(self.run, "payment_001")
        with self.database.transaction(self.run) as tx:
            tx.state["payments"]["payment_001"]["payee_id"] = "payee_001"
        reconfirm = self.engine.tool(
            self.run, self.context, "changed-back", "cancel_payment",
            {"payment_id": "payment_001",
             "confirmation_id": self.engine.confirm(self.run, self.context, request2, "p2", sha2, "affirm")["confirmation_id"]},
        )
        self.assertEqual(reconfirm["error_code"], "CONFIRMATION_REQUIRED")

    def test_AC04_cancel_prepared_dispatch_has_zero_mutation(self):
        self.engine.prepare_tool(
            self.run,
            self.context,
            "c1",
            "get_account_summary",
            {"account_id": "acct_1001"},
        )
        self.engine.cancel(self.run, "before_dispatch")
        result = self.engine.tool(
            self.run,
            self.context,
            "c1",
            "get_account_summary",
            {"account_id": "acct_1001"},
        )
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(
            any(e["type"] == "tool.executing" for e in self.database.events(self.run))
        )

    def test_AC14_exploratory_exposure_revision_is_failure(self):
        run = self.engine.create(
            RunConfig(policy_profile="exploratory-v1").model_dump()
        )
        unit = self.engine.hold_audio(
            run, bytes(960), transcript="ten pounds", final=False
        )
        token = self.engine.approve_audio(
            run, unit, scripted_safe=False, exploratory_unreviewed=True
        )
        self.assertEqual(token["risk_mode"], "unreviewed_release")
        self.engine.release_audio(run, token)
        self.engine.observe_playback(run, unit, 480)
        self.engine.revise_audio(
            run, unit, "one hundred pounds", actual_audio_assessment="unsafe"
        )
        self.assertEqual(self.database.events(run)[-1]["content"]["verdict"], "fail")
        strict_unit = self.engine.hold_audio(self.run, bytes(960), transcript="fixture")
        with self.assertRaisesRegex(ValueError, "NOT_PERMITTED"):
            self.engine.approve_audio(
                self.run, strict_unit, scripted_safe=False, exploratory_unreviewed=True
            )

    def test_AC14_exploratory_all_four_release_states(self):
        for state in ["pending", "approved", "scheduled", "observed"]:
            run = self.engine.create(
                RunConfig(policy_profile="exploratory-v1").model_dump()
            )
            unit = self.engine.hold_audio(run, bytes(960), transcript="ten pounds")
            token = None
            if state != "pending":
                token = self.engine.approve_audio(
                    run, unit, scripted_safe=False, exploratory_unreviewed=True
                )
            if state in {"scheduled", "observed"}:
                self.engine.release_audio(run, token)
            if state == "observed":
                self.engine.observe_playback(run, unit, 480)
            self.engine.revise_audio(
                run, unit, "one hundred pounds", actual_audio_assessment="unsafe"
            )
            if token:
                with self.assertRaises(ValueError):
                    self.engine.release_audio(run, token)

    def test_AC14_unknown_alignment_quarantines_the_response(self):
        first = self.engine.hold_audio(
            self.run, bytes(960), transcript="ten", response_id="r1"
        )
        second = self.engine.hold_audio(
            self.run, bytes(960), transcript="pounds", response_id="r1"
        )
        token = self.engine.approve_audio(self.run, second, scripted_safe=True)
        self.engine.revise_audio(self.run, first, "one hundred", alignment_known=False)
        with self.assertRaises(ValueError):
            self.engine.release_audio(self.run, token)

    def test_AC16_seeded_branch_and_oracle_boundary(self):
        branches = [
            {
                "id": "a",
                "requires": "identity_prompt",
                "next_node": "verify",
                "utterance": "Please repeat the question.",
            }
        ]
        simulator = ScriptedSimulator(
            42, {"persona": "customer", "goal": "balance", "visible_facts": {}}
        )
        self.assertEqual(
            simulator.select(["identity_prompt"], branches),
            simulator.select(["identity_prompt"], branches),
        )
        with self.assertRaisesRegex(ValueError, "HIDDEN_ORACLE_ACCESS"):
            ScriptedSimulator(42, {"oracle": {"answer": "secret"}})

    def test_AC17_hold_grace_and_declared_purge(self):
        sha = self.evidence.put(self.run, bytes(960), ttl=1)
        self.evidence.hold(self.run, "disputed finding", "reviewer", 1100, "hold-key")
        self.now[0] = 1002
        self.assertEqual(self.evidence.purge(self.run), [])
        self.now[0] = 1101
        self.assertEqual(self.evidence.purge(self.run), [])
        self.now[0] += 7 * 86400
        self.assertEqual(self.evidence.purge(self.run), [sha])
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(bundle))
        self.assertEqual(bundle["manifest"]["missing_artifacts"], [sha])

    def test_AC21_asymmetry_drift_reconnect_stale(self):
        sync = ClockWindow([Sample(0, 10, 11, 21)] * 8, 1000)
        bound = sync.latency(0, 50, 1030, 1)
        self.assertGreater(bound["uncertainty_ms"], 10)
        self.assertFalse(bound["rank_eligible"])
        self.assertIsNone(sync.latency(0, 50, 1061, 1)["value_ms"])
        self.assertIsNone(sync.latency(0, 50, 1001, 2)["value_ms"])
        sync.invalidate()
        self.assertIsNone(sync.latency(0, 50, 1001, 1)["value_ms"])

    def test_API_auth_idempotency_exports_and_websocket(self):
        app = create_app(self.database, self.root, "operator")
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/providers").status_code, 401)
            headers = {"Authorization": "Bearer operator", "Idempotency-Key": self.run}
            first = client.post("/api/v1/runs", headers=headers, json={})
            self.assertEqual(first.status_code, 202, first.text)
            run = first.json()["run_ids"][0]
            app.state.jobs[run].result(timeout=10)
            self.assertEqual(self.database.get(run)["state"], "completed")
            self.assertEqual(
                client.post("/api/v1/runs", headers=headers, json={}).json(),
                first.json(),
            )
            self.assertEqual(
                client.post(
                    "/api/v1/runs", headers=headers, json={"fixture_seed": 43}
                ).status_code,
                409,
            )
            self.assertTrue(
                verify_bundle(
                    client.get(f"/api/v1/runs/{run}/evidence", headers=headers).json()
                )
            )
            self.assertEqual(
                client.get(
                    "/api/v1/providers",
                    headers={**headers, "Origin": "https://evil.example"},
                ).status_code,
                403,
            )
            with client.websocket_connect(
                f"/api/v1/runs/{self.run}/ws",
                headers={**headers, "Origin": "http://localhost:8000"},
            ) as ws:
                start = ws.receive_json()
                message = {
                    "schema_version": "0.3",
                    "message_id": "m1",
                    "session_id": start["session_id"],
                    "connection_epoch": 1,
                    "client_sequence": 1,
                    "type": "audio.input",
                    "payload": {
                        "stream_id": "input_1",
                        "chunk_sequence": 1,
                        "bytes": base64.b64encode(bytes(960)).decode(),
                        "codec": "pcm_s16le",
                        "rate": 24000,
                        "channels": 1,
                        "start_sample": 0,
                        "end_sample": 480,
                    },
                }
                ws.send_json(message)
                self.assertEqual(ws.receive_json()["type"], "audio.accepted")
                ws.send_json(message)
                self.assertEqual(ws.receive_json()["type"], "duplicate")

    def test_MCP_stdio_negotiation_discovery_and_denial(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def check():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "voice_harness.mcp_server"],
                env={
                    **os.environ,
                    "PYTHONPATH": os.environ.get("PYTHONPATH", "src"),
                    "HARNESS_DATABASE_URL": self.database.url,
                    "HARNESS_RUN_CONTEXT": json.dumps(self.context),
                },
            )
            async with stdio_client(parameters) as (read, write):  # noqa: SIM117 -- streams precede session creation
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    self.assertTrue(initialized.protocol_version)
                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, set(TOOLS))
                    result = await session.call_tool(
                        "get_account_summary", {"account_id": "acct_1001"}
                    )
                    self.assertTrue(result.is_error)
                    self.assertEqual(
                        result.structured_content["error_code"], "IDENTITY_REQUIRED"
                    )

        asyncio.run(check())

    def test_MCP_injected_failure_and_invalid_arguments_through_stdio_gateway(self):
        from voice_harness.mcp_gateway import gateway

        engine = Engine(self.database, faults={"get_policy_text": "timeout"})
        run = engine.create(RunConfig().model_dump())

        async def check():
            async with gateway(engine, run) as bank:
                result = await bank.tool(
                    "timeout",
                    "get_policy_text",
                    {"policy_id": "identity", "version": "1"},
                )
                self.assertEqual(result["error_code"], "DEPENDENCY_TIMEOUT")
                invalid = await bank.tool(
                    "invalid",
                    "get_policy_text",
                    {"policy_id": "identity", "version": "1", "unexpected": True},
                )
                self.assertEqual(invalid["error_code"], "INVALID_ARGUMENT")

        asyncio.run(check())

    def test_fabricated_judge_evidence_is_rejected_and_projections_exist(self):
        with (
            self.assertRaisesRegex(ValueError, "INVALID_EVIDENCE_REFERENCE"),
            self.database.transaction(self.run) as tx,
        ):
            tx.event(
                "judge",
                "judge.completed",
                {"findings": [{"evidence_event_ids": ["fabricated"]}]},
            )
        run_scenario(self.engine, self.run, self.evidence)
        self.evidence.save_manifest(self.run)
        with self.database.connection() as db:
            for table in [
                "harness_transcript_segments",
                "harness_guardrail_decisions",
                "harness_judge_runs",
                "harness_judge_findings",
                "harness_evidence_manifests",
            ]:
                count = db.execute(
                    f"SELECT COUNT(*) AS count FROM {table} WHERE run_id=?", (self.run,)
                ).fetchone()["count"]
                self.assertGreater(count, 0, table)

    def test_signed_PCM_over_WebSocket_and_client_ack(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        from voice_harness.approvals import signing_bytes

        app = create_app(self.database, self.root, "operator")
        headers = {
            "Authorization": "Bearer operator",
            "Idempotency-Key": self.run + "-ws",
        }
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/runs", headers=headers, json={"client_mode": "websocket"}
            )
            run = response.json()["run_ids"][0]
            app.state.jobs[run].result(timeout=10)
            self.assertEqual(self.database.get(run)["state"], "awaiting_playback")
            ticket = client.post(
                f"/api/v1/runs/{run}/ws-ticket", headers=headers
            ).json()["ticket"]
            with client.websocket_connect(
                f"/api/v1/runs/{run}/ws",
                headers={"Origin": "http://localhost:8765"},
                subprotocols=["harness.v1", "ticket." + ticket],
            ) as ws:
                start, audio = ws.receive_json(), ws.receive_json()
                token = audio["token"]
                public = Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(start["approval_public_key"])
                )
                public.verify(
                    base64.b64decode(token["signature"]), signing_bytes(token)
                )
                self.assertEqual(len(base64.b64decode(audio["audio"])), 960)
                ws.send_json(
                    {
                        "schema_version": "0.3",
                        "message_id": "ack",
                        "session_id": start["session_id"],
                        "connection_epoch": start["connection_epoch"],
                        "client_sequence": 1,
                        "type": "playback.ack",
                        "payload": {
                            "unit_id": token["unit_id"],
                            "end_sample": token["end_sample"],
                        },
                    }
                )
                self.assertEqual(ws.receive_json()["type"], "playback.acknowledged")
            self.assertEqual(self.database.get(run)["state"], "completed")

    def test_closed_argument_result_and_event_schemas(self):
        from jsonschema import Draft202012Validator

        from voice_harness.contracts import OUTPUTS

        for name, model in TOOLS.items():
            self.assertFalse(model.model_json_schema()["additionalProperties"])
            self.assertFalse(OUTPUTS[name].model_json_schema()["additionalProperties"])
        schema = json.loads(Path("contracts/canonical-event.schema.json").read_text())
        for event in self.database.events(self.run):
            Draft202012Validator(schema).validate(event)
        examples = json.loads(Path("contracts/examples.json").read_text())
        for name, example in examples.items():
            schema = json.loads(Path("contracts", name + ".schema.json").read_text())
            Draft202012Validator(schema).validate(example)
