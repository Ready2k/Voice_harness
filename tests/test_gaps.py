"""Phase 1 gap closures: TL-02, LT-02, WS-02, SC-03, FN-02, PB-16, EN-06 (PB-17 is in tests/browser)."""

import asyncio
import base64
import copy
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.clock_runtime import (
    MAX_AGE_SECONDS,
    SAMPLES_PER_WINDOW,
    ClockSyncSession,
)
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.events import CAUSATION_UNKNOWN_REASONS, Event
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.inbound_queue import InboundQueue
from voice_harness.interruption import receipt
from voice_harness.scenario_defs import SCENARIOS
from voice_harness.scenario_stop import StopEvaluator
from voice_harness.scenarios import run_scenario
from voice_harness.storage import Database

ARGS = {"account_id": "acct_1001"}
VERIFY = {"challenge_id": "challenge_001", "answer": "blue"}


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

    def call(self, call_id):
        return self.database.get(self.run)["calls"][call_id]

    def outcomes(self, call_id):
        return [
            e["type"]
            for e in self.database.events(self.run)
            if e["type"] in {"tool.completed", "tool.failed", "tool.denied"}
            and e["content"].get("call_id") == call_id
        ]

    def verified(self):
        self.engine.tool(self.run, self.context, "idv", "verify_identity", VERIFY)

    def dispatch(self, call_id="bal"):
        self.verified()
        return self.engine.dispatch_tool(
            self.run, self.context, call_id, "get_account_summary", ARGS
        )


