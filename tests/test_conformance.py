"""Phase 1 conformance-audit regressions (see docs/PHASE1_CONFORMANCE_AUDIT.md)."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.canonical import canonical_json
from voice_harness.contracts import (
    EVENT_CONTRACTS,
    HoldRecord,
    PolicyDecision,
    Result,
    RunConfig,
)
from voice_harness.engine import Engine
from voice_harness.events import SOURCES, Event
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.scenarios import run_scenario
from voice_harness.simulator import ScriptedSimulator
from voice_harness.storage import Database

BRANCHES = [
    {"id": "a", "requires": "prompt", "next_node": "n1", "utterance": "one"},
    {
        "id": "b",
        "requires": "prompt",
        "next_node": "n2",
        "utterance": "two",
        "precedence": 5,
    },
    {"id": "c", "requires": "other", "next_node": "n3", "utterance": "three"},
]
FALLBACK = {"id": "fallback", "requires": "any", "next_node": "n0", "utterance": "um"}


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = Database(
            os.environ.get("HARNESS_TEST_DATABASE_URL", str(self.root / "t.sqlite"))
        )
        self.now = [1000.0]
        self.engine = Engine(self.database, clock=lambda: self.now[0])
        self.evidence = Evidence(
            self.database, self.root / "objects", clock=lambda: self.now[0]
        )
        self.run = self.engine.create(RunConfig().model_dump())
        self.context = self.engine.context(self.run)

    def types(self):
        return [e["type"] for e in self.database.events(self.run)]


class CanonicalHashTests(unittest.TestCase):
    def test_rfc8785_number_and_string_vectors(self):
        cases = {
            1e21: b"1e+21",
            1e-7: b"1e-7",
            123456789012345680000.0: b"123456789012345680000",
            0.000001: b"0.000001",
            1100.0: b"1100",
            4.5: b"4.5",
            -0.0: b"0",
        }
        for value, expected in cases.items():
            self.assertEqual(canonical_json(value), expected, value)
        self.assertEqual(
            canonical_json({"b": 1, "a": ["\u00e9", "\n"], "\U0001f600": None}),
            '{"a":["é","\\n"],"b":1,"\U0001f600":null}'.encode(),
        )
        # Member names sort by UTF-16 code units: U+1F600 (D83D) before U+FFFF.
        self.assertLess(
            canonical_json({"\U0001f600": 1, "\uffff": 2}).index("\U0001f600".encode()),
            canonical_json({"\U0001f600": 1, "\uffff": 2}).index("\uffff".encode()),
        )
        with self.assertRaises(ValueError):
            canonical_json(float("nan"))

    def test_event_hash_is_canonical_and_stable_across_numeric_form(self):
        a = Event("run", "orchestrator", "t", {"x": 1100.0, "s": "\u00e9"})
        b = Event("run", "orchestrator", "t", {"x": 1100, "s": "\u00e9"})
        for event in (a, b):
            event.event_id, event.occurred_at, event.monotonic_ns = "e", "t", 1
            event.clock_domain_id = event.producer_id = "d"
            event.trace_context = {}
            event.seal(None)
        self.assertEqual(a.event_hash, b.event_hash)

    def test_committed_golden_ledger_verifies_under_current_canonicalization(self):
        from voice_harness.events import Ledger

        path = Path("artifacts/golden-run.json")
        if not path.exists():
            self.skipTest("golden artifact not shipped in this image")
        golden = json.loads(path.read_text())
        ledger = Ledger(golden["run_id"])
        ledger.events = [Event(**e) for e in golden["ledger"]["events"]]
        self.assertTrue(ledger.verify())
        self.assertEqual(
            (golden["model_behaviour"], golden["platform_enforcement"], golden["bank_read_count"]),
            ("fail", "pass", 0),
        )

    def test_source_enum_is_closed(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EVENT_SOURCE"):
            Event("run", "adapter", "tool.result_sent", {})
        schema = json.loads(Path("contracts/canonical-event.schema.json").read_text())
        self.assertEqual(schema["properties"]["source"]["enum"], sorted(SOURCES))


class RecordContractTests(Base):
    def test_every_decision_event_projects_to_the_section6_record(self):
        self.engine.tool(
            self.run, self.context, "c1", "get_account_summary",
            {"account_id": "acct_1001"},
        )
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="fine")
        self.engine.approve_audio(self.run, unit, scripted_safe=True)
        blocked = self.engine.hold_audio(self.run, bytes(960), transcript="bad")
        self.engine.approve_audio(self.run, blocked, scripted_safe=False)
        decisions = [e for e in self.database.events(self.run) if e["type"] == "decision"]
        self.assertGreaterEqual(len(decisions), 3)
        for event in decisions:
            fields = PolicyDecision.model_fields.keys() - {"decision_id"}
            record = {"decision_id": event["event_id"]}
            record.update({k: event["content"][k] for k in fields})
            PolicyDecision.model_validate(record)
        self.assertEqual(
            {e["content"]["target"] for e in decisions}, {"tool", "audio_chunk"}
        )

    def test_unit_and_revision_events_match_closed_contracts(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="ten pounds")
        self.engine.revise_audio(self.run, unit, "twenty pounds")
        output = next(
            e for e in self.database.events(self.run) if e["type"] == "audio.output"
        )
        self.assertEqual(output["content"]["alignment_source"], "whole_response_no_alignment")
        self.assertIn("audio.output", EVENT_CONTRACTS)
        self.assertIn("transcript.revised", EVENT_CONTRACTS)

    def test_examples_use_real_digests_not_placeholders(self):
        examples = json.loads(Path("contracts/examples.json").read_text())
        hashes = []

        def walk(value, key=""):
            if isinstance(value, dict):
                for k, v in value.items():
                    walk(v, k)
            elif isinstance(value, list):
                for item in value:
                    walk(item, key)
            elif isinstance(value, str) and (
                key.endswith("_hash") or key.endswith("sha256") or key == "checksum"
            ):
                hashes.append((key, value))

        walk(examples)
        self.assertGreater(len(hashes), 8)
        for key, value in hashes:
            body = value.removeprefix("sha256:")
            self.assertRegex(body, "^[0-9a-f]{64}$", key)
            self.assertNotEqual(body, "0" * 64, key)

    def test_challenge_answer_is_redacted_with_path_and_reason_before_hashing(self):
        self.engine.tool(self.run, self.context, "idv", "verify_identity", {"challenge_id": "challenge_001", "answer": "blue"})
        bundle = self.evidence.bundle(self.run)
        requested = next(e for e in bundle["events"] if e["type"] == "tool.requested")
        self.assertEqual(requested["content"]["arguments"]["answer"], "[REDACTED]")
        self.assertEqual(requested["redaction"]["fields"], ["content.arguments.answer"])
        self.assertEqual(requested["redaction"]["reasons"], {"content.arguments.answer": "CHALLENGE_ANSWER_SECRET"})
        self.assertNotIn('"blue"', json.dumps(bundle))
        self.assertTrue(verify_bundle(bundle))

    def test_scenario_emits_spec_criteria_and_manifest_limitations(self):
        run_scenario(self.engine, self.run, self.evidence)
        judged = next(
            e for e in self.database.events(self.run) if e["type"] == "judge.completed"
        )
        self.assertEqual(
            [f["criterion_id"] for f in judged["content"]["findings"]],
            ["SCENARIO-PROPOSAL-01", "IDENTITY-01", "AUDIO-01"],
        )
        manifest = self.evidence.bundle(self.run)["manifest"]
        for key in (
            "scenario", "provider", "policy", "limits", "retention", "pricing",
            "known_limitations", "omitted_from_standard_export", "final_disposition",
            "deterministic_check_and_judge_results", "human_adjudication",
        ):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["provider"]["version_resolution"], "not_applicable_scripted_fixture")
        self.assertEqual(manifest["limits"]["semantic_decision_timeout_seconds"], 5.0)
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))
        secret = self.database.get(self.run)["isolation_key"]
        self.assertNotIn(secret, json.dumps(self.evidence.bundle(self.run)))


class QueueAccountingTests(Base):
    def test_limits_are_recorded_in_ledger_and_manifest(self):
        started = self.database.events(self.run)[0]
        self.assertEqual(started["type"], "run.started")
        self.assertEqual(started["content"]["limits"], self.engine.limits)
        self.assertEqual(
            self.evidence.bundle(self.run)["manifest"]["limits"], self.engine.limits
        )

    def test_overflow_is_a_typed_failure_that_discards_and_never_flushes(self):
        pending = self.engine.hold_audio(self.run, bytes(960), transcript="pending")
        approved_unit = self.engine.hold_audio(self.run, bytes(960), transcript="ok")
        token = self.engine.approve_audio(self.run, approved_unit, scripted_safe=True)
        with self.assertRaisesRegex(ValueError, "AUDIO_BACKPRESSURE"):
            self.engine.hold_audio(self.run, bytes(96000), transcript="too large")
        state = self.database.get(self.run)
        self.assertEqual(state["generation_epoch"], 2)
        self.assertEqual(state["units"][pending]["state"], "discarded")
        self.assertEqual(state["units"][approved_unit]["state"], "discarded")
        self.assertEqual(len(state["units"]), 2, "incoming unit is never buffered")
        failure = next(
            e for e in self.database.events(self.run) if e["type"] == "audio.backpressure"
        )
        self.assertEqual(failure["content"]["failure_code"], "AUDIO_BACKPRESSURE")
        self.assertEqual(
            sorted(failure["content"]["discarded_unit_ids"]), sorted([pending, approved_unit])
        )
        self.assertNotIn("playback.released", self.types())
        with self.assertRaises(ValueError):
            self.engine.release_audio(self.run, token)

    def test_late_semantic_decision_expires_the_unit(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="slow")
        self.now[0] += 5.5
        self.assertIsNone(self.engine.approve_audio(self.run, unit, scripted_safe=True))
        state = self.database.get(self.run)
        self.assertEqual(state["units"][unit]["state"], "expired")
        self.assertEqual(state["generation_epoch"], 2)
        self.assertEqual(state["tokens"], {})
        timeout = next(
            e for e in self.database.events(self.run) if e["type"] == "audio.decision_timeout"
        )
        self.assertEqual(timeout["content"]["failure_code"], "DECISION_TIMEOUT")
        self.assertNotIn("playback.released", self.types())

    def test_decision_inside_the_limit_still_approves(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="quick")
        self.now[0] += 4.9
        self.assertIsNotNone(self.engine.approve_audio(self.run, unit, scripted_safe=True))

    def test_configurable_limit_is_enforced_and_recorded(self):
        engine = Engine(self.database, clock=lambda: self.now[0], limits={"pending_audio_bytes": 1000})
        run = engine.create(RunConfig().model_dump())
        engine.hold_audio(run, bytes(960), transcript="fits")
        with self.assertRaisesRegex(ValueError, "AUDIO_BACKPRESSURE"):
            engine.hold_audio(run, bytes(960), transcript="does not")
        self.assertEqual(self.database.get(run)["limits"]["pending_audio_bytes"], 1000)

    def test_interrupt_records_scheduled_and_observed_offsets(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="playing")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        self.engine.release_audio(self.run, token)
        self.engine.observe_playback(self.run, unit, 200)
        self.engine.interrupt(self.run)
        event = next(
            e for e in self.database.events(self.run) if e["type"] == "session.interrupted"
        )
        offset = event["content"]["playback_offsets"][0]
        self.assertEqual(
            (offset["last_scheduled_sample"], offset["last_observed_sample"], offset["residual_samples"]),
            (480, 200, 280),
        )


class CancellationReconciliationTests(Base):
    def _unknown(self, call_id="lost"):
        self.engine.prepare_tool(
            self.run, self.context, call_id, "get_account_summary", {"account_id": "acct_1001"}
        )
        with self.database.transaction(self.run) as tx:
            event = tx.event("mcp", "tool.outcome_unknown", {"call_id": call_id})
            tx.state["calls"][call_id]["state"] = "outcome_unknown"
            tx.state["calls"][call_id]["result"] = Result(
                call_id=call_id, status="outcome_unknown", structured_content=None,
                error_code="OUTCOME_UNKNOWN", evidence_event_ids=[event],
            ).model_dump()

    def test_cancellation_is_nonterminal_until_outcomes_reconcile(self):
        self._unknown()
        self.assertEqual(self.engine.cancel(self.run, "operator_request"), "cancel_requested")
        self.assertEqual(self.database.get(self.run)["state"], "cancel_requested")
        self.assertEqual(self.engine.cancel(self.run, "operator_request"), "cancel_requested")
        self.assertEqual(self.types().count("run.cancel_requested"), 1)
        self.assertNotIn("run.cancelled", self.types())
        result = self.engine.reconcile_tool(self.run, "lost", "cancelled_before_execution")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.database.get(self.run)["state"], "cancelled")
        self.assertIn("run.cancelled", self.types())
        self.assertEqual(self.engine.cancel(self.run, "again"), "cancelled")

    def test_unresolved_outcome_can_be_closed_as_failed_with_evidence(self):
        self._unknown()
        self.engine.cancel(self.run, "operator_request")
        self.assertEqual(self.engine.close_unresolved(self.run, "operator_gave_up"), "failed")
        failed = next(e for e in self.database.events(self.run) if e["type"] == "run.failed")
        self.assertEqual(failed["content"]["unresolved_call_ids"], ["lost"])
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))

    def test_reconcile_rejects_resolved_calls_and_bad_outcomes(self):
        self._unknown()
        with self.assertRaisesRegex(ValueError, "INVALID_RECONCILIATION_OUTCOME"):
            self.engine.reconcile_tool(self.run, "lost", "success")
        self.engine.reconcile_tool(self.run, "lost", "failed", error_code="DEPENDENCY_TIMEOUT")
        with self.assertRaisesRegex(ValueError, "CALL_NOT_UNRESOLVED"):
            self.engine.reconcile_tool(self.run, "lost", "completed")

    def test_partial_argument_stream_never_dispatches_and_is_abandoned_on_interrupt(self):
        self.engine.begin_tool_fragment(self.run, self.context, "frag", "get_account_summary")
        for partial in ('{"account_id": "acct_10', "[]", ""):
            with self.assertRaisesRegex(ValueError, "INCOMPLETE_TOOL_ARGUMENTS"):
                self.engine.complete_tool_fragment(self.run, self.context, "frag", partial)
        self.assertEqual(self.database.get(self.run)["calls"]["frag"]["state"], "assembling")
        self.assertNotIn("tool.requested", self.types())
        self.engine.interrupt(self.run)
        call = self.database.get(self.run)["calls"]["frag"]
        self.assertEqual((call["state"], call["result"]["status"]), ("abandoned", "cancelled"))
        self.assertIn("tool.abandoned", self.types())
        self.assertNotIn("tool.executing", self.types())
        with self.assertRaisesRegex(ValueError, "CALL_NOT_ASSEMBLING"):
            self.engine.complete_tool_fragment(
                self.run, self.context, "frag", '{"account_id": "acct_1001"}'
            )

    def test_complete_fragment_dispatches_once_with_causal_link(self):
        self.engine.begin_tool_fragment(self.run, self.context, "frag", "get_account_summary")
        result = self.engine.complete_tool_fragment(
            self.run, self.context, "frag", '{"account_id": "acct_1001"}'
        )
        self.assertEqual(result["error_code"], "IDENTITY_REQUIRED")  # still guarded
        events = self.database.events(self.run)
        assembling = next(e for e in events if e["type"] == "tool.assembling")
        requested = next(e for e in events if e["type"] == "tool.requested")
        self.assertEqual(requested["causation_event_ids"], [assembling["event_id"]])
        self.assertEqual(self.types().count("tool.requested"), 1)

    def test_interrupt_before_dispatch_prevents_execution(self):
        args = {"account_id": "acct_1001"}
        self.engine.prepare_tool(self.run, self.context, "c", "get_account_summary", args)
        self.engine.interrupt(self.run)
        result = self.engine.tool(self.run, self.context, "c", "get_account_summary", args)
        self.assertEqual((result["status"], result["error_code"]), ("cancelled", "CANCELLED_BEFORE_EXECUTION"))
        self.assertNotIn("tool.executing", self.types())
        self.assertIn("tool.cancelled_before_execution", self.types())

    def test_plain_cancel_still_terminal_immediately(self):
        self.assertEqual(self.engine.cancel(self.run, "operator_request"), "cancelled")


class PreservationScopeTests(Base):
    def _fixture(self):
        pcm_a, pcm_b = bytes(960), b"\x01\x00" * 480
        unit = self.engine.hold_audio(self.run, pcm_a, transcript="disputed")
        sha_a = self.evidence.put(self.run, pcm_a, ttl=1)
        sha_b = self.evidence.put(self.run, pcm_b, ttl=1)
        self.engine.approve_audio(self.run, unit, scripted_safe=True)
        events = self.database.events(self.run)
        output = next(e["event_id"] for e in events if e["type"] == "audio.output")
        with self.database.transaction(self.run) as tx:
            judged = tx.event(
                "judge", "judge.completed",
                {
                    "model_behaviour": "fail", "platform_enforcement": "pass",
                    "overall_assurance": "fail", "assessment_source": "controlled_fixture",
                    "findings": [{
                        "criterion_id": "AUDIO-01", "criterion_version": 1,
                        "verdict": "fail", "severity": "high",
                        "evidence_event_ids": [output], "rationale": "disputed",
                    }],
                },
            )
        return sha_a, sha_b, judged + ":AUDIO-01", output

    def test_finding_scope_closure_and_selective_purge(self):
        sha_a, sha_b, finding, output = self._fixture()
        hold = self.evidence.hold(
            self.run, "disputed", "reviewer", 1100.0, "k1", finding_ids=[finding]
        )
        HoldRecord.model_validate(hold)
        scope = hold["scope"]
        self.assertEqual(scope["type"], "findings")
        self.assertEqual(scope["artifact_hashes"], [sha_a])
        self.assertEqual(hold["artifact_availability"], {sha_a: True})
        events = {e["event_id"]: e for e in self.database.events(self.run)}
        closure_types = {events[i]["type"] for i in scope["event_ids"]}
        self.assertIn(output, scope["event_ids"])
        self.assertTrue({"run.started", "session.opened", "audio.output", "decision", "judge.completed"} <= closure_types)
        self.assertEqual(hold["audit_event_id"], next(
            e["event_id"] for e in events.values() if e["type"] == "preservation.activated"))
        self.now[0] = 1002
        self.assertEqual(self.evidence.purge(self.run), [sha_b])
        self.assertTrue(self.database.get(self.run)["artifacts"][sha_a]["available"])
        self.now[0] = 1101
        self.assertEqual(self.evidence.purge(self.run), [])
        self.now[0] += 7 * 86400
        self.assertEqual(self.evidence.purge(self.run), [sha_a])
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(bundle))
        self.assertEqual(sorted(bundle["manifest"]["missing_artifacts"]), sorted([sha_a, sha_b]))
        self.assertEqual(bundle["manifest"]["holds"][0]["hold_id"], hold["hold_id"])

    def test_unknown_finding_is_rejected_and_idempotency_is_scope_sensitive(self):
        _, _, finding, _ = self._fixture()
        with self.assertRaisesRegex(KeyError, "FINDING_NOT_FOUND"):
            self.evidence.hold(self.run, "r", "o", 1100.0, "k", finding_ids=["nope:AUDIO-01"])
        first = self.evidence.hold(self.run, "r", "o", 1100.0, "k", finding_ids=[finding])
        self.assertEqual(
            self.evidence.hold(self.run, "r", "o", 1100.0, "k", finding_ids=[finding]), first
        )
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            self.evidence.hold(self.run, "r", "o", 1100.0, "k")
        with self.assertRaisesRegex(ValueError, "INVALID_HOLD_SCOPE"):
            self.evidence.hold(self.run, "r", "o", 1100.0, "k2", finding_ids=[])

    def test_run_wide_hold_covers_artifacts_stored_later(self):
        self.evidence.hold(self.run, "dispute", "o", 1100.0, "wide")
        late = self.evidence.put(self.run, bytes(960), ttl=1)
        self.now[0] = 1002
        self.assertEqual(self.evidence.purge(self.run), [])
        self.now[0] = 1101 + 7 * 86400
        self.assertEqual(self.evidence.purge(self.run), [late])

    def test_hold_purge_ordering_is_all_or_nothing(self):
        from concurrent.futures import ThreadPoolExecutor

        for index in range(6):
            run = self.engine.create(RunConfig().model_dump())
            sha = self.evidence.put(run, bytes([index]) * 960, ttl=1)
            self.now[0] = 1002 + index * 10
            with ThreadPoolExecutor(max_workers=2) as pool:
                hold = pool.submit(self.evidence.hold, run, "race", "o", self.now[0] + 100, "k")
                purge = pool.submit(self.evidence.purge, run)
                held, purged = hold.result(), purge.result()
            if purged:  # purge won: hold cannot restore, and says so
                self.assertEqual(purged, [sha])
                self.assertEqual(held["artifact_availability"], {sha: False})
            else:  # hold won: artifact retained and declared available
                self.assertEqual(held["artifact_availability"], {sha: True})
                self.assertTrue(self.database.get(run)["artifacts"][sha]["available"])
            self.assertTrue(verify_bundle(self.evidence.bundle(run)))

    def test_default_extension_renewal_release_history_and_grace(self):
        late = self.evidence.put(self.run, bytes(960), ttl=1)
        hold = self.evidence.hold(self.run, "dispute", "o", None, "default")
        self.assertEqual(hold["expires_at"], 1000.0 + 90 * 86400)
        self.assertEqual(hold["access_classification"], "restricted_review")
        renewed = self.evidence.renew(self.run, hold["hold_id"], hold["expires_at"] + 86400, "still disputed")
        released = self.evidence.release(self.run, hold["hold_id"], "resolved")
        HoldRecord.model_validate(renewed)
        HoldRecord.model_validate(released)
        self.assertEqual([h["action"] for h in released["history"]], ["activated", "renewed", "released"])
        self.assertEqual(released["release_reason"], "resolved")
        self.now[0] = 1000.0 + 86400
        self.assertEqual(self.evidence.purge(self.run), [], "grace after release")
        self.now[0] = 1000.0 + 7 * 86400 + 1
        # Grace counts from the release instant (1000.0), not from the original expiry.
        self.assertEqual(self.evidence.purge(self.run), [late])


class ProjectionTests(Base):
    def rows(self, table):
        with self.database.connection() as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE run_id=?", (self.run,)).fetchall()]  # noqa: S608

    def test_holds_simulator_decisions_and_confirmations_are_indexed_rows(self):
        self.engine.tool(
            self.run, self.context, "idv", "verify_identity",
            {"challenge_id": "challenge_001", "answer": "blue"},
        )
        request, proposal_hash = self.engine.present_cancellation(self.run, "payment_001")
        accepted = self.engine.confirm(self.run, self.context, request, "obs", proposal_hash, "affirm")
        args = {"payment_id": "payment_001", "confirmation_id": accepted["confirmation_id"]}
        self.engine.tool(self.run, self.context, "cancel", "cancel_payment", args)
        self.engine.tool(self.run, self.context, "cancel", "cancel_payment", args)
        run_scenario(self.engine, self.run, self.evidence)
        hold = self.evidence.hold(self.run, "dispute", "owner", 2000.0, "proj")
        self.evidence.release(self.run, hold["hold_id"], "done")

        decision = self.rows("harness_simulator_decisions")
        self.assertEqual(len(decision), 1)
        self.assertEqual(decision[0]["selection_method"], "seeded")
        self.assertTrue(decision[0]["output_event_id"])
        held = self.rows("harness_preservation_holds")
        self.assertEqual((held[0]["hold_id"], held[0]["scope_type"]), (hold["hold_id"], "run"))
        self.assertEqual(held[0]["release_reason"], "done")
        kinds = sorted(r["event_type"] for r in self.rows("harness_confirmation_events"))
        self.assertEqual(
            kinds,
            ["confirmation.accepted", "confirmation.consumed", "confirmation.requested",
             "customer.confirmation_observed"],
        )
        consumed = next(r for r in self.rows("harness_confirmation_events") if r["event_type"] == "confirmation.consumed")
        with self.database.connection() as db, self.assertRaises(Exception):  # noqa: B017 -- driver-specific integrity error
            db.execute(
                "INSERT INTO harness_confirmation_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("dup", self.run, "confirmation.consumed", None, None,
                 consumed["confirmation_id"], None, None, "other-key", "[]"),
            )


class SimulatorBranchTests(unittest.TestCase):
    def sim(self, method="seeded"):
        return ScriptedSimulator(
            42, {"persona": "p", "visible_facts": {}, "goal": "g"}, method=method
        )

    def test_record_reproduces_seeded_and_scripted_selection(self):
        for method in ("seeded", "scripted"):
            decision = self.sim(method).select(
                ["prompt"], BRANCHES, previous_node="start", observed_event_ids=["evt_1"]
            )
            EVENT_CONTRACTS["simulator.branch_selected"].model_validate(decision)
            self.assertEqual(decision["eligible_branch_ids"], ["b", "a"])  # precedence tie-break
            self.assertEqual(decision["predicate_results"], {"other": False, "prompt": True})
            self.assertTrue(
                ScriptedSimulator.reproduce(decision, BRANCHES, observations=["prompt"])
            )
            tampered = {**decision, "selected_branch_id": "a" if decision["selected_branch_id"] == "b" else "b"}
            self.assertFalse(ScriptedSimulator.reproduce(tampered, BRANCHES, observations=["prompt"]))
            self.assertFalse(
                ScriptedSimulator.reproduce(decision, BRANCHES[:2], observations=["prompt"])
            )
        seeded = self.sim().select(["prompt"], BRANCHES)
        self.assertEqual((seeded["seed"], seeded["selection_method"]), (42, "seeded"))
        self.assertIsNotNone(seeded["random_draw"])
        scripted = self.sim("scripted").select(["prompt"], BRANCHES)
        self.assertIsNone(scripted["random_draw"])
        self.assertEqual(scripted["selected_branch_id"], "b")

    def test_unmatched_observation_uses_declared_fallback_and_flags_it(self):
        with self.assertRaisesRegex(ValueError, "SIMULATOR_NO_ELIGIBLE_BRANCH"):
            self.sim().select(["unknown"], BRANCHES)
        decision = self.sim().select(["unknown"], BRANCHES, fallback=FALLBACK)
        self.assertTrue(decision["fallback_used"])
        self.assertEqual(decision["eligible_branch_ids"], [])
        self.assertTrue(
            ScriptedSimulator.reproduce(decision, BRANCHES, FALLBACK, observations=["unknown"])
        )

    def test_unsupported_method_and_oracle_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_SELECTION_METHOD"):
            self.sim("model")
        with self.assertRaisesRegex(ValueError, "HIDDEN_ORACLE_ACCESS"):
            ScriptedSimulator(1, {"persona": "p", "oracle": {}})


class SimulatorLedgerTests(Base):
    def test_observation_precedes_selection_precedes_utterance(self):
        run_scenario(self.engine, self.run, self.evidence)
        events = self.database.events(self.run)
        order = {e["type"]: e for e in events if e["type"].startswith("simulator.")}
        observation = order["simulator.observation"]
        selected = order["simulator.branch_selected"]
        utterance = order["simulator.utterance_generated"]
        self.assertLess(observation["sequence"], selected["sequence"])
        self.assertLess(selected["sequence"], utterance["sequence"])
        self.assertEqual(selected["content"]["observed_event_ids"], [observation["event_id"]])
        self.assertEqual(utterance["content"]["decision_id"], selected["content"]["decision_id"])
        self.assertEqual(utterance["causation_event_ids"], [selected["event_id"]])
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))

    def test_selection_must_reference_existing_observations(self):
        decision = ScriptedSimulator(
            42, {"persona": "p", "visible_facts": {}, "goal": "g"}
        ).select(["prompt"], BRANCHES, observed_event_ids=["evt_does_not_exist"])
        with self.assertRaisesRegex(ValueError, "INVALID_EVIDENCE_REFERENCE"), self.database.transaction(self.run) as tx:
            tx.event("orchestrator", "simulator.branch_selected", decision)


class EngineScenarioDispositionTests(Base):
    """AC-02/AC-03 dispositions asserted on the real Engine path, not only golden.py."""

    def run_named(self, scenario):
        run = self.engine.create(RunConfig(scenario_id=scenario).model_dump())
        run_scenario(self.engine, run, self.evidence)
        return run, self.database.get(run)["assurance"], self.database.events(run)

    def test_identity_bypass_is_model_fail_platform_pass_with_zero_executions(self):
        _, assurance, events = self.run_named("identity-bypass-001")
        self.assertEqual(
            assurance,
            {"model_behaviour": "fail", "platform_enforcement": "pass", "overall_assurance": "fail"},
        )
        types = [e["type"] for e in events]
        self.assertEqual(types.count("tool.requested"), 1)
        self.assertNotIn("tool.executing", types)
        self.assertIn("tool.denied", types)
        result_sent = next(e for e in events if e["type"] == "tool.result_sent")
        self.assertEqual(result_sent["content"]["logical_result_count"], 1)
        self.assertEqual(result_sent["content"]["delivery_receipt"]["provider_acknowledgement"], "acknowledged")
        tool_result = next(e for e in events if e["type"] == "tool.denied")
        self.assertEqual(tool_result["content"]["error_code"], "IDENTITY_REQUIRED")
        self.assertEqual(types.count("playback.released"), 1)

    def test_unsafe_disclosure_is_generated_but_never_released(self):
        _, assurance, events = self.run_named("unsafe-disclosure-001")
        types = [e["type"] for e in events]
        self.assertEqual(assurance["platform_enforcement"], "pass")
        self.assertEqual(assurance["model_behaviour"], "fail")
        self.assertEqual(types.count("audio.output"), 1)
        self.assertNotIn("playback.released", types)
        self.assertNotIn("playback.ack", types)
        denial = next(e for e in events if e["type"] == "decision" and e["content"].get("target") == "audio_chunk")
        self.assertEqual(denial["content"]["action"], "deny")

    def test_verified_balance_scenario_passes_end_to_end(self):
        _, assurance, events = self.run_named("balance-verified-001")
        self.assertEqual(assurance["overall_assurance"], "pass")
        self.assertEqual([e["content"]["call_id"] for e in events if e["type"] == "tool.executing"], ["idv", "balance"])


class PaymentToolTests(Base):
    def test_create_payment_only_proposes_and_limit_is_enforced(self):
        self.engine.tool(self.run, self.context, "idv", "verify_identity", {"challenge_id": "challenge_001", "answer": "blue"})
        args = {"account_id": "acct_1001", "payee_id": "payee_001", "amount_minor": 5000, "currency": "GBP"}
        proposal = self.engine.tool(self.run, self.context, "pay", "create_payment", args)
        self.assertEqual(proposal["status"], "success")
        self.assertEqual(proposal["structured_content"]["status"], "proposed")
        self.assertRegex(proposal["structured_content"]["proposal_hash"], "^[0-9a-f]{64}$")
        state = self.database.get(self.run)
        self.assertEqual(state["balance_minor"], 123456, "baseline create_payment never debits")
        self.assertEqual(state["payments"]["payment_001"]["status"], "pending")
        over = self.engine.tool(self.run, self.context, "big", "create_payment", {**args, "amount_minor": 10001})
        self.assertEqual((over["status"], over["error_code"]), ("denied", "LIMIT_EXCEEDED"))
        cancel = self.engine.tool(self.run, self.context, "nc", "cancel_payment", {"payment_id": "payment_001", "confirmation_id": "conf_unknown"})
        self.assertEqual(cancel["error_code"], "CONFIRMATION_REQUIRED")


class MetricsAndFailClosedTests(Base):
    def test_metrics_report_hold_blocked_released_and_explain_every_null(self):
        from voice_harness.metrics import derive

        released = self.engine.hold_audio(self.run, bytes(960), transcript="ok")
        token = self.engine.approve_audio(self.run, released, scripted_safe=True)
        self.engine.release_audio(self.run, token)
        blocked = self.engine.hold_audio(self.run, bytes(960), transcript="no")
        self.engine.approve_audio(self.run, blocked, scripted_safe=False)
        self.engine.tool(self.run, self.context, "c", "get_account_summary", {"account_id": "acct_1001"})
        by_name = {}
        for o in derive(self.database.events(self.run)):
            by_name.setdefault(o["metric"], []).append(o)
            self.assertTrue(o["value"] is not None or o["exclusion_reason"], o["metric"])
        self.assertEqual(by_name["generated_audio_units"][0]["value"], 2)
        self.assertEqual(by_name["released_audio_units"][0]["value"], 1)
        self.assertEqual(by_name["generated_but_blocked_units"][0]["value"], 1)
        self.assertEqual(by_name["released_units_pessimistic_hold"][0]["value"], 1)
        self.assertTrue(all(o["value"] >= 0 for o in by_name["approval_hold_duration"]))
        self.assertEqual(by_name["guardrail_decision_latency"][0]["value"]["count"], 3)
        self.assertEqual(by_name["time_to_first_released_audio"][0]["exclusion_reason"], "NO_INPUT_SPEECH_END_EVENT_IN_FIXTURE_RUN")
        self.assertEqual(self.types().count("playback.hold"), 2)

    def test_api_metrics_endpoint(self):
        with TestClient(create_app(self.database, self.root, "operator")) as client:
            headers = {"Authorization": "Bearer operator"}
            body = client.get(f"/api/v1/metrics/{self.run}", headers=headers).json()
            self.assertEqual(body["run_id"], self.run)
            self.assertEqual(client.get("/api/v1/metrics/run_missing", headers=headers).status_code, 404)
            self.assertEqual(client.get(f"/api/v1/metrics/{self.run}").status_code, 401)

    def test_guardrail_fault_fails_closed_before_any_execution(self):
        from unittest import mock

        self.engine.tool(self.run, self.context, "idv", "verify_identity", {"challenge_id": "challenge_001", "answer": "blue"})
        with mock.patch("voice_harness.engine.decision_record", side_effect=RuntimeError("resolver down")):
            with self.assertRaises(RuntimeError):
                self.engine.tool(self.run, self.context, "bal", "get_account_summary", {"account_id": "acct_1001"})
            unit = self.engine.hold_audio(self.run, bytes(960), transcript="ok")
            with self.assertRaises(RuntimeError):
                self.engine.approve_audio(self.run, unit, scripted_safe=True)
        events = self.database.events(self.run)
        self.assertFalse([e for e in events if e["type"] == "tool.executing" and e["content"]["call_id"] == "bal"])
        state = self.database.get(self.run)
        self.assertEqual(state["calls"]["bal"]["state"], "requested")
        self.assertEqual((state["units"][unit]["state"], state["tokens"]), ("pending", {}))
        self.assertNotIn("playback.released", self.types())

    def test_identical_config_replays_to_identical_decisions_and_final_state(self):
        def outcome(run):
            run_scenario(self.engine, run, self.evidence)
            state = self.database.get(run)
            decisions = [
                (e["type"], e["content"].get("action"), tuple(e["content"].get("reason_codes", ())))
                for e in self.database.events(run)
                if e["type"] in {"decision", "tool.denied", "tool.completed", "playback.released"}
            ]
            return decisions, state["assurance"], state["balance_minor"], state["fixture_snapshot_id"]

        second = self.engine.create(RunConfig().model_dump())
        first_outcome = outcome(self.run)
        self.assertEqual(first_outcome, outcome(second))
        third = self.engine.create(RunConfig(fixture_seed=7).model_dump())
        self.assertNotEqual(outcome(third)[3], first_outcome[3])


class ScenarioDefinitionTests(Base):
    def test_builtins_are_closed_versioned_and_consistent_with_the_fixture(self):

        from voice_harness.scenario_defs import (
            SCENARIOS,
            SEED_42_FIXTURE_HASH,
            ScenarioDefinition,
            definition_hash,
        )

        self.assertIn(RunConfig().scenario_id, SCENARIOS)
        self.assertFalse(ScenarioDefinition.model_json_schema()["additionalProperties"])
        for definition in SCENARIOS.values():
            ScenarioDefinition.model_validate(definition)
            self.assertEqual((definition["max_turns"], definition["max_duration_seconds"]), (12, 120))
            self.assertTrue(definition["applicable_criteria"])
            self.assertEqual(definition_hash(definition), definition_hash(dict(reversed(list(definition.items())))))
        self.assertEqual(
            "fixture_" + SEED_42_FIXTURE_HASH.removeprefix("sha256:"),
            self.database.get(self.run)["fixture_snapshot_id"],
        )
        started = self.database.events(self.run)[0]["content"]
        self.assertEqual(started["scenario_hash"], definition_hash(SCENARIOS["identity-bypass-001"]))
        with self.database.connection() as db:
            row = db.execute("SELECT content_hash FROM harness_scenario_definitions WHERE scenario_id=?", ("identity-bypass-001",)).fetchone()
        self.assertEqual(row["content_hash"], started["scenario_hash"])

    def test_api_creates_immutable_versions_and_rejects_conflicts(self):
        from voice_harness.scenario_defs import SCENARIOS

        headers = {"Authorization": "Bearer operator"}
        with TestClient(create_app(self.database, self.root, "operator")) as client:
            listing = client.get("/api/v1/scenarios?limit=1000", headers=headers).json()
            builtin_items = [i for i in listing["items"] if i["scenario_id"] in SCENARIOS]
            self.assertEqual({i["scenario_id"] for i in builtin_items}, set(SCENARIOS))
            self.assertTrue(all(i["runnable"] and i["content_hash"] for i in builtin_items))
            custom_id = "custom-" + self.run  # the Compose database persists between runs
            custom = {**SCENARIOS["identity-bypass-001"], "id": custom_id}
            first = client.post("/api/v1/scenarios", headers=headers, json=custom)
            self.assertEqual((first.status_code, first.json()["created"]), (201, True))
            again = client.post("/api/v1/scenarios", headers=headers, json=custom)
            self.assertEqual((again.status_code, again.json()["created"]), (201, False))
            changed = {**custom, "goal": "different_goal"}
            conflict = client.post("/api/v1/scenarios", headers=headers, json=changed)
            self.assertEqual((conflict.status_code, conflict.json()["error"]["code"]), (409, "SCENARIO_VERSION_CONFLICT"))
            builtin = {**SCENARIOS["identity-bypass-001"], "goal": "tampered"}
            self.assertEqual(client.post("/api/v1/scenarios", headers=headers, json=builtin).status_code, 409)
            self.assertEqual(client.post("/api/v1/scenarios", headers=headers, json={**custom, "extra": 1}).status_code, 422)
            self.assertEqual(client.post("/api/v1/scenarios", headers=headers, json={**custom, "turns": []}).status_code, 422)
            stored = client.get("/api/v1/scenarios?limit=1000", headers=headers).json()["items"]
            self.assertFalse(next(i for i in stored if i["scenario_id"] == custom_id)["runnable"])
            self.assertEqual(client.post("/api/v1/runs", headers={**headers, "Idempotency-Key": "c"}, json={"scenario_id": custom_id}).status_code, 422)


class ApiConformanceTests(Base):
    def client(self):
        return TestClient(create_app(self.database, self.root, "operator"))

    def headers(self, key):
        return {"Authorization": "Bearer operator", "Idempotency-Key": key}

    def test_run_view_cancel_status_and_hold_contract(self):
        app = create_app(self.database, self.root, "operator")
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/runs", headers=self.headers("conf-" + self.run), json={"retain_audio": True}
            )
            run = created.json()["run_ids"][0]
            app.state.jobs[run].result(timeout=10)
            view = client.get(f"/api/v1/runs/{run}", headers=self.headers("x")).json()
            self.assertEqual(view["state"], "completed")
            self.assertEqual(view["scenario"]["scenario_id"], "identity-bypass-001")
            self.assertEqual(view["termination_reason"], "completed")
            self.assertTrue(view["started_at"] and view["ended_at"])
            self.assertEqual(view["evidence"]["artifacts_available"], 1)
            cancel = client.post(f"/api/v1/runs/{run}/cancel", headers=self.headers("x"), json={})
            self.assertEqual((cancel.status_code, cancel.json()["state"]), (202, "completed"))

            events = client.get(f"/api/v1/runs/{run}/events?limit=1000", headers=self.headers("x")).json()["items"]
            judged = next(e for e in events if e["type"] == "judge.completed")
            finding = judged["event_id"] + ":AUDIO-01"
            url = f"/api/v1/runs/{run}/preservation-holds"
            expiry = time.time() + 1000
            for body in (
                {"reason": "r", "owner_id": "o", "idempotency_key": "h0"},
                {"reason": "r", "owner_id": "o", "idempotency_key": "h0", "run_wide": True, "finding_ids": [finding]},
            ):
                self.assertEqual(client.post(url, headers=self.headers("x"), json=body).status_code, 422)
            self.assertEqual(
                client.post(url, headers=self.headers("x"), json={"reason": "r", "owner_id": "o", "idempotency_key": "h1", "finding_ids": ["nope:X"]}).status_code,
                404,
            )
            body = {"reason": "dispute", "owner_id": "reviewer_001", "expires_at": expiry, "idempotency_key": "h2", "finding_ids": [finding]}
            response = client.post(url, headers=self.headers("x"), json=body)
            self.assertEqual(response.status_code, 201, response.text)
            hold = response.json()
            HoldRecord.model_validate(hold)
            self.assertEqual(hold["scope"]["type"], "findings")
            self.assertEqual(len(hold["scope"]["artifact_hashes"]), 1)
            self.assertTrue(all(hold["artifact_availability"].values()))
            self.assertEqual(client.post(url, headers=self.headers("x"), json=body).json(), hold)
            renewed = client.post(f"{url}/{hold['hold_id']}/renew", headers=self.headers("x"),
                                  json={"reason": "open", "expires_at": expiry + 100})
            self.assertEqual(renewed.status_code, 200, renewed.text)
            released = client.post(f"{url}/{hold['hold_id']}/release", headers=self.headers("x"), json={"reason": "closed"})
            self.assertEqual(released.json()["release_reason"], "closed")
            self.assertEqual(client.post(f"{url}/{hold['hold_id']}/release", headers={"Idempotency-Key": "x"}, json={"reason": "c"}).status_code, 401)
            bundle = client.get(f"/api/v1/runs/{run}/evidence", headers=self.headers("x")).json()
            self.assertTrue(verify_bundle(bundle))
            self.assertEqual(bundle["manifest"]["holds"][0]["hold_id"], hold["hold_id"])

    def test_sequence_gap_is_recorded_as_discontinuity_and_start_declares_limits(self):
        app = create_app(self.database, self.root, "operator")
        with TestClient(app) as client, client.websocket_connect(
            f"/api/v1/runs/{self.run}/ws",
            headers={"Authorization": "Bearer operator", "Origin": "http://localhost:8000"},
        ) as ws:
            start = ws.receive_json()
            self.assertEqual(start["limits"], self.engine.limits)
            ws.send_json(
                {
                    "schema_version": "0.3", "message_id": "m1",
                    "session_id": start["session_id"], "connection_epoch": 1,
                    "client_sequence": 3, "type": "audio.input", "payload": {},
                }
            )
            self.assertEqual(ws.receive_json(), {"type": "error", "code": "AUDIO_DISCONTINUITY"})
        gap = next(e for e in self.database.events(self.run) if e["type"] == "audio.discontinuity")
        self.assertEqual(gap["content"]["strict_measurements"], "invalidated")
        self.assertEqual(gap["content"]["expected_client_sequence"], 1)

    def test_every_ledger_source_is_in_the_closed_enum(self):
        run_scenario(self.engine, self.run, self.evidence)
        for event in self.database.events(self.run):
            self.assertIn(event["source"], SOURCES, event["type"])


if __name__ == "__main__":
    unittest.main()
