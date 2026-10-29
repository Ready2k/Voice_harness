import copy
import json
import tempfile
import unittest
from pathlib import Path

from voice_harness import perf_gate


def run(p95=10.0, count=1000):
    stages = {"total_path": {"p50_ms": p95 / 2, "p95_ms": p95, "count": count}}
    profile = {"sessions": 1, "payload_scale": 1, "instrumented": False, "stages": stages,
               "timeouts": 0, "failures": 0, "dropped_samples": 0, "discontinuous_samples": 0}
    ws = {"sessions": 1, "distribution": {"p50_ms": p95 / 2, "p95_ms": p95, "count": count}, "failures": 0, "discontinuities": 0}
    return {"benchmark_version": "t", "source_hash": "h", "environment": {}, "profiles": [profile], "websocket_profiles": [ws]}


class PerfGate(unittest.TestCase):
    def setUp(self):
        self.baseline = perf_gate.summarize(run())

    def test_identical_run_passes(self):
        problems, rows = perf_gate.check(run(), self.baseline)
        self.assertEqual(problems, [])
        self.assertEqual(len(rows), 2)

    def test_noise_within_tolerance_passes(self):
        self.assertEqual(perf_gate.check(run(p95=19.9), self.baseline)[0], [])

    def test_order_of_magnitude_regression_fails(self):
        problems, _ = perf_gate.check(run(p95=85.0), self.baseline)
        self.assertEqual(len(problems), 2)
        self.assertIn("exceeds limit", problems[0])

    def test_floor_protects_fast_profiles(self):
        fast = perf_gate.summarize(run(p95=1.0))
        self.assertEqual(perf_gate.check(run(p95=5.5), fast)[0], [])
        self.assertTrue(perf_gate.check(run(p95=6.5), fast)[0])

    def test_hard_failures_fail_even_when_fast(self):
        for field in ("timeouts", "failures", "dropped_samples", "discontinuous_samples"):
            bad = run()
            bad["profiles"][0][field] = 1
            self.assertTrue(perf_gate.check(bad, self.baseline)[0], field)
        bad = run()
        bad["websocket_profiles"][0]["discontinuities"] = 2
        self.assertTrue(perf_gate.check(bad, self.baseline)[0])

    def test_short_sample_fails(self):
        problems, _ = perf_gate.check(run(count=999), self.baseline)
        self.assertEqual(len(problems), 2)

    def test_missing_and_unbaselined_profiles_fail(self):
        missing = run()
        missing["websocket_profiles"] = []
        self.assertIn("missing from run", perf_gate.check(missing, self.baseline)[0][0])
        extra = copy.deepcopy(run())
        extra["profiles"][0]["sessions"] = 5
        problems = perf_gate.check(extra, self.baseline)[0]
        self.assertTrue(any("not in baseline" in p for p in problems))
        self.assertTrue(any("missing from run" in p for p in problems))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.json").write_text(json.dumps(run()))
            (root / "bad.json").write_text(json.dumps(run(p95=90.0)))
            (root / "base.json").write_text(json.dumps(self.baseline))
            self.assertEqual(perf_gate.main(["check", str(root / "good.json"), "--baseline", str(root / "base.json")]), 0)
            self.assertEqual(perf_gate.main(["check", str(root / "bad.json"), "--baseline", str(root / "base.json")]), 1)

    def test_checked_in_baseline_covers_reference_profiles(self):
        baseline = json.loads((Path(__file__).parents[1] / "artifacts/perf-baseline.json").read_text())
        keys = baseline["profiles"]
        self.assertEqual(sum(k.startswith("component/") for k in keys), 12)
        self.assertEqual(sum(k.startswith("websocket/") for k in keys), 3)
        self.assertTrue(all(v["count"] >= 1000 for v in keys.values()))


if __name__ == "__main__":
    unittest.main()