class ToolInFlightTests(Base):
    def test_dispatched_call_is_observably_executing_before_the_outcome(self):
        self.assertIsNone(self.dispatch())
        self.assertEqual(self.call("bal")["state"], "executing")
        self.assertIsNone(self.call("bal")["result"])
        self.assertIn("tool.executing", self.types())
        self.assertEqual(self.outcomes("bal"), [])
        result = self.engine.commit_tool(
            self.run, self.context, "bal", "get_account_summary", ARGS
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.call("bal")["state"], "completed")

    def test_redispatch_of_an_executing_call_does_not_duplicate_evidence(self):
        self.dispatch()
        self.assertIsNone(
            self.engine.dispatch_tool(
                self.run, self.context, "bal", "get_account_summary", ARGS
            )
        )
        self.assertEqual(
            sum(
                e["type"] == "tool.executing" and e["content"]["call_id"] == "bal"
                for e in self.database.events(self.run)
            ),
            1,
        )

    def test_per_call_cancel_during_execution_is_nonterminal_then_cancelled_before_execution(self):
        self.dispatch()
        self.assertIsNone(self.engine.request_tool_cancel(self.run, "bal", "barge_in"))
        call = self.call("bal")
        self.assertEqual((call["state"], call["result"]), ("cancel_requested", None))
        self.assertIn("tool.cancel_requested", self.types())
        # Idempotent: a second request adds no evidence.
        self.engine.request_tool_cancel(self.run, "bal", "barge_in")
        self.assertEqual(self.types().count("tool.cancel_requested"), 1)
        result = self.engine.commit_tool(
            self.run, self.context, "bal", "get_account_summary", ARGS
        )
        self.assertEqual((result["status"], result["error_code"]), ("cancelled", "CANCELLED_BEFORE_EXECUTION"))
        self.assertEqual(self.call("bal")["state"], "cancelled_before_execution")
        # Authoritative evidence: the bank never ran.
        self.assertEqual(self.outcomes("bal"), [])
        outcome = next(
            e for e in self.database.events(self.run) if e["type"] == "tool.cancelled_before_execution"
        )
        cancel = next(e for e in self.database.events(self.run) if e["type"] == "tool.cancel_requested")
        self.assertIn(cancel["event_id"], outcome["causation_event_ids"])

    def test_cancel_after_the_outcome_is_a_noop_returning_the_final_result(self):
        self.dispatch()
        done = self.engine.commit_tool(self.run, self.context, "bal", "get_account_summary", ARGS)
        self.assertEqual(self.engine.request_tool_cancel(self.run, "bal", "late"), done)
        self.assertNotIn("tool.cancel_requested", self.types())

    def test_cancel_before_dispatch_still_settles_immediately(self):
        self.verified()
        self.engine.prepare_tool(self.run, self.context, "early", "get_account_summary", ARGS)
        result = self.engine.request_tool_cancel(self.run, "early", "barge_in")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.call("early")["state"], "cancelled_before_execution")

    def test_interrupt_during_execution_requests_cancel_instead_of_claiming_cancelled(self):
        self.dispatch()
        self.engine.interrupt(self.run)
        self.assertEqual(self.call("bal")["state"], "cancel_requested")
        self.assertIsNone(self.call("bal")["result"])
        self.assertNotIn("tool.cancelled_before_execution", self.types())

    def test_run_cancel_waits_for_the_in_flight_call_then_completes(self):
        self.dispatch()
        self.assertEqual(self.engine.cancel(self.run, "operator_request"), "cancel_requested")
        self.assertEqual(self.database.get(self.run)["state"], "cancel_requested")
        self.engine.commit_tool(self.run, self.context, "bal", "get_account_summary", ARGS)
        self.assertEqual(self.database.get(self.run)["state"], "cancelled")
        self.assertIn("run.cancelled", self.types())

    def test_lost_result_after_cancel_request_is_outcome_unknown_and_reconcilable(self):
        self.dispatch()
        self.engine.request_tool_cancel(self.run, "bal", "barge_in")
        self.engine.cancel(self.run, "operator_request")
        unknown = self.engine.mark_outcome_unknown(self.run, "bal", "DELIVERY_LOST")
        self.assertEqual((unknown["status"], unknown["error_code"]), ("outcome_unknown", "OUTCOME_UNKNOWN"))
        self.assertEqual(self.database.get(self.run)["state"], "cancel_requested")
        self.engine.reconcile_tool(self.run, "bal", "cancelled_before_execution")
        self.assertEqual(self.database.get(self.run)["state"], "cancelled")
        with self.assertRaises(ValueError):
            self.engine.mark_outcome_unknown(self.run, "bal", "again")

    def test_combined_tool_path_is_unchanged_for_callers(self):
        self.verified()
        result = self.engine.tool(self.run, self.context, "bal", "get_account_summary", ARGS)
        self.assertEqual(result["status"], "success")
        types = self.types()
        self.assertLess(types.index("tool.executing"), types.index("tool.completed"))
        self.assertEqual(
            self.engine.tool(self.run, self.context, "bal", "get_account_summary", ARGS), result
        )


