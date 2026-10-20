import json
import tempfile
import unittest
from pathlib import Path

from voice_harness import coverage
from voice_harness.capability_report import unknown_report


class ProbeEventTests(unittest.TestCase):
    def test_returns_cited_events_read_only_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            report = unknown_report("cap_x", "openai", "m")
            row = report.rows[0].model_copy(update={"evidence": report.rows[0].evidence.model_copy(
                update={"probe_event_ids": ("oai_ev_1", "oai_ev_9")})})
            report = report.model_copy(update={"rows": (row, *report.rows[1:])})
            (d / "openai-capability-report.json").write_text(report.model_dump_json())
            (d / "openai-probe-1.jsonl").write_text(
                json.dumps({"id": "oai_ev_1", "t": "t", "dir": "in", "type": "ws_open"}) + "\nnot json\n"
                + json.dumps({"id": "other"}) + "\n")
            result = coverage.probe_events("openai", d)
            self.assertEqual([e["event"]["id"] for e in result["events"]], ["oai_ev_1"])
            self.assertEqual(result["missing_ids"], ["oai_ev_9"])
            self.assertEqual(result["cited_count"], 2)
            self.assertIsNone(coverage.probe_events("aws_nova_sonic", d))

    def test_summary_carries_server_expiry_for_real_reports(self):
        items = coverage.build_registry()
        verified = [c for c in items if c.coverage_state == "implemented_and_verified"]
        for c in verified:
            self.assertTrue(c.expires_at)


if __name__ == "__main__":
    unittest.main()
