"""The committed vendor price table loads, never carries an unestablished price and matches recorder identifiers."""

import unittest
from pathlib import Path

from voice_harness import cost
from voice_harness.adapters.nova_sonic import NovaSonicAdapter
from voice_harness.cost_recorder import SERVICES
from voice_harness.live_providers import FAMILIES, TESTED_MODELS
from voice_harness.pricing import load_table

PRICING = Path(__file__).parent.parent / "artifacts" / "pricing"
TABLE = PRICING / "vendor-pricing-2026-10-24.json"


class VendorPricingTable(unittest.TestCase):
    def setUp(self):
        self.table = load_table(TABLE)

    def find(self, provider, service, model, region, meter):
        return self.table.find(provider=provider, service=service, model=model, region=region, meter_id=meter,
                               usage_date="2026-10-18")

    def test_every_row_is_a_dated_primary_source_price(self):
        for tariff in self.table.tariffs:
            self.assertTrue(tariff.evidence_ids)
            if tariff.unit_price is None:  # a deliberate unknown placeholder (xAI token meters), never a price
                self.assertEqual(tariff.source_status, "unestablished")
                self.assertTrue(tariff.unknown_reason)
            else:
                self.assertEqual(tariff.source_status, "primary_source")
                self.assertEqual(tariff.currency, "USD")

    def recorded(self, config_id, meter, region=None, usage_date="2026-10-24"):
        """The tariff for the identifiers a live run of ``config_id`` records (as live_providers builds the recorder)."""
        provider = FAMILIES[config_id]
        return self.table.find(provider=provider, service=SERVICES.get(provider, provider),
                               model=TESTED_MODELS[config_id]["model_id"], region=region, meter_id=meter,
                               usage_date=usage_date)

    def test_rows_match_recorder_identifiers(self):
        self.assertEqual(self.recorded("openai-realtime-live", "audio_output_tokens").unit_price, "64.00")
        self.assertEqual(self.recorded("nova-sonic-live", "text_input_tokens", NovaSonicAdapter().region).unit_price,
                         "0.33")
        self.assertEqual(self.recorded("xai-voice-live", "billable_audio_seconds").unit_price, "0.08")
        self.assertEqual(self.find("openai", "judge", "gpt-4.1-mini", None, "judge_output_tokens").unit_price, "1.60")

    def test_gemini_3_8_is_priced_from_the_developer_api_page(self):
        prices = {m: self.recorded("gemini-live", m, "us-central1").unit_price for m in
                  ("audio_input_tokens", "text_input_tokens", "audio_output_tokens", "text_output_tokens")}
        self.assertEqual(prices, {"audio_input_tokens": "3.00", "text_input_tokens": "0.75",
                                  "audio_output_tokens": "12.00", "text_output_tokens": "4.50"})
        self.assertIsNone(self.recorded("gemini-live", "input_tokens_unclassified", "us-central1"))  # unsplit stays unknown

    def test_the_2026_10_18_table_is_kept_unchanged_for_earlier_exports(self):
        old = load_table(PRICING / "vendor-pricing-2026-10-18.json")
        self.assertEqual(old.version, "2026-10-18")
        self.assertIsNotNone(old.find(provider="openai_realtime", service="openai_realtime", model="gpt-realtime-2.1",
                                      region=None, meter_id="audio_output_tokens", usage_date="2026-10-18"))
        self.assertEqual(self.find("openai", "judge", "gpt-4.1-mini", None, "judge_output_tokens").unit_price, "1.60")

    def test_unpriced_families_stay_unknown(self):
        self.assertIsNone(self.find("google_gemini_live", "google_gemini_live", "gemini-live-2.5-flash-native-audio",
                                    None, "input_tokens_unclassified"))  # unsplit totals cannot be priced


class NewestTableIsTheDefault(unittest.TestCase):
    def test_serve_live_defaults_to_the_newest_vendor_table(self):
        newest = sorted(PRICING.glob("vendor-pricing-*.json"))[-1].name
        self.assertEqual(newest, "vendor-pricing-2026-10-29.json")
        makefile = Path(__file__).parent.parent / "Makefile"
        if not makefile.exists():
            self.skipTest("the Makefile is not shipped into the test image")
        self.assertIn(newest, makefile.read_text())

    def test_reverified_rows_cite_the_official_page_with_todays_date(self):
        table = load_table(PRICING / "vendor-pricing-2026-10-24.json")
        rows = {t.tariff_id: t for t in table.tariffs}
        for tariff_id in ("openai-rt-audio-in", "openai-judge-41mini-out", "gemini-3-8-live-audio-input-tokens", "xai-voice-sts-minute"):
            self.assertTrue(any(e.endswith("#2026-10-24") for e in rows[tariff_id].evidence_ids), tariff_id)
        self.assertTrue(all(e.endswith("#2026-10-18") for e in rows["nova25-speech-in"].evidence_ids))  # not re-verifiable: unchanged