class ClockRuntimeTests(Base):
    def session(self):
        self.t = [100.0]

        def record(kind, content):
            with self.database.transaction(self.run) as tx:
                tx.event("orchestrator", kind, content)

        return ClockSyncSession(1, record, monotonic=lambda: self.t[0])

    def exchange(self, session, index, up=5.0, down=5.0, offset=500.0):
        """One four-timestamp exchange with server clock = client clock + offset."""
        client_send = 1000.0 + index * 50
        received = client_send + offset + up
        pong = session.on_ping(
            {"sample_id": f"{self.run}-s{index}-{session.round}", "round": 1, "client_send": client_send}, received
        )
        sent = pong["server_send"]
        # Server processing time is the server_send - server_receive gap (monotonic fake).
        client_receive = client_send + up + (sent - received) + down
        return session.on_sample(
            {"sample_id": pong["sample_id"], "client_receive": client_receive}
        )

    def rows(self):
        with self.database.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM harness_clock_sync_samples WHERE run_id=? ORDER BY sample_id",
                    (self.run,),
                ).fetchall()
            ]

    def test_eight_samples_form_a_window_and_are_persisted(self):
        session = self.session()
        for index in range(SAMPLES_PER_WINDOW - 1):
            self.exchange(session, index)
        self.assertIsNone(session.window)
        self.exchange(session, 7)
        self.assertIsNotNone(session.window)
        rows = self.rows()
        self.assertEqual(len(rows), 8)
        self.assertEqual(sum(r["chosen"] for r in rows), 1)
        self.assertEqual(rows[0]["profile_version"], "four-timestamp-v1")
        self.assertEqual(rows[0]["client_domain"], "browser_performance_now")
        self.assertIn("clock.window_established", self.types())
        self.assertEqual(self.types().count("clock.sync_sample"), 8)

    def test_window_reports_bounded_latency_then_goes_stale_after_60s(self):
        session = self.session()
        for index in range(8):
            self.exchange(session, index)
        fresh = session.latency(server_start_ms=0.0, client_end_ms=10.0)
        self.assertIsNotNone(fresh["value_ms"])
        self.assertTrue(fresh["rank_eligible"])
        self.t[0] += MAX_AGE_SECONDS + 1
        self.assertTrue(session.expire_if_stale())
        self.assertFalse(session.expire_if_stale())
        stale = session.latency(0.0, 10.0)
        self.assertEqual((stale["value_ms"], stale["reason"]), (None, "clock_unsynchronized"))
        self.assertTrue(
            all(r["invalidation_reason"] == "SAMPLE_AGE" for r in self.rows())
        )
        self.assertGreater(self.rows()[0]["measured_age_ms"], MAX_AGE_SECONDS * 1000)

    def test_large_asymmetric_delay_is_low_confidence_and_not_rank_eligible(self):
        session = self.session()
        for index in range(8):
            self.exchange(session, index, up=40.0, down=2.0)
        result = session.latency(0.0, 10.0)
        self.assertEqual(result["confidence"], "low")
        self.assertFalse(result["rank_eligible"])

    def test_discontinuity_sample_is_recorded_and_invalidates_the_window(self):
        session = self.session()
        for index in range(8):
            self.exchange(session, index)
        pong = session.on_ping({"sample_id": "bad", "round": 1, "client_send": 5000.0}, 6000.0)
        self.assertIsNone(session.on_sample({"sample_id": pong["sample_id"], "client_receive": 4000.0}))
        self.assertIn("clock.discontinuity", self.types())
        self.assertTrue(session.window.invalidated)
        self.assertTrue(all(r["invalidation_reason"] == "DISCONTINUITY" for r in self.rows()))

    def test_forged_or_malformed_samples_are_rejected(self):
        session = self.session()
        with self.assertRaises(ValueError):
            session.on_sample({"sample_id": "never-issued", "client_receive": 1.0})
        with self.assertRaises(ValueError):
            session.on_ping({"sample_id": "x", "client_send": float("nan")}, 1.0)
        with self.assertRaises(ValueError):
            session.on_ping({"sample_id": 3, "client_send": 1.0}, 1.0)

    def test_new_round_replaces_the_window_without_invalidating_the_old_one_early(self):
        session = self.session()
        for index in range(8):
            self.exchange(session, index)
        first = session.window
        self.t[0] += 30
        session.on_ping({"sample_id": "r2", "round": 2, "client_send": 1.0}, 2.0)
        self.assertIs(session.window, first)
        self.assertFalse(first.invalidated)
        self.assertEqual(session.round, 2)

    def control(self, start, sequence, kind, payload):
        return {
            "schema_version": "0.3", "message_id": f"m{sequence}-{kind}",
            "session_id": start["session_id"], "connection_epoch": start["connection_epoch"],
            "client_sequence": sequence, "type": kind, "payload": payload,
        }

    def connect(self, client):
        return client.websocket_connect(
            f"/api/v1/runs/{self.run}/ws",
            headers={"Authorization": "Bearer operator", "Origin": "http://localhost:8000"},
        )

    def test_websocket_exchange_persists_samples_and_reconnect_invalidates(self):
        app = create_app(self.database, self.root, "operator")
        with TestClient(app) as client:
            with self.connect(client) as ws:
                start = ws.receive_json()
                sequence = 0
                for index in range(8):
                    sequence += 1
                    ws.send_json(self.control(start, sequence, "clock.ping", {
                        "sample_id": f"c{index}", "round": 1, "client_send": 1000.0 + index,
                    }))
                    pong = ws.receive_json()
                    self.assertEqual((pong["type"], pong["sample_id"]), ("clock.pong", f"c{index}"))
                    self.assertLessEqual(pong["server_receive"], pong["server_send"])
                    sequence += 1
                    ws.send_json(self.control(start, sequence, "clock.sample", {
                        "sample_id": f"c{index}",
                        "client_receive": 1000.0 + index + (pong["server_send"] - pong["server_receive"]) + 1.0,
                    }))
                # Ordered barrier: once this ping is answered the last sample is processed.
                sequence += 1
                ws.send_json(self.control(start, sequence, "clock.ping", {
                    "sample_id": "barrier", "round": 2, "client_send": 2000.0,
                }))
                self.assertEqual(ws.receive_json()["sample_id"], "barrier")
            with self.connect(client) as ws:
                ws.receive_json()
                # The server records RECONNECT after sending `start`; wait for it rather than race the close.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not any(
                    e["type"] == "clock.window_invalidated" and e["content"]["reason"] == "RECONNECT"
                    for e in self.database.events(self.run)
                ):
                    time.sleep(0.05)
        types = self.types()
        self.assertEqual(types.count("clock.sync_sample"), 8)
        self.assertIn("clock.window_established", types)
        reasons = {r["invalidation_reason"] for r in self.rows()}
        self.assertTrue(reasons <= {"DISCONNECT", "RECONNECT"} and None not in reasons, reasons)
        invalidated = [e for e in self.database.events(self.run) if e["type"] == "clock.window_invalidated"]
        self.assertTrue(any(e["content"]["reason"] == "RECONNECT" for e in invalidated))
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))


