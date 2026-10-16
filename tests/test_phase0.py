import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from voice_harness.capability_report import (
    REQUIRED_PRIMITIVES,
    CapabilityReport,
    CapabilityRow,
    Evidence,
    unknown_report,
)


class CapabilityReportTests(unittest.TestCase):
    def test_seed_report_fails_closed_on_all_primitives(self):
        r = unknown_report("cap_1", "openai", "gpt-realtime")
        self.assertEqual(sorted(r.failed_closed()), sorted(REQUIRED_PRIMITIVES))
        self.assertTrue(r.version_unresolved)

    def test_verified_requires_doc_and_probe_evidence(self):
        with self.assertRaises(ValidationError):
            CapabilityRow(name="audio_in", status="verified", evidence=Evidence(doc_url="https://x"))
        CapabilityRow(
            name="audio_in",
            status="verified",
            evidence=Evidence(doc_url="https://x", probe_event_ids=("e1",)),
        )

    def test_missing_and_duplicate_rows_rejected(self):
        r = unknown_report("cap_1", "openai", "m")
        data = r.model_dump()
        data["rows"] = data["rows"][1:]
        with self.assertRaises(ValidationError):
            CapabilityReport.model_validate(data)

    def test_unexpected_fields_rejected(self):
        data = unknown_report("cap_1", "openai", "m").model_dump()
        data["surprise"] = 1
        with self.assertRaises(ValidationError):
            CapabilityReport.model_validate(data)

    def test_cannot_claim_resolved_version_without_returned_id(self):
        data = unknown_report("cap_1", "openai", "m").model_dump()
        data["version_unresolved"] = False
        with self.assertRaises(ValidationError):
            CapabilityReport.model_validate(data)

    def test_expiry_after_seven_days(self):
        r = unknown_report("cap_1", "openai", "m")
        self.assertFalse(r.expired())
        self.assertTrue(r.expired(datetime.now(UTC) + timedelta(days=8)))



class NovaReportTests(unittest.TestCase):
    OBS = {
        "meta": {"modelId": "amazon.nova-2-5-sonic", "region": "us-east-1"},
        "flags": {"sdk": "x"},
        "notes": {},
        "evidence": {
            "audio_in": ["e1"], "audio_out": ["e2"], "tool_arguments_and_results": ["e3"],
            "turn_signalling": ["e4"], "usage_granularity": ["e5"],
        },
    }

    def test_report_passes_gate_and_marks_untested_rows_unknown(self):
        from voice_harness.nova_report import build_report

        r = build_report(self.OBS, "cap_t", "log.jsonl")
        by = {x.name: x for x in r.rows}
        self.assertEqual(r.failed_closed(), [])
        self.assertEqual(by["audio_in"].status, "verified")
        self.assertEqual(by["cancel_truncate"].status, "unknown")
        self.assertEqual(by["transcript_finality"].status, "unknown")  # no evidence supplied
        self.assertTrue(r.version_unresolved)


if __name__ == "__main__":
    unittest.main()
