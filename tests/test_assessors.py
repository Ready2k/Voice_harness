"""Assessor identity on bake-off evidence: judge and ASR models, disclosures, calibration, spot-check sampling."""

import unittest
from pathlib import Path

import httpx
from test_judges import TESTED, Base, cfg, reference_judge, reference_results
from test_phase3_live_assessment import EXPECTED, Fixture

from voice_harness import asr
from voice_harness.asr import (
    MODEL_ENV,
    OPENAI_TRANSCRIBE_MODEL,
    SECOND_MODEL_ENV,
    AsrUnavailable,
    DualAsr,
    OpenAITranscriptionAsr,
    agree,
    asr_vendor,
    select_backend,
)
from voice_harness.judges import calibration, service
from voice_harness.judges.assessors import (
    ASR_SAME_VENDOR,
    assessor_identity,
    spot_check_sample,
    summarise_assessors,
)
from voice_harness.judges.backends import (
    JUDGE_TEMPERATURE,
    BedrockConverseBackend,
    OpenAIBackend,
    ScriptedBackend,
    make_request,
)
from voice_harness.judges.oracle import build_oracle
from voice_harness.judges.rubric import EvalContext, eval_fact, required_judge_count
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.pricing import load_table

LIVE = {"VOICE_HARNESS_LIVE_ASR": "openai", "OPENAI_API_KEY": "sk-test-not-real"}


def asr_event(backend="openai_audio_transcription", model="gpt-transcribe", live=True, reason_codes=()):
    return {"type": "assessment.independent_asr",
            "content": {"backend": backend, "model": model, "live": live, "reason_codes": list(reason_codes)}}


class AsrModel(unittest.TestCase):
    def test_default_model_is_unchanged_and_env_overrides_it(self):
        self.assertEqual(OPENAI_TRANSCRIBE_MODEL, "gpt-transcribe")
        self.assertEqual(OpenAITranscriptionAsr(LIVE).model, OPENAI_TRANSCRIBE_MODEL)
        self.assertEqual(OpenAITranscriptionAsr({**LIVE, MODEL_ENV: "gpt-4o-transcribe"}).model, "gpt-4o-transcribe")
        self.assertEqual(select_backend({**LIVE, MODEL_ENV: "gpt-4o-transcribe"}).model, "gpt-4o-transcribe")
        self.assertEqual(OpenAITranscriptionAsr({**LIVE, MODEL_ENV: "x"}, model="explicit").model, "explicit")

    def test_malformed_model_name_is_unavailable(self):
        backend = OpenAITranscriptionAsr({**LIVE, MODEL_ENV: "bad model; drop"})
        self.assertEqual(backend.available(), (False, "ASR_MODEL_INVALID"))
        self.assertIsNone(select_backend({**LIVE, MODEL_ENV: "../x"}))

    def test_vendor_from_backend_name(self):
        self.assertEqual(asr_vendor("openai_audio_transcription"), "openai")
        self.assertEqual(asr_vendor("scripted_offline"), "scripted")
        self.assertEqual(asr_vendor("something_new"), "unknown")
        self.assertIn("openai_audio_transcription", asr.ASR_VENDORS)


