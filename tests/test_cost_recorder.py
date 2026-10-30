"""Provider usage -> cost lines: replayed real probe shapes, fixture tariffs only (never vendor prices)."""

import copy
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from voice_harness import cost
from voice_harness.adapters import AudioOut, Interrupted, TranscriptDelta, TurnEnd, TurnStart, Usage
from voice_harness.asr import AsrResult, ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.cost_recorder import CostMeteredJudge, UsageRecorder
from voice_harness.engine import Engine
from voice_harness.judges.backends import JudgeBackend, JudgeConfig
from voice_harness.live_assessment import AdapterTurnDriver, IndependentAssessor
from voice_harness.pricing import PricingTable, load_table
from voice_harness.storage import Database

ROOT = Path(__file__).resolve().parent.parent
PHASE0 = ROOT / "artifacts" / "phase0"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "pricing_fixture.json").read_text())
SHIPPED = load_table(ROOT / "src" / "voice_harness" / "data" / "pricing_table.json")
DATE = "2026-10-17"


def tariff(provider, service, meter, price, unit="token", rate="1000000", cached=None, scale=6):
    base = copy.deepcopy(FIXTURE["tariffs"][0])
    base.update(tariff_id=f"{provider}-{service}-{meter}", provider=provider, service=service, meter_id=meter,
                unit=unit, unit_price=price, rate_unit_quantity=rate, exclusivity_group=None,
                cached_unit_price=cached, cached_billing="discounted" if cached else "not_documented",
                amount_scale=scale, billing_model="per_token" if unit == "token" else "per_second")
    return base


def fixture_table():
    meters = {"audio_input_tokens": ("10", "2.50"), "text_input_tokens": ("1", None),
              "audio_output_tokens": ("20", None), "text_output_tokens": ("2", None)}
    rows = []
    for provider, service in (("fixture_oa", "rt"), ("fixture_nova", "rt")):
        rows += [tariff(provider, service, m, p, cached=c) for m, (p, c) in meters.items()]
    rows.append(tariff("fixture_asr", "transcription", "asr_seconds", "0.06", unit="second", rate="1", scale=4))
    rows.append(tariff("fixture_judge", "judge", "judge_input_tokens", "1"))
    rows.append(tariff("fixture_judge", "judge", "judge_output_tokens", "4"))
    return PricingTable({**FIXTURE, "tariffs": rows})


def openai_usage_events():
    """Usage events exactly as the OpenAI adapter emits them, from the recorded probe response.done frames."""
    out = []
    for path in sorted(PHASE0.glob("openai-probe-*.jsonl")):
        for line in path.read_text().splitlines():
            record = json.loads(line)
            if record.get("type") == "response.done":
                resp = record["detail"]["response"]
                usage = resp.get("usage")
                if usage:
                    out.append(Usage(session_id="s1", turn_id=resp["id"], input_tokens=usage["input_tokens"],
                                     output_tokens=usage["output_tokens"],
                                     raw={**usage, "response_status": resp.get("status")}))
    return out


def nova_usage_events():
    """Usage events as the Nova adapter emits them from recorded usageEvent frames (cumulative per session)."""
    out = []
    path = PHASE0 / "nova-probe-2026-10-17T12-15-23-874Z.jsonl"
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") == "usageEvent":
            body = record["detail"]
            out.append(Usage(session_id="s1", turn_id=body.get("completionId"), input_tokens=body["totalInputTokens"],
                             output_tokens=body["totalOutputTokens"],
                             raw={"totalTokens": body["totalTokens"], "details": body.get("details", {}),
                                  "cumulative": True, "provider_session_id": body.get("sessionId")}))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")))
        self.engine = Engine(self.database)
        self.run = self.engine.create(RunConfig().model_dump())
        self.table = fixture_table()

    def recorder(self, provider="fixture_oa", **kw):
        return UsageRecorder(self.database, self.run, provider=provider, service="rt", clock=lambda: DATE, **kw)

    def section(self, table=None):
        return cost.build_section(self.run, self.database.events(self.run), (), table or self.table)

    def active(self, section, **match):
        return [x for x in section["lines"] if x["superseded_by"] is None
                and all(x[k] == v for k, v in match.items())]


