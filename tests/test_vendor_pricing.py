"""The committed vendor price table loads, never carries an unestablished price and matches recorder identifiers."""

import unittest
from pathlib import Path

from voice_harness import cost
from voice_harness.pricing import load_table

TABLE = Path(__file__).parent.parent / "artifacts" / "pricing" / "vendor-pricing-2026-10-18.json"


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

    def test_rows_match_recorder_identifiers(self):
        self.assertEqual(self.find("openai_realtime", "openai_realtime", "gpt-realtime-2.1", None,
                                   "audio_output_tokens").unit_price, "64.00")
        self.assertEqual(self.find("aws_nova_sonic", "bedrock", "amazon.nova-2-5-sonic", "us-east-1",
                                   "text_input_tokens").unit_price, "0.33")
        self.assertEqual(self.find("openai", "judge", "gpt-4.1-mini", None, "judge_output_tokens").unit_price, "1.60")

    def test_unpriced_families_stay_unknown(self):
        self.assertIsNone(self.find("google_gemini_live", "google_gemini_live", "gemini-live-2.5-flash-native-audio",
                                    None, "input_tokens_unclassified"))


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

    def test_no_seconds_reported_adds_no_report(self):
        reports = cost.reports_from_adapter_usage(
            "xai_grok_voice", {"input_tokens": 1, "output_tokens": 2, "raw": {}}, scope_id="r", usage_date="2026-10-18")
        self.assertNotIn("billable_audio_seconds", [r["meter_id"] for r in reports])


if __name__ == "__main__":
    unittest.main()