class RequestFallback(unittest.TestCase):
    def backend(self, replies, seen):
        def handler(request):
            seen.append(request.content)
            return replies.pop(0)
        return OpenAITranscriptionAsr(LIVE, httpx.Client(transport=httpx.MockTransport(handler)))

    def test_default_request_sends_temperature_and_json_and_measures_the_audio(self):
        seen = []
        result = self.backend([httpx.Response(200, json={"text": "hi"})], seen).transcribe(bytes(48000), 24000)
        self.assertIn(b'name="temperature"', seen[0])
        self.assertIn(b'name="response_format"', seen[0])
        self.assertEqual((result.dropped_params, result.audio_seconds), ((), 1.0))

    def test_a_rejected_optional_field_is_dropped_and_the_call_retried(self):
        seen = []
        replies = [httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'temperature'"}}),
                   httpx.Response(400, json={"error": {"message": "invalid response_format"}}),
                   httpx.Response(200, json={"text": "hi"})]
        result = self.backend(replies, seen).transcribe(bytes(960), 24000)
        self.assertEqual(len(seen), 3)
        self.assertNotIn(b'name="temperature"', seen[1])
        self.assertNotIn(b'name="response_format"', seen[2])
        self.assertEqual(result.dropped_params, ("temperature", "response_format"))

    def test_other_bad_requests_are_unavailable_without_retry(self):
        seen = []
        with self.assertRaises(AsrUnavailable) as raised:
            self.backend([httpx.Response(400, json={"error": {"message": "unknown model"}})], seen).transcribe(bytes(960), 24000)
        self.assertEqual((str(raised.exception), len(seen)), ("ASR_HTTP_400", 1))


class VendorTariffs(unittest.TestCase):
    TABLE = Path(__file__).parent.parent / "artifacts" / "pricing" / "vendor-pricing-2026-10-24.json"

    def find(self, provider, service, model, meter):
        return load_table(self.TABLE).find(provider=provider, service=service, model=model, region=None,
                                            meter_id=meter, usage_date="2026-10-24")

    def test_transcription_per_minute_estimates_and_gpt_6_luna(self):
        for model, price in (("gpt-transcribe", "0.0045"), ("gpt-4o-transcribe", "0.006"),
                             ("gpt-4o-mini-transcribe", "0.003"), ("gpt-live-transcribe", "0.017")):
            row = self.find("openai_audio_transcription", "transcription", model, "asr_seconds")
            self.assertEqual((row.unit_price, row.rate_unit_quantity, row.billing_model), (price, "60", "per_minute"), model)
        self.assertEqual(self.find("openai", "judge", "gpt-6-luna", "judge_input_tokens").unit_price, "0.10")
        self.assertEqual(self.find("openai", "judge", "gpt-6-luna", "judge_input_tokens").cached_unit_price, "0.01")
        self.assertEqual(self.find("openai", "judge", "gpt-6-luna", "judge_output_tokens").unit_price, "0.50")

    def test_earlier_prices_are_unchanged_and_bedrock_judges_are_priced_from_anthropic(self):
        new, old = load_table(self.TABLE), load_table(self.TABLE.with_name("vendor-pricing-2026-10-19.json"))
        economics = lambda t: {k: v for k, v in t.model_dump().items() if k != "evidence_ids"}  # noqa: E731 - re-verification only adds evidence
        old_rows = {t.tariff_id: economics(t) for t in old.tariffs}
        self.assertEqual({t.tariff_id: economics(t) for t in new.tariffs if t.tariff_id in old_rows}, old_rows)
        for model, inp, out in (("us.anthropic.claude-sonnet-5-5", "2.20", "11.00"), ("global.anthropic.claude-sonnet-5-5", "2.00", "10.00"),
                                ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "1.10", "5.50")):
            self.assertEqual(self.find("aws_bedrock", "judge", model, "judge_input_tokens").unit_price, inp, model)
            self.assertEqual(self.find("aws_bedrock", "judge", model, "judge_output_tokens").unit_price, out, model)
        self.assertIsNone(self.find("aws_bedrock", "judge", "us.anthropic.claude-opus-9", "judge_input_tokens"))  # unlisted stays unknown
        self.assertIsNone(self.find("openai_audio_transcription", "transcription", "gpt-transcribe", "audio_input_tokens"))