class OpenAIShapes(Base):
    def test_probe_logs_have_real_usage_shapes(self):
        events = openai_usage_events()
        self.assertGreaterEqual(len(events), 3)
        self.assertIn("cancelled", {e.raw["response_status"] for e in events})

    def test_real_responses_give_exact_fixture_totals_and_cached_subset(self):
        recorder = self.recorder()
        recorder.declare("voice")
        event = openai_usage_events()[0]  # in 359 text / 0 audio, out 45 text / 48 audio (real response.done)
        recorder.observe(event)
        recorder.observe(TurnEnd(turn_id=event.turn_id))
        section = self.section()
        q = {x["meter_id"]: x["quantity"] for x in self.active(section)}
        self.assertEqual(q, {"audio_input_tokens": "0", "text_input_tokens": "359",
                             "audio_output_tokens": "48", "text_output_tokens": "45"})
        # (359*1 + 0*10 + 48*20 + 45*2) / 1e6
        summary = section["summaries"][0]
        self.assertEqual(summary["known_subtotal"], "0.001409")
        self.assertEqual(summary["completeness"], "complete")
        self.assertEqual(summary["total_cost"], "0.001409")

    def test_cached_tokens_priced_at_discount(self):
        recorder = self.recorder()
        recorder.declare("voice")
        recorder.observe(Usage(turn_id="resp_c", input_tokens=1000000, output_tokens=0, raw={
            "input_token_details": {"audio_tokens": 1000000, "text_tokens": 0, "cached_tokens": 400000,
                                    "cached_tokens_details": {"audio_tokens": 400000, "text_tokens": 0}},
            "output_token_details": {"audio_tokens": 0, "text_tokens": 0}, "response_status": "completed"}))
        line = self.active(self.section(), meter_id="audio_input_tokens")[0]
        self.assertEqual(line["amount"], "7.000000")  # 600000 * 10/1e6 + 400000 * 2.5/1e6

    def test_duplicate_delivery_and_replay_are_idempotent(self):
        recorder = self.recorder()
        event = openai_usage_events()[0]
        first = recorder.observe(event)
        self.assertTrue(first)
        self.assertEqual(recorder.observe(event), [])
        replay = self.recorder()  # reconnect: a fresh recorder seeds from the ledger
        self.assertEqual(replay.observe(event), [])
        section = self.section()
        self.assertEqual(section["anomalies"], [])
        self.assertEqual(len(section["lines"]), 4)

    def test_cancelled_generation_records_usage_with_billed_status_unknown(self):
        recorder = self.recorder()
        cancelled = next(e for e in openai_usage_events() if e.raw["response_status"] == "cancelled")
        recorder.observe(Interrupted(turn_id=cancelled.turn_id))
        recorder.observe(cancelled)
        recorder.observe(TurnEnd(turn_id=cancelled.turn_id))
        lines = self.section()["lines"]
        self.assertTrue(lines and all(x["generation_state"] == "cancelled" for x in lines))
        self.assertTrue(all(any(a.startswith("BILLED_STATUS_UNKNOWN") for a in x["assumptions"]) for x in lines))
        self.assertTrue(all(x["quantity"] is not None for x in lines))  # usage is as reported, not guessed

    def test_turn_without_usage_is_unknown_with_reason_never_zero(self):
        recorder = self.recorder()
        recorder.declare("voice")
        recorder.observe(TurnStart(turn_id="resp_x"))
        recorder.observe(TurnEnd(turn_id="resp_x"))
        recorder.observe(TurnEnd(turn_id="resp_x"))  # duplicate delivery
        section = self.section()
        self.assertEqual({x["unknown_reason"] for x in section["lines"]}, {"PROVIDER_RETURNED_NO_USAGE"})
        self.assertTrue(all(x["quantity"] is None and x["amount"] is None for x in section["lines"]))
        self.assertEqual(section["summaries"], [])
        self.assertEqual(section["completeness"], "unknown")

    def test_interrupted_turn_without_usage_is_unknown_cancelled(self):
        recorder = self.recorder()
        recorder.observe(Interrupted(turn_id="resp_i"))
        recorder.observe(TurnEnd(turn_id="resp_i"))
        lines = self.section()["lines"]
        self.assertEqual({x["generation_state"] for x in lines}, {"cancelled"})
        self.assertIn("voice_cancelled_generation", self.section()["unknown_components"])


