import json
import tempfile
import unittest
from pathlib import Path

from voice_harness import load_test


def step(sessions, p95):
    return {"sessions": sessions, "turn_latency": {"p95_ms": p95}}


class Knee(unittest.TestCase):
    def test_none_when_latency_stays_flat(self):
        self.assertIsNone(load_test.knee([step(1, 2.0), step(6, 3.9), step(12, 4.0)]))

    def test_first_step_beyond_the_factor(self):
        self.assertEqual(load_test.knee([step(1, 2.0), step(6, 3.0), step(12, 4.1), step(24, 30.0)]), 12)

    def test_single_step_has_no_knee(self):
        self.assertIsNone(load_test.knee([step(1, 2.0)]))


class Run(unittest.TestCase):
    def test_small_run_reports_every_step_and_writes_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "load.json"
            result = load_test.load_test(str(output), steps=(1, 3), observations=60, pace_ms=2)
            self.assertEqual(json.loads(output.read_text())["load_test_version"], result["load_test_version"])
        self.assertEqual([s["sessions"] for s in result["steps"]], [1, 3])
        for record in result["steps"]:
            self.assertGreaterEqual(record["observations"], 60)
            self.assertGreaterEqual(record["pacing_lag"]["min_ms"], 0)
            self.assertGreater(record["turn_latency"]["p95_ms"], 0)
            self.assertGreater(record["cpu_cores_used"], 0)
        self.assertIn("Evidence-write failure", " ".join(result["limitations"]))


if __name__ == "__main__":
    unittest.main()