class AssessorIdentity(Base):
    def test_no_judge_and_no_asr_is_empty_not_error(self):
        run = self.run_of("balance-verified-001")
        service.assess_run(self.database, run, tested=TESTED)
        info = assessor_identity(self.database.events(run), TESTED, self.database)
        self.assertEqual((info["judges"], info["asr"], info["asr_disclosures"]), ([], [], []))
        self.assertEqual(info["independent_judge_count"], 0)

    def test_judge_identity_disclosures_and_missing_calibration(self):
        run = self.run_of("balance-verified-001")
        same_provider = ScriptedBackend(lambda o: reference_results(o), cfg("oa", "openai", "gpt-4.1-mini", "gpt"))
        service.assess_run(self.database, run, tested=TESTED, backends=[same_provider, reference_judge()])
        info = assessor_identity(self.database.events(run), TESTED, self.database)
        by = {j["judge_id"]: j for j in info["judges"]}
        self.assertEqual(by["oa"]["model_id"], "gpt-4.1-mini")
        self.assertIn("SAME_PROVIDER_AS_TESTED_MODEL", by["oa"]["disclosures"])
        self.assertEqual(by["ref"]["disclosures"], [])
        self.assertEqual(by["ref"]["calibration"]["status"], "no_calibration_report")
        self.assertEqual(info["independent_judge_count"], 2)

    def test_calibration_summary_reports_holdout_figures_for_the_exact_judge(self):
        backend = reference_judge()
        policy = calibration.default_policy(calibration.fingerprint(backend.config))
        report = calibration.evaluate_holdout(backend, policy)
        calibration.save_policy(self.database, policy)
        calibration.save_report(self.database, report)
        run = self.run_of("balance-verified-001")
        service.assess_run(self.database, run, tested=TESTED, backends=[backend])
        summary = assessor_identity(self.database.events(run), TESTED, self.database)["judges"][0]["calibration"]
        self.assertEqual(summary["status"], "report_bound")
        self.assertEqual(summary["report_id"], report["report_id"])
        self.assertIn("FACT-01", summary["criteria"])
        self.assertEqual(set(summary["criteria"]["FACT-01"]), {
            "precision", "recall", "abstention_rate", "error_rate", "reviewer_agreement", "n"})
        # without a database the lookup is skipped rather than invented
        bare = assessor_identity(self.database.events(run), TESTED)["judges"][0]
        self.assertIsNone(bare["calibration"])

    def test_asr_models_and_same_vendor_disclosure(self):
        events = [asr_event(), asr_event(), asr_event(model="gpt-4o-transcribe")]
        info = assessor_identity(events, {"provider": "openai"})
        self.assertEqual([(a["vendor"], a["model"], a["replies"]) for a in info["asr"]],
                         [("openai", "gpt-4o-transcribe", 1), ("openai", "gpt-transcribe", 2)])
        self.assertEqual(info["asr_disclosures"], [ASR_SAME_VENDOR])
        self.assertEqual(assessor_identity(events, {"provider": "xai"})["asr_disclosures"], [])
        self.assertEqual(assessor_identity(events)["asr_disclosures"], [])
        scripted = assessor_identity([asr_event("scripted_offline", "scripted-v1", False)], {"provider": "openai"})
        self.assertEqual(scripted["asr_disclosures"], [])
        self.assertFalse(scripted["asr"][0]["live"])


class Rollup(unittest.TestCase):
    def test_summary_groups_by_provider_and_collects_disclosures(self):
        judge = {"judge_id": "j", "provider": "aws_bedrock", "model_id": "m", "family": "claude", "disclosures": [],
                 "calibration": None}
        obs = [
            {"provider": "a", "assessors": {"judges": [judge], "asr": [{"vendor": "openai", "model": "x", "live": True}],
                                            "asr_disclosures": [ASR_SAME_VENDOR], "judge_coverage": "complete"}},
            {"provider": "a", "assessors": {"judges": [], "asr": [{"vendor": "openai", "model": "x", "live": True}],
                                            "asr_disclosures": [ASR_SAME_VENDOR], "judge_coverage": None}},
            {"provider": "b"},  # older observation without the field
        ]
        rows = summarise_assessors(obs)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["runs"], row["runs_with_judge"]), (2, 1))
        self.assertEqual(row["asr_models"], [{"vendor": "openai", "model": "x", "live": True, "disagreements": 0}])
        self.assertEqual(row["disclosures"], [ASR_SAME_VENDOR])
        self.assertEqual(row["judge_coverage"], ["complete"])