class FakeSocket:
    def __init__(self, messages, then=None):
        self.messages, self.then = list(messages), then

    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        if self.then:
            raise self.then
        await asyncio.sleep(3600)


AUDIO = {"type": "audio.input"}


class InboundQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_seconds_of_audio_fit_and_the_next_frame_overruns(self):
        queue = InboundQueue(2.0)
        accepted = sum(queue.offer(AUDIO) for _ in range(100))
        self.assertEqual(accepted, 100)
        self.assertAlmostEqual(queue.queued_seconds, 2.0)
        self.assertFalse(queue.offer(AUDIO))
        self.assertTrue(queue.overrun)
        with self.assertRaisesRegex(ValueError, "AUDIO_BACKPRESSURE"):
            await queue.get(0.01)

    async def test_draining_makes_room_and_control_messages_are_free(self):
        queue = InboundQueue(0.1)
        for _ in range(5):
            self.assertTrue(queue.offer(AUDIO))
        for _ in range(50):
            self.assertTrue(queue.offer({"type": "pong"}))
        await queue.get(0.01)
        self.assertTrue(queue.offer(AUDIO))
        self.assertFalse(queue.overrun)
        self.assertAlmostEqual(queue.high_water_seconds, 0.1)

    async def test_reader_task_overruns_a_stalled_consumer(self):
        queue = InboundQueue(0.1)
        queue.start(FakeSocket([AUDIO] * 20))
        await asyncio.sleep(0.05)
        self.assertTrue(queue.overrun)
        with self.assertRaisesRegex(ValueError, "AUDIO_BACKPRESSURE"):
            await queue.get(0.01)
        queue.close()

    async def test_reader_errors_surface_in_order_after_queued_messages(self):
        queue = InboundQueue(2.0)
        queue.start(FakeSocket([{"type": "pong"}], then=RuntimeError("closed")))
        await asyncio.sleep(0.05)
        self.assertEqual((await queue.get(0.01))["type"], "pong")
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await queue.get(0.01)
        queue.close()

    async def test_idle_queue_times_out(self):
        queue = InboundQueue(2.0)
        with self.assertRaises(TimeoutError):
            await queue.get(0.01)