class NovaShapes(Base):
    def test_cumulative_totals_supersede_and_never_add(self):
        events = nova_usage_events()
        self.assertGreater(len(events), 5)
        recorder = self.recorder("fixture_nova", usage_mode="cumulative_session")
        recorder.declare("voice")
        for event in events:
            recorder.observe(event)
        for event in events:  # replay after reconnect: nothing new
            self.assertEqual(recorder.observe(event), [])
        # the probe log spans more than one provider session: the last total of each counts once, sessions add
        finals = {e.raw["provider_session_id"]: e for e in events}
        self.assertGreaterEqual(len(finals), 2)
        section = self.section()
        active = self.active(section)
        self.assertEqual(len(active), 4 * len(finals))
        expected = 0
        for session, final in finals.items():
            details = final.raw["details"]["total"]
            mine = {x["meter_id"]: x["quantity"] for x in active if x["billing_scope_id"] == session}
            self.assertEqual(mine, {"audio_input_tokens": str(details["input"]["speechTokens"]),
                                    "text_input_tokens": str(details["input"]["textTokens"]),
                                    "audio_output_tokens": str(details["output"]["speechTokens"]),
                                    "text_output_tokens": str(details["output"]["textTokens"])})
            expected += (details["input"]["speechTokens"] * 10 + details["input"]["textTokens"] * 1
                         + details["output"]["speechTokens"] * 20 + details["output"]["textTokens"] * 2)
        self.assertEqual(section["summaries"][0]["known_subtotal"], format(Decimal(expected) / 1000000, ".6f"))
        self.assertTrue(all(x["report_kind"] == "cumulative" and x["scope_type"] == "session" for x in active))
        self.assertEqual(section["anomalies"], [])

    def test_session_without_usage_is_unknown_not_zero(self):
        recorder = self.recorder("fixture_nova", usage_mode="cumulative_session")
        recorder.declare("voice")
        recorder.observe(TurnStart(session_id="n1"))
        recorder.observe(TurnEnd(session_id="n1"))
        self.assertEqual(self.section()["lines"], [])
        recorder.close()
        lines = self.section()["lines"]
        self.assertEqual({x["unknown_reason"] for x in lines}, {"PROVIDER_RETURNED_NO_USAGE"})
        self.assertEqual({x["scope_type"] for x in lines}, {"session"})


class RealTariffsStayUnknown(Base):
    def test_shipped_table_exports_unknown_with_reasons(self):
        self.assertTrue(all(t.unit_price is None for t in SHIPPED.tariffs))
        for provider, events in (("openai", openai_usage_events()), ("aws_nova_sonic", nova_usage_events())):
            self.run = self.engine.create(RunConfig().model_dump())
            recorder = UsageRecorder(self.database, self.run, provider=provider, clock=lambda: DATE)
            recorder.declare("voice")
            for event in events:
                recorder.observe(event)
            section = self.section(SHIPPED)
            self.assertTrue(section["lines"])
            active = self.active(section)
            self.assertTrue(all(x["amount"] is None and x["unknown_reason"] for x in active), provider)
            self.assertEqual(section["completeness"], "unknown")
            self.assertEqual(section["summaries"], [])  # no known_subtotal at all
            self.assertTrue(any(x["quantity"] is not None for x in active))  # quantities are still reported


class Components(Base):
    def test_ensure_expected_components_unions_and_is_idempotent(self):
        self.assertTrue(cost.ensure_expected_components(self.database, self.run, ["voice"]))
        self.assertIsNone(cost.ensure_expected_components(self.database, self.run, ["voice"]))
        cost.ensure_expected_components(self.database, self.run, ["asr", "voice"])
        self.assertEqual(self.section()["expected_components"], ["asr", "voice"])

    def test_missing_expected_component_blocks_complete(self):
        recorder = self.recorder()
        recorder.declare("voice", "asr")
        recorder.observe(openai_usage_events()[0])
        section = self.section()
        self.assertEqual(section["completeness"], "partial")
        self.assertEqual(section["uncovered_components"], ["asr"])
        self.assertIsNone(section["summaries"][0]["total_cost"])