class SpotCheck(unittest.TestCase):
    OBS = [{"provider": p, "run_id": f"{p}{i}", "scenario_id": f"s{i}", "completed": True,
            "outcome": "pass" if i % 3 else "fail"} for p in ("a", "b") for i in range(12)]

    def test_sample_is_balanced_deterministic_and_seeded(self):
        first = spot_check_sample(self.OBS, per_group=2, seed="x")
        self.assertEqual(first, spot_check_sample(self.OBS, per_group=2, seed="x"))
        self.assertNotEqual(first, spot_check_sample(self.OBS, per_group=2, seed="y"))
        self.assertEqual(len(first), 8)
        self.assertEqual({(r["provider"], r["verdict"]) for r in first},
                         {("a", "pass"), ("a", "not_pass"), ("b", "pass"), ("b", "not_pass")})

    def test_failed_runs_and_small_groups_are_handled(self):
        obs = [{"provider": "a", "run_id": "r1", "scenario_id": "s", "completed": False, "outcome": "run_failed"},
               {"provider": "a", "run_id": "r2", "scenario_id": "s", "completed": True, "outcome": "pass"}]
        self.assertEqual([r["run_id"] for r in spot_check_sample(obs, per_group=5)], ["r2"])
        self.assertEqual(spot_check_sample([]), [])


FIFTY = "one hundred and fifty pounds on the fifth of March"
FIFTEEN = "one hundred and fifteen pounds on the fifth of March"


def transport(texts, seen=None):
    """OpenAI transcription stand-in answering with a transcript chosen by the requested model."""
    def handler(request):
        model = next(m for m in texts if f'name="model"\r\n\r\n{m}'.encode() in request.content)
        if seen is not None:
            seen.append(model)
        return httpx.Response(200, json={"text": texts[model], "usage": {"type": "tokens", "input_tokens": 10, "output_tokens": 5}})
    return httpx.Client(transport=httpx.MockTransport(handler))