class XaiPerMinuteMeter(unittest.TestCase):
    """xAI usage carries billable_audio_seconds; it is priced per minute and token meters never add to it."""

    def reports(self, seconds):
        usage = {"input_tokens": 120, "output_tokens": 300, "billable_audio_seconds": seconds}
        return cost.reports_from_adapter_usage(
            "xai_grok_voice", {"input_tokens": 120, "output_tokens": 300, "raw": usage}, scope_id="resp_1",
            usage_date="2026-10-18", service="xai_grok_voice", model="grok-voice-3.0")

    def events(self, reports):
        return [{"type": "cost.usage_reported", "content": r, "event_id": f"e{i}"} for i, r in enumerate(reports)] + [
            {"type": "cost.expectation_declared", "content": {"components": ["voice"], "declared_by": "t"},
             "event_id": "x"}]

    def test_seconds_report_prices_per_minute_exactly(self):
        table = load_table(TABLE)
        report = next(r for r in self.reports(90) if r["meter_id"] == "billable_audio_seconds")
        self.assertEqual((report["unit"], report["quantity"]), ("second", "90"))
        _tariff, amount, _calc, reason = cost.price(report, table)
        self.assertIsNone(reason)
        self.assertEqual(str(amount), "0.120000")  # 1.5 min * $0.08

    def test_token_meters_are_superseded_so_the_run_is_complete(self):
        lines, summary, _ = cost.derive("run1", self.events(self.reports(60)), load_table(TABLE))
        active = [x for x in lines if not x["superseded_by"]]
        self.assertEqual([x["meter_id"] for x in active], ["billable_audio_seconds"])
        self.assertEqual(summary["completeness"], "complete")
        self.assertEqual(summary["summaries"][0]["total_cost"], "0.080000")

    def test_modality_split_token_meters_are_superseded_too(self):
        # The adapter now reports audio/text token meters; they are unpriced alternatives of the per-minute line.
        reports = [r for r in self.reports(60) if r["meter_id"] == "billable_audio_seconds"]
        reports += [{**reports[0], "report_id": f"resp_1:{m}:1", "meter_id": m, "unit": "token", "quantity": "10"}
                    for m in ("audio_input_tokens", "text_input_tokens", "audio_output_tokens", "text_output_tokens")]
        lines, summary, _ = cost.derive("run1", self.events(reports), load_table(TABLE))
        self.assertEqual([x["meter_id"] for x in lines if not x["superseded_by"]], ["billable_audio_seconds"])
        self.assertEqual(summary["completeness"], "complete")

    def test_no_seconds_reported_adds_no_report(self):
        reports = cost.reports_from_adapter_usage(
            "xai_grok_voice", {"input_tokens": 1, "output_tokens": 2, "raw": {}}, scope_id="r", usage_date="2026-10-18")
        self.assertNotIn("billable_audio_seconds", [r["meter_id"] for r in reports])


class GeminiModalitySplit(unittest.TestCase):
    """Real Gemini usageMetadata shape (probe 2026-10-18): per-modality counts price at the Live API rates."""

    USAGE = {"promptTokenCount": 249, "candidatesTokenCount": 20, "totalTokenCount": 269,
             "promptTokensDetails": [{"modality": "AUDIO", "tokenCount": 118}, {"modality": "TEXT", "tokenCount": 131}],
             "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 18}, {"modality": "TEXT", "tokenCount": 2}]}

    def reports(self, usage):
        return cost.reports_from_adapter_usage(
            "google_gemini_live", {"input_tokens": usage["promptTokenCount"], "output_tokens": usage["candidatesTokenCount"],
                                   "raw": usage}, scope_id="turn_1", usage_date="2026-10-18",
            service="google_gemini_live", model="gemini-live-2.5-flash-native-audio", region="us-central1")

    def test_split_reports_and_exact_total(self):
        reports = self.reports(self.USAGE)
        self.assertEqual({r["meter_id"]: r["quantity"] for r in reports},
                         {"audio_input_tokens": "118", "text_input_tokens": "131",
                          "audio_output_tokens": "18", "text_output_tokens": "2"})
        table = load_table(TABLE)
        amounts = [cost.price(r, table)[1] for r in reports]
        self.assertEqual(str(sum(amounts)), "0.000640")  # 354 + 66 (65.5 half-up) + 216 + 4 micro-dollars

    def test_text_only_turn_has_zero_audio_not_unknown(self):
        usage = {"promptTokenCount": 33, "candidatesTokenCount": 13,
                 "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 33}],
                 "candidatesTokensDetails": [{"modality": "TEXT", "tokenCount": 13}]}
        quantities = {r["meter_id"]: r["quantity"] for r in self.reports(usage)}
        self.assertEqual(quantities["audio_input_tokens"], "0")
        self.assertEqual(quantities["text_input_tokens"], "33")

    def test_unsplittable_usage_stays_unclassified(self):
        image = {**self.USAGE, "promptTokensDetails": [{"modality": "IMAGE", "tokenCount": 249}]}
        mismatch = {**self.USAGE, "candidatesTokenCount": 21}
        for usage in (image, mismatch):
            meters = {r["meter_id"] for r in self.reports(usage)}
            self.assertTrue(any(m.endswith("_tokens_unclassified") for m in meters), meters)
        self.assertNotIn("audio_input_tokens", {r["meter_id"] for r in self.reports(image)})


if __name__ == "__main__":
    unittest.main()


class Table20261029(unittest.TestCase):
    def test_gemini_38_rows_cite_the_vertex_page_and_prices_are_unchanged_from_10_24(self):
        new = {t.tariff_id: t for t in load_table(PRICING / "vendor-pricing-2026-10-29.json").tariffs}
        old = {t.tariff_id: t for t in load_table(PRICING / "vendor-pricing-2026-10-24.json").tariffs}
        self.assertEqual({k: new[k].unit_price for k in old}, {k: v.unit_price for k, v in old.items()})
        self.assertEqual(new["elevenlabs-agents-minute"].unit_price, "0.08")
        for tid, t in new.items():
            if t.model == "gemini-3.8-live":
                self.assertTrue(any("cloud.google.com" in e for e in t.evidence_ids), tid)
