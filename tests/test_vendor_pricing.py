"""The committed vendor price table loads, never carries an unestablished price and matches recorder identifiers."""

import unittest
from pathlib import Path

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
            self.assertEqual(tariff.source_status, "primary_source")
            self.assertEqual(tariff.currency, "USD")
            self.assertTrue(tariff.evidence_ids)

    def test_rows_match_recorder_identifiers(self):
        self.assertEqual(self.find("openai_realtime", "openai_realtime", "gpt-realtime-2.1", None,
                                   "audio_output_tokens").unit_price, "64.00")
        self.assertEqual(self.find("aws_nova_sonic", "bedrock", "amazon.nova-2-5-sonic", "us-east-1",
                                   "text_input_tokens").unit_price, "0.33")
        self.assertEqual(self.find("openai", "judge", "gpt-4.1-mini", None, "judge_output_tokens").unit_price, "1.60")

    def test_unpriced_families_stay_unknown(self):
        self.assertIsNone(self.find("xai_grok_voice", "xai_grok_voice", "grok-voice-3.0", None,
                                    "input_tokens_unclassified"))
        self.assertIsNone(self.find("google_gemini_live", "google_gemini_live", "gemini-live-2.5-flash-native-audio",
                                    None, "input_tokens_unclassified"))


if __name__ == "__main__":
    unittest.main()