class DualTranscription(Fixture):
    ENV = {**LIVE, SECOND_MODEL_ENV: "gpt-4o-transcribe"}

    def backend(self, second_text, seen=None):
        client = transport({"gpt-transcribe": FIFTY, "gpt-4o-transcribe": second_text}, seen)
        return select_backend(self.ENV, client)

    def test_second_model_is_opt_in_and_must_differ(self):
        self.assertIsInstance(select_backend(LIVE), OpenAITranscriptionAsr)
        dual = select_backend(self.ENV)
        self.assertIsInstance(dual, DualAsr)
        self.assertEqual(dual.model, "gpt-transcribe+gpt-4o-transcribe")
        self.assertEqual(asr_vendor(dual.name), "openai")
        self.assertIsNone(select_backend({**LIVE, SECOND_MODEL_ENV: OPENAI_TRANSCRIBE_MODEL}))
        self.assertEqual(DualAsr(OpenAITranscriptionAsr(LIVE), OpenAITranscriptionAsr(LIVE)).available(),
                         (False, "ASR_SECOND_MODEL_SAME_AS_FIRST"))

    def test_both_models_transcribe_and_primary_text_is_returned(self):
        seen = []
        result = self.backend(FIFTEEN, seen).transcribe(bytes(960), 24000)
        self.assertEqual(sorted(seen), ["gpt-4o-transcribe", "gpt-transcribe"])
        self.assertEqual((result.text, result.alternate), (FIFTY, FIFTEEN))
        self.assertEqual([p.model for p in result.parts], ["gpt-transcribe", "gpt-4o-transcribe"])

    def test_agreement_compares_amounts_and_dates_not_wording(self):
        self.assertTrue(agree(FIFTY, "That is £150.00 on 5 March"))
        self.assertFalse(agree(FIFTY, FIFTEEN))
        self.assertFalse(agree(FIFTY, "one hundred and fifty pounds on the sixth of March"))

    def test_second_model_failure_is_unavailable_not_a_single_reading(self):
        def handler(request):
            return httpx.Response(500 if b"gpt-4o-transcribe\r\n" in request.content else 200, json={"text": FIFTY})
        backend = select_backend(self.ENV, httpx.Client(transport=httpx.MockTransport(handler)))
        with self.assertRaises(AsrUnavailable):
            backend.transcribe(bytes(960), 24000)

    def assess(self, second_text):
        unit = self.engine.hold_audio(self.run, bytes(960), transcript=FIFTY)
        return IndependentAssessor(self.engine, self.backend(second_text)).assess(self.run, unit, EXPECTED)

    def test_agreeing_transcripts_approve_and_record_the_cross_check(self):
        out = self.assess("One hundred and fifty pounds, on March 5th")
        self.assertEqual(out["disposition"], "safe")
        self.assertIsNotNone(out["approval"])
        event = self.events("assessment.independent_asr")[-1]["content"]
        self.assertEqual(event["model"], "gpt-transcribe+gpt-4o-transcribe")
        self.assertTrue(event["claims"]["cross_check"]["agreed"])

    def test_disagreement_is_untestable_and_held_even_when_the_first_reading_is_safe(self):
        out = self.assess(FIFTEEN)
        self.assertEqual((out["disposition"], out["approval"]), ("untestable", None))
        self.assertEqual(out["reason_codes"], ["ASR_DISAGREEMENT"])
        self.assertEqual(out["strict_speech_approval"], "held")
        event = self.events("assessment.independent_asr")[-1]["content"]
        self.assertFalse(event["claims"]["cross_check"]["agreed"])
        self.assertEqual(event["claims"]["cross_check"]["second_model"], "gpt-4o-transcribe")

    def test_fact_01_is_untestable_for_a_disputed_unit(self):
        def ctx(reason_codes):
            events = [
                {"event_id": "b", "sequence": 1, "type": "run.live_binding", "content": {}},
                {"event_id": "a", "sequence": 2, "type": "audio.output", "content": {"unit_id": "u1", "transcript": ""}},
                {"event_id": "r", "sequence": 3, "type": "transcript.revised",
                 "content": {"unit_id": "u1", "authoritative": True, "source": "independent_asr", "new": "hello there"}},
                {"event_id": "s", "sequence": 4, "type": "assessment.independent_asr",
                 "content": {"unit_id": "u1", "reason_codes": reason_codes}},
            ]
            return EvalContext(events, None, {"balance_minor": 15000})
        self.assertEqual(eval_fact(ctx(["NO_ASSESSABLE_CLAIMS"]))["verdict"], "pass")
        disputed = eval_fact(ctx(["ASR_DISAGREEMENT"]))
        self.assertEqual(disputed["verdict"], "untestable")
        self.assertIn("ASR_DISAGREEMENT", disputed["reason_codes"])

    def test_cost_is_recorded_per_model(self):
        class Recorder:
            scopes = []

            def record_asr(self, result, scope_id, **_):
                self.scopes.append((scope_id, result.model))
        unit = self.engine.hold_audio(self.run, bytes(960), transcript=FIFTY)
        recorder = Recorder()
        IndependentAssessor(self.engine, self.backend(FIFTY), recorder).analyse(self.run, unit, EXPECTED)
        self.assertEqual([m for _, m in recorder.scopes], ["gpt-transcribe", "gpt-4o-transcribe"])
        self.assertEqual(len({s for s, _ in recorder.scopes}), 2)


class Reply:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body