class AsrAndJudges(Base):
    def asr_recorder(self):
        return UsageRecorder(self.database, self.run, provider="fixture_oa", clock=lambda: DATE)

    def test_asr_seconds_and_tokens_and_missing(self):
        recorder = self.asr_recorder()
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", True, {"type": "duration", "seconds": 30}), "asr:u1")
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", True, None), "asr:u2")
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", False, None), "asr:u3")
        recorder.record_asr(None, "asr:u4", failed_reason="ASR_REQUEST_FAILED_USAGE_UNKNOWN")
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", True, {
            "type": "tokens", "total_tokens": 6, "input_tokens": 2, "output_tokens": 4,
            "input_token_details": {"text_tokens": 0, "audio_tokens": 2}}), "asr:u5")
        lines = {x["billing_scope_id"]: x for x in self.section()["lines"] if x["meter_id"] == "asr_seconds"}
        self.assertEqual((lines["asr:u1"]["quantity"], lines["asr:u1"]["amount"]), ("30", "1.8000"))
        self.assertEqual(lines["asr:u2"]["unknown_reason"], "PROVIDER_RETURNED_NO_USAGE")
        self.assertEqual(lines["asr:u3"]["unknown_reason"], "OFFLINE_FIXTURE_NO_USAGE")
        self.assertEqual(lines["asr:u4"]["unknown_reason"], "ASR_REQUEST_FAILED_USAGE_UNKNOWN")
        self.assertTrue(all(lines[k]["amount"] is None for k in ("asr:u2", "asr:u3", "asr:u4")))
        token = [x for x in self.section()["lines"] if x["billing_scope_id"] == "asr:u5"]
        self.assertEqual({x["meter_id"]: x["quantity"] for x in token if x["quantity"]}, {"audio_input_tokens": "2", "text_input_tokens": "0", "output_tokens_unclassified": "4"})

    def test_asr_without_provider_usage_is_priced_from_harness_measured_seconds_and_labelled(self):
        recorder = self.asr_recorder()
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", True, None, audio_seconds=12.5), "asr:m1")
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", True, {"type": "duration", "seconds": 30}, audio_seconds=12.5), "asr:m2")
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", False, None, audio_seconds=12.5), "asr:m3")
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", True, None, audio_seconds=0.0), "asr:m4")
        recorder.record_asr(AsrResult("t", "fixture_asr", "m", True, None, audio_seconds=12.5), "asr:m5", failed_reason="ASR_REQUEST_FAILED_USAGE_UNKNOWN")
        lines = {x["billing_scope_id"]: x for x in self.section()["lines"] if x["meter_id"] == "asr_seconds"}
        measured = lines["asr:m1"]
        self.assertEqual((measured["quantity"], measured["amount"]), ("12.5", "0.7500"))
        self.assertEqual((measured["provenance"], measured["usage_source"]), ("estimated", "estimated"))
        self.assertEqual(measured["estimation_method"], "harness_measured_audio_seconds")
        self.assertIn("HARNESS_MEASURED_NOT_PROVIDER_REPORTED", measured["assumptions"][0])
        # Provider-reported usage is preferred over the harness's own measurement.
        self.assertEqual((lines["asr:m2"]["quantity"], lines["asr:m2"]["provenance"]), ("30", "provider_reported_usage"))
        # Offline fixtures, zero-length audio and failed requests stay unknown, never priced from a guess.
        self.assertEqual(lines["asr:m3"]["unknown_reason"], "OFFLINE_FIXTURE_NO_USAGE")
        self.assertEqual(lines["asr:m4"]["unknown_reason"], "PROVIDER_RETURNED_NO_USAGE")
        self.assertEqual(lines["asr:m5"]["unknown_reason"], "ASR_REQUEST_FAILED_USAGE_UNKNOWN")

    def test_judge_wrapper_records_usage_unknown_and_failure(self):
        class Fake(JudgeBackend):
            def __init__(self):
                self.config = JudgeConfig("fj", "fixture_judge", "fixture_judge", "m1", "fam")
                self.mode = "usage"

            def complete(self, request):
                if self.mode == "usage":
                    self.last_usage = {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": None}
                elif self.mode == "fail":
                    raise RuntimeError("boom")
                return "{}"

        class Req:
            kind, user_payload = "k", "{}"

        backend = Fake()
        metered = CostMeteredJudge(backend, self.database, self.run, clock=lambda: DATE)
        self.assertEqual(metered.config.judge_id, "fj")
        metered.complete(Req)
        backend.mode = "none"
        metered.complete(Req)
        backend.mode = "fail"
        with self.assertRaises(RuntimeError):
            metered.complete(Req)
        section = self.section()
        by_scope = {}
        for x in section["lines"]:
            by_scope.setdefault(x["billing_scope_id"].rsplit(":", 1)[1], []).append(x)
        first, second, third = (by_scope[k] for k in ("1", "2", "3"))
        self.assertEqual(sorted(x["amount"] for x in first), ["0.001000", "0.002000"])
        self.assertEqual({x["unknown_reason"] for x in second}, {"PROVIDER_RETURNED_NO_USAGE"})
        self.assertEqual({x["unknown_reason"] for x in third}, {"JUDGE_CALL_FAILED_USAGE_UNKNOWN"})
        self.assertIn("judge", section["expected_components"])
        self.assertEqual(section["completeness"], "partial")


class DriverEndToEnd(Base):
    def make(self, backend):
        recorder = UsageRecorder(self.database, self.run, provider="fixture_oa", service="rt", clock=lambda: DATE)
        assessor = IndependentAssessor(self.engine, backend)
        driver = AdapterTurnDriver(self.engine, self.run, assessor, expected_claims={}, cost_recorder=recorder)
        return driver

    def test_driven_session_produces_voice_and_asr_cost_lines(self):
        class UsageAsr(ScriptedAsr):
            def transcribe(self, pcm, rate):
                base = super().transcribe(pcm, rate)
                return AsrResult(base.text, "fixture_asr", "m", True, {"type": "duration", "seconds": 10})

        driver = self.make(UsageAsr(queue=["hello"]))
        usage = openai_usage_events()[0]
        driver.feed(TranscriptDelta(role="assistant", text="hello", ts_ms=1))
        driver.feed(AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id=usage.turn_id, ts_ms=2))
        driver.feed(usage)
        driver.feed(TurnEnd(turn_id=usage.turn_id, ts_ms=3))
        section = self.section()
        self.assertEqual(section["expected_components"], ["asr", "voice"])
        self.assertEqual(section["completeness"], "complete")
        self.assertEqual(section["summaries"][0]["known_subtotal"], "0.601409")  # voice 0.001409 + asr 10 s * 0.06

    def test_driver_without_asr_backend_declares_only_voice(self):
        driver = self.make(None)
        usage = openai_usage_events()[0]
        driver.feed(usage)
        driver.feed(TurnEnd(turn_id=usage.turn_id))
        self.assertEqual(self.section()["expected_components"], ["voice"])

    def test_discarded_interrupted_audio_still_records_provider_usage(self):
        driver = self.make(None)
        cancelled = next(e for e in openai_usage_events() if e.raw["response_status"] == "cancelled")
        driver.feed(AudioOut(pcm=bytes(960), sample_rate=24000, sample_count=480, turn_id=cancelled.turn_id, ts_ms=1))
        driver.feed(Interrupted(turn_id=cancelled.turn_id))
        driver.feed(cancelled)
        driver.feed(TurnEnd(turn_id=cancelled.turn_id))
        self.assertEqual({x["generation_state"] for x in self.section()["lines"]}, {"cancelled"})