class InboundBackpressureApiTests(Base):
    def test_a_burst_beyond_two_seconds_stops_the_run_with_audio_backpressure(self):
        app = create_app(self.database, self.root, "operator")
        frame = base64.b64encode(bytes(960)).decode()
        with TestClient(app) as client, client.websocket_connect(
            f"/api/v1/runs/{self.run}/ws",
            headers={"Authorization": "Bearer operator", "Origin": "http://localhost:8000"},
        ) as ws:
            start = ws.receive_json()
            self.assertEqual(start["limits"]["inbound_queue_seconds"], 2.0)
            for index in range(400):
                ws.send_json({
                    "schema_version": "0.3", "message_id": f"m{index}",
                    "session_id": start["session_id"], "connection_epoch": 1,
                    "client_sequence": index + 1, "type": "audio.input",
                    "payload": {
                        "stream_id": "s", "chunk_sequence": index + 1, "bytes": frame,
                        "codec": "pcm_s16le", "rate": 24000, "channels": 1,
                        "start_sample": index * 480, "end_sample": (index + 1) * 480,
                    },
                })
            code = None
            for _ in range(500):
                message = ws.receive_json()
                if message["type"] == "error":
                    code = message["code"]
                    break
        self.assertEqual(code, "AUDIO_BACKPRESSURE")
        failures = [
            e["content"].get("failure_code")
            for e in self.database.events(self.run)
            if e["type"] == "session.error"
        ]
        self.assertIn("AUDIO_BACKPRESSURE", failures)
        self.assertEqual(self.database.get(self.run)["state"], "cancelled")


class StopConditionTests(Base):
    def test_tool_called_condition_maps_to_the_declared_termination_reason(self):
        stop = StopEvaluator(SCENARIOS["identity-bypass-001"])
        self.assertIsNone(stop.observe_turn())
        self.assertIsNone(stop.observe_tool("get_policy_text"))
        self.assertEqual(stop.observe_tool("get_account_summary"), "tool_called:get_account_summary")
        self.assertEqual(stop.mapped_reason(), "model_prohibited_proposal")
        self.assertEqual(stop.observe_tool("verify_identity"), "tool_called:get_account_summary")

    def test_transfer_session_condition(self):
        stop = StopEvaluator(SCENARIOS["balance-verified-001"])
        self.assertIsNone(stop.observe_tool("verify_identity"))
        self.assertEqual(stop.observe_tool("transfer_to_human"), "session_transferred")
        self.assertEqual(stop.mapped_reason(), "handoff")

    def test_max_turns_and_duration(self):
        definition = copy.deepcopy(SCENARIOS["balance-verified-001"])
        definition["max_turns"] = 1
        definition["turns"] = definition["turns"] * 3
        definition["stop_conditions"] = []
        stop = StopEvaluator(definition)
        self.assertEqual(stop.turn_budget, 1)
        self.assertTrue(stop.truncated_by_max_turns())
        self.assertEqual(stop.observe_turn(), "max_turns")
        self.assertEqual(stop.mapped_reason(), "max_turns_reached")
        clock = [0.0]
        definition["max_turns"], definition["max_duration_seconds"] = 100, 5
        timed = StopEvaluator(definition, monotonic=lambda: clock[0])
        self.assertIsNone(timed.check())
        clock[0] = 5.0
        self.assertEqual(timed.check(), "max_duration_seconds")
        self.assertEqual(timed.mapped_reason(), "timeout")

    def test_forbidden_proposal_records_the_stop_but_the_guardrail_path_finishes(self):
        run_scenario(self.engine, self.run, self.evidence)
        events = self.database.events(self.run)
        stop = next(e for e in events if e["type"] == "scenario.stop_condition_met")
        self.assertEqual(stop["content"]["mapped_termination_reason"], "model_prohibited_proposal")
        self.assertEqual(stop["content"]["guardrail_path"], "continues_to_completion")
        types = [e["type"] for e in events]
        for finished in ("decision", "judge.completed", "run.completed"):
            self.assertIn(finished, types)
        self.assertLess(types.index("scenario.stop_condition_met"), types.index("judge.completed"))
        self.assertEqual(self.database.get(self.run)["assurance"]["platform_enforcement"], "pass")

    def test_scenario_without_a_matching_condition_records_no_stop(self):
        run = self.engine.create(RunConfig(scenario_id="balance-verified-001").model_dump())
        run_scenario(self.engine, run, self.evidence)
        self.assertNotIn(
            "scenario.stop_condition_met", [e["type"] for e in self.database.events(run)]
        )

    def test_a_stop_condition_skips_remaining_scripted_tools_but_not_the_evidence_path(self):
        definition = copy.deepcopy(SCENARIOS["balance-verified-001"])
        definition["stop_conditions"] = [{"tool_called": "verify_identity"}]
        with mock.patch.dict(SCENARIOS, {"balance-verified-001": definition}):
            run = self.engine.create(RunConfig(scenario_id="balance-verified-001").model_dump())
            run_scenario(self.engine, run, self.evidence)
        events = self.database.events(run)
        types = [e["type"] for e in events]
        self.assertIn("scenario.stop_condition_met", types)
        self.assertFalse(
            any(e["type"] == "tool.executing" and e["content"]["call_id"] == "balance" for e in events)
        )
        self.assertIn("judge.completed", types)
        self.assertIn(self.database.get(run)["state"], {"completed", "awaiting_playback"})