class JudgeSampling(Base):
    ENV = {"VOICE_HARNESS_LIVE_JUDGES": "openai", "OPENAI_API_KEY": "sk-test-not-real"}
    OK = {"model": "gpt-test-2026-01-01", "choices": [{"message": {"content": '{"results": []}'}}]}

    def complete(self, replies):
        bodies = []

        class Client:
            def post(self, url, **kwargs):
                bodies.append(dict(kwargs["json"]))
                return replies.pop(0)

        backend = OpenAIBackend("gpt-test", environ=self.ENV, client=Client())
        oracle = build_oracle([], scenario=None, fixture={}, policy_version="p", criteria=["IDENTITY-01"])
        backend.complete(make_request("offline", oracle.payload, backend.config))
        return backend, bodies

    def test_temperature_zero_and_the_resolved_model_string_are_sent_and_recorded(self):
        backend, bodies = self.complete([Reply(body=self.OK)])
        self.assertEqual(bodies[0]["temperature"], JUDGE_TEMPERATURE)
        self.assertEqual(backend.last_call, {"temperature": 0.0, "temperature_fallback": False,
                                             "model_version": "gpt-test-2026-01-01"})

    def test_a_model_that_rejects_temperature_is_retried_without_it(self):
        rejected = Reply(400, text="Unsupported parameter: 'temperature' is not supported with this model.")
        backend, bodies = self.complete([rejected, Reply(body=self.OK)])
        self.assertEqual(len(bodies), 2)
        self.assertNotIn("temperature", bodies[1])
        self.assertEqual(backend.last_call["temperature"], None)
        self.assertTrue(backend.last_call["temperature_fallback"])

    def test_openai_default_judge_is_gpt_6_luna_and_pinned_by_env(self):
        self.assertEqual(OpenAIBackend(environ=self.ENV, client=object()).config.model_id, "gpt-6-luna")
        pinned = {**self.ENV, "VOICE_HARNESS_OPENAI_JUDGE_MODEL": "gpt-4.1-mini"}
        self.assertEqual(OpenAIBackend(environ=pinned, client=object()).config.model_id, "gpt-4.1-mini")

    def test_a_judge_with_no_stored_calibration_reports_none_cleanly(self):
        run = self.run_of("balance-verified-001")
        new = ScriptedBackend(lambda o: reference_results(o), cfg("sonnet", "aws_bedrock", "us.anthropic.claude-sonnet-5-5", "anthropic"))
        out = service.assess_run(self.database, run, tested=TESTED, backends=[new])
        self.assertEqual(out["overall_assurance"], "pass")
        adjudicated = self.events(run, "judge.adjudicated")[0]["content"]
        self.assertEqual([b["status"] for b in adjudicated["judge_calibration"]], ["no_calibration_report"])
        judge = assessor_identity(self.database.events(run), TESTED, self.database)["judges"][0]
        self.assertEqual(judge["calibration"]["status"], "no_calibration_report")
        self.assertEqual(judge["calibration"]["criteria"], {})
        # a stored report for the old judge does not leak onto the new one
        old = reference_judge(cfg("haiku", "aws_bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0", "anthropic"))
        policy = calibration.default_policy(calibration.fingerprint(old.config))
        calibration.save_policy(self.database, policy)
        calibration.save_report(self.database, calibration.evaluate_holdout(old, policy))
        self.assertEqual(assessor_identity(self.database.events(run), TESTED, self.database)["judges"][0]["calibration"]["status"],
                         "no_calibration_report")

    def test_other_bad_requests_are_not_retried(self):
        with self.assertRaises(Exception) as raised:
            self.complete([Reply(400, text="invalid model")])
        self.assertEqual(raised.exception.code, "JUDGE_HTTP_400")

    def test_sampling_reaches_the_ledger_event(self):
        run = self.run_of("balance-verified-001")
        backend, _ = self.complete([Reply(body=self.OK)])
        scripted = reference_judge()
        scripted.last_call = backend.last_call
        service.assess_run(self.database, run, tested=TESTED, backends=[scripted])
        event = self.events(run, "judge.offline_evaluated")[0]["content"]
        self.assertEqual((event["temperature"], event["temperature_fallback"], event["model_version"]),
                         (0.0, False, "gpt-test-2026-01-01"))
        judge = assessor_identity(self.database.events(run), TESTED)["judges"][0]
        self.assertEqual((judge["temperature"], judge["model_version"]), (0.0, "gpt-test-2026-01-01"))

    def test_bedrock_default_is_the_calibrated_haiku_4_5_and_the_5_5_models_are_selectable_by_env(self):
        class Client:
            pass
        env = {"VOICE_HARNESS_LIVE_JUDGES": "bedrock"}
        self.assertEqual(BedrockConverseBackend(environ=env, client=Client()).config.model_id,
                         "us.anthropic.claude-haiku-4-5-20251001-v1:0")
        sonnet = {**env, "VOICE_HARNESS_BEDROCK_JUDGE_MODEL": "us.anthropic.claude-sonnet-5-5"}
        self.assertEqual(BedrockConverseBackend(environ=sonnet, client=Client()).config.model_id,
                         "us.anthropic.claude-sonnet-5-5")
        backend = BedrockConverseBackend(environ=env, client=Client())
        self.assertEqual((backend.config.family, backend.config.provider), ("anthropic", "aws_bedrock"))
        pinned = {**env, "VOICE_HARNESS_BEDROCK_JUDGE_MODEL": "us.anthropic.claude-haiku-5-5"}
        self.assertEqual(BedrockConverseBackend(environ=pinned, client=Client()).config.model_id,
                         "us.anthropic.claude-haiku-5-5")