if __name__ == "__main__":
    unittest.main()


class LiveUsageTests(unittest.TestCase):
    def test_voice_duration_is_cumulative_and_backend_tokens_are_separate(self):
        from unittest.mock import patch
        recorder = UsageRecorder(None, 'run', provider='openai_live', model='gpt-live-1')
        with patch.object(recorder, '_append', side_effect=lambda reports: reports):
            voice = recorder.observe(Usage(session_id='s', raw={'voice_usage': {'seconds': 12.5}, 'cumulative': True}))
            self.assertEqual(len(voice), 1)
            self.assertEqual((voice[0]['unit'], voice[0]['quantity'], voice[0]['report_kind']), ('second', '12.5', 'cumulative'))
            backend = recorder.observe(Usage(session_id='s', raw={'backend_model_id': 'gpt-6-luna',
                'response_id': 'resp1', 'backend_usage': {'input_tokens': 20, 'output_tokens': 5}}))
            self.assertTrue(all(r['model'] == 'gpt-6-luna' and r['service'] == 'live_responses' for r in backend))
            self.assertEqual([r['quantity'] for r in backend], ['20', '5'])
            self.assertEqual(recorder.observe(TurnEnd(session_id='s')), [])


class ElevenLabsConnectedTimeTests(unittest.TestCase):
    def test_connected_seconds_are_an_estimate_priced_per_minute_from_the_vendor_table(self):
        from unittest.mock import patch
        recorder = UsageRecorder(None, 'run', provider='elevenlabs_agents', model='elevenlabs-agent', clock=lambda: '2026-10-29')
        with patch.object(recorder, '_append', side_effect=lambda reports: reports):
            [report] = recorder.observe(Usage(session_id='s', raw={'connected_seconds': 30.0, 'agent_llm': None}))
        self.assertEqual((report['meter_id'], report['unit'], report['quantity'], report['provenance']),
                         ('connected_audio_seconds', 'second', '30.0', 'estimated'))
        self.assertIn('LLM', report['assumptions'][0])
        table = load_table(ROOT / "artifacts" / "pricing" / "vendor-pricing-2026-10-29.json")
        _, amount, _, reason = cost.price(report, table)
        self.assertIsNone(reason)
        self.assertEqual(Decimal(str(amount)), Decimal("0.04"))

    def test_known_agent_llm_adds_a_separately_priced_estimate_line(self):
        from unittest.mock import patch
        recorder = UsageRecorder(None, 'run', provider='elevenlabs_agents', model='elevenlabs-agent', clock=lambda: '2026-10-29')
        with patch.object(recorder, '_append', side_effect=lambda reports: reports):
            connection, llm = recorder.observe(Usage(session_id='s', raw={'connected_seconds': 60.0, 'agent_llm': 'gemini-3.5-flash'}))
        table = load_table(ROOT / "artifacts" / "pricing" / "vendor-pricing-2026-10-29.json")
        self.assertEqual((llm['service'], llm['model'], llm['meter_id']), ('elevenlabs_agent_llm', 'gemini-3.5-flash', 'agent_llm_estimated_seconds'))
        self.assertEqual(Decimal(str(cost.price(connection, table)[1])), Decimal("0.08"))
        self.assertEqual(Decimal(str(cost.price(llm, table)[1])), Decimal("0.0071"))