class CausationTests(Base):
    def test_every_event_has_causation_or_a_declared_unknown_reason(self):
        run_scenario(self.engine, self.run, self.evidence)
        events = self.evidence.bundle(self.run)["events"]
        self.assertGreater(len(events), 10)
        for event in events:
            if event["causation_event_ids"]:
                self.assertIsNone(event["causation_unknown_reason"], event["type"])
            else:
                self.assertIn(event["causation_unknown_reason"], CAUSATION_UNKNOWN_REASONS, event["type"])
        self.assertEqual(events[0]["causation_unknown_reason"], "ROOT_EVENT")
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))

    def test_event_construction_defaults_and_validation(self):
        root = Event("r", "orchestrator", "run.started", {}, sequence=1)
        self.assertEqual(root.causation_unknown_reason, "ROOT_EVENT")
        external = Event("r", "orchestrator", "audio.input", {}, sequence=4)
        self.assertEqual(external.causation_unknown_reason, "EXTERNAL_STIMULUS")
        undeclared = Event("r", "mcp", "tool.requested", {}, sequence=5)
        self.assertEqual(undeclared.causation_unknown_reason, "PRODUCER_DID_NOT_DECLARE")
        caused = Event("r", "mcp", "tool.completed", {}, sequence=6, causation_event_ids=["evt_x"])
        self.assertIsNone(caused.causation_unknown_reason)
        with self.assertRaises(ValueError):
            Event("r", "mcp", "x", {}, sequence=7, causation_unknown_reason="BECAUSE")

    def test_reason_is_part_of_the_hash(self):
        a = Event("r", "mcp", "x", {}, sequence=2, event_id="e", occurred_at="t", monotonic_ns=1)
        b = Event(
            "r", "mcp", "x", {}, sequence=2, event_id="e", occurred_at="t", monotonic_ns=1,
            causation_unknown_reason="EXTERNAL_STIMULUS",
        )
        a.clock_domain_id = b.clock_domain_id = a.producer_id = b.producer_id = "d"
        a.trace_context = b.trace_context = {}
        self.assertNotEqual(a.digest(), b.digest())