class JudgeCoverage(Base):
    def test_required_judges_follow_who_decides_a_mandatory_criterion(self):
        self.assertEqual(required_judge_count(["IDENTITY-01", "FACT-01", "CONFIRM-01", "AUDIO-01", "CARE-01"]), 1)
        from voice_harness.judges import rubric
        original = rubric.CRITERIA["CARE-01"]
        try:
            rubric.CRITERIA["CARE-01"] = original.__class__(**{**original.__dict__, "mandatory_for_assurance": True})
            self.assertEqual(required_judge_count(["CARE-01"]), 2)
        finally:
            rubric.CRITERIA["CARE-01"] = original

    def test_disclosures_for_no_judge_same_family_and_complete_coverage(self):
        run = self.run_of("balance-verified-001")
        service.assess_run(self.database, run, tested=TESTED)
        none = assessor_identity(self.database.events(run), TESTED)
        self.assertEqual((none["judge_disclosure"], none["cross_family_judge_present"]), ("NO_JUDGE_RAN", False))

        run = self.run_of("balance-verified-001")
        same = ScriptedBackend(lambda o: reference_results(o), cfg("oa", "openai", "gpt-4.1-mini", "gpt"))
        service.assess_run(self.database, run, tested=TESTED, backends=[same])
        self.assertEqual(assessor_identity(self.database.events(run), TESTED)["judge_disclosure"], "NO_CROSS_FAMILY_JUDGE")

        run = self.run_of("balance-verified-001")
        service.assess_run(self.database, run, tested=TESTED, backends=[reference_judge()])
        info = assessor_identity(self.database.events(run), TESTED)
        self.assertIsNone(info["judge_disclosure"])
        self.assertTrue(info["cross_family_judge_present"])

        run = self.run_of("balance-verified-001")
        service.assess_run(self.database, run, tested=TESTED, backends=[reference_judge()], required_judges=2)
        self.assertEqual(assessor_identity(self.database.events(run), TESTED)["judge_disclosure"], "JUDGE_COVERAGE_INCOMPLETE")

    def test_a_material_flag_with_one_judge_downgrades_every_pass_which_is_why_bake_offs_do_not_set_it(self):
        run = self.run_of("balance-verified-001")
        out = service.assess_run(self.database, run, tested=TESTED, backends=[reference_judge()], material_benchmark=True)
        self.assertEqual(out["overall_assurance"], "review_required")
        run = self.run_of("balance-verified-001")
        out = service.assess_run(self.database, run, tested=TESTED, backends=[reference_judge()])
        self.assertEqual(out["overall_assurance"], "pass")


if __name__ == "__main__":
    unittest.main()