class OpenAILivePricingTests(unittest.TestCase):
    def test_voice_seconds_and_backend_tokens_price_from_the_2026_10_29_table(self):
        from unittest.mock import patch
        recorder = UsageRecorder(None, 'run', provider='openai_live', model='gpt-live-1', clock=lambda: '2026-10-29')
        with patch.object(recorder, '_append', side_effect=lambda reports: reports):
            [voice] = recorder.observe(Usage(session_id='s', raw={'voice_usage': {'seconds': 90}, 'cumulative': True}))
            backend = recorder.observe(Usage(session_id='s', raw={'backend_model_id': 'gpt-6-luna', 'response_id': 'r1',
                                                                   'backend_usage': {'input_tokens': 1000000, 'output_tokens': 1000000}}))
        table = load_table(ROOT / "artifacts" / "pricing" / "vendor-pricing-2026-10-29.json")
        self.assertEqual(Decimal(str(cost.price(voice, table)[1])), Decimal("0.075"))
        self.assertEqual([Decimal(str(cost.price(r, table)[1])) for r in backend], [Decimal("0.10"), Decimal("0.50")])


class XaiTextInputTests(unittest.TestCase):
    def test_counted_text_inputs_are_an_assumed_unit_estimate_priced_per_item(self):
        from unittest.mock import patch
        recorder = UsageRecorder(None, 'run', provider='xai_grok_voice', model='grok-voice-3.0', clock=lambda: '2026-10-29')
        with patch.object(recorder, '_append', side_effect=lambda reports: reports):
            [report] = recorder.observe(Usage(session_id='s', raw={'text_inputs': 3}))
        self.assertEqual((report['meter_id'], report['unit'], report['quantity'], report['provenance']),
                         ('text_inputs', 'request', '3', 'estimated'))
        self.assertIn('UNIT_ASSUMED', report['assumptions'][0])
        table = load_table(ROOT / "artifacts" / "pricing" / "vendor-pricing-2026-10-29.json")
        self.assertEqual(Decimal(str(cost.price(report, table)[1])), Decimal("0.012"))


class ServerWiresJudgeMetering(unittest.TestCase):
    def test_the_auto_judge_path_wraps_every_backend_in_the_cost_meter(self):
        source = (ROOT / "src" / "voice_harness" / "api.py").read_text()
        self.assertIn("CostMeteredJudge(backend, database, run_id)", source)
        self.assertLess(source.index("live_providers.auto_judges()"), source.index("CostMeteredJudge(backend, database, run_id)"))
        self.assertLess(source.index("CostMeteredJudge(backend, database, run_id)"), source.index("judge_service.assess_run("))