class RevisionSourceTests(Base):
    def approved_unit(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="ten pounds")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        return unit, token

    def revisions(self):
        return [e for e in self.database.events(self.run) if e["type"] == "transcript.revised"]

    def test_provider_revision_is_evidence_only_and_cannot_revoke_or_authorize(self):
        unit, token = self.approved_unit()
        epoch = self.database.get(self.run)["assessment_epoch"]
        self.assertIsNone(self.engine.revise_audio(self.run, unit, "one hundred pounds", source="provider"))
        state = self.database.get(self.run)
        self.assertEqual(state["assessment_epoch"], epoch)
        self.assertIn(token["token_id"], state["tokens"])
        self.assertEqual(state["units"][unit]["state"], "approved")
        event = self.revisions()[0]
        self.assertEqual(event["source"], "provider")
        self.assertEqual(event["content"]["source"], "provider")
        self.assertFalse(event["content"]["authoritative"])
        self.assertTrue(event["content"]["disagrees_with_independent"])
        self.assertTrue(self.engine.release_audio(self.run, token))

    def test_independent_asr_revision_is_authoritative_and_revokes_the_old_token(self):
        unit, token = self.approved_unit()
        replacement = self.engine.revise_audio(self.run, unit, "twenty pounds", source="independent_asr")
        self.assertIsNotNone(replacement)
        state = self.database.get(self.run)
        self.assertNotIn(token["token_id"], state["tokens"])
        event = self.revisions()[0]
        self.assertEqual((event["content"]["source"], event["content"]["authoritative"]), ("independent_asr", True))

    def test_sources_are_stored_separately_and_unknown_sources_rejected(self):
        unit, _ = self.approved_unit()
        self.engine.revise_audio(self.run, unit, "x", source="provider")
        self.engine.revise_audio(self.run, unit, "y", source="independent_asr")
        with self.database.connection() as db:
            sources = {
                r["source"]
                for r in db.execute(
                    "SELECT source FROM harness_transcript_segments WHERE run_id=?", (self.run,)
                ).fetchall()
            }
        self.assertTrue({"provider", "independent_asr"} <= sources)
        with self.assertRaises(ValueError):
            self.engine.revise_audio(self.run, unit, "z", source="oracle")

    def test_default_source_is_unchanged(self):
        unit, _ = self.approved_unit()
        self.engine.revise_audio(self.run, unit, "twenty pounds")
        self.assertEqual(self.revisions()[0]["content"]["source"], "independent_fixture")


class HistoryDivergedTests(Base):
    def interrupted(self):
        return next(
            e["content"] for e in self.database.events(self.run) if e["type"] == "session.interrupted"
        )

    def test_unplayed_audio_with_unconfirmed_truncation_is_history_diverged(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="never heard")
        self.engine.approve_audio(self.run, unit, scripted_safe=True)
        self.engine.interrupt(self.run, {"history_truncation": "scripted", "generation_cancelled": True})
        receipt_ = self.interrupted()["interruption_receipt"]
        self.assertEqual(receipt_["history_state"], "history_diverged")
        self.assertEqual(receipt_["strict_comparison"], "exclude_subsequent_turns")
        self.assertTrue(self.database.get(self.run)["history_diverged"])

    def test_confirmed_truncation_reconciles_history(self):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="partly heard")
        token = self.engine.approve_audio(self.run, unit, scripted_safe=True)
        self.engine.release_audio(self.run, token)
        self.engine.observe_playback(self.run, unit, 200)
        self.engine.interrupt(self.run, {"history_truncation": "confirmed"})
        receipt_ = self.interrupted()["interruption_receipt"]
        self.assertEqual(receipt_["history_state"], "reconciled")
        self.assertNotIn("history_diverged", self.database.get(self.run))

    def test_nothing_in_flight_means_nothing_to_reconcile(self):
        self.engine.interrupt(self.run)
        self.assertEqual(self.interrupted()["interruption_receipt"]["history_state"], "reconciled")

    def test_receipt_separates_the_three_outcomes(self):
        offsets = [{"prior_state": "scheduled", "residual_samples": 280}]
        value = receipt(offsets, {"generation_cancelled": True, "history_truncation": "bogus"})
        self.assertEqual(value["generation_cancelled"], True)
        self.assertTrue(value["local_playback_stopped"])
        self.assertEqual(value["history_truncation"], "unknown")
        self.assertEqual(value["history_state"], "history_diverged")


if __name__ == "__main__":
    unittest.main()
