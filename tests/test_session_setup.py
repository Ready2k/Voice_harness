"""Provider session setup time: the typed ledger event and its report."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from voice_harness import bakeoff_metrics as bm
from voice_harness import live_providers
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.event_contracts import EVENT_CONTENT_CONTRACTS
from voice_harness.storage import Database


class SlowAdapter:
    def __init__(self, delay=0.05, fail=False):
        self.delay, self.fail = delay, fail

    async def open_session(self, config):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ValueError("PROVIDER_UNREACHABLE")
        return {"session": "ok"}


class Recording(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = Engine(Database(str(Path(directory.name) / "t.sqlite")))
        self.run = self.engine.create(RunConfig().model_dump())

    def events(self):
        return [e for e in self.engine.database.events(self.run) if e["type"] == "adapter.session_ready"]

    def test_open_records_how_long_the_provider_took(self):
        receipt = asyncio.run(live_providers._open_timed(self.engine, self.run, SlowAdapter(0.05), {}))
        self.assertEqual(receipt, {"session": "ok"})
        (event,) = self.events()
        content = event["content"]
        self.assertEqual(content["adapter"], "SlowAdapter")
        self.assertGreaterEqual(content["setup_ms"], 45)
        self.assertLess(content["setup_ms"], 2000)
        self.assertAlmostEqual(content["ready_ms"] - content["started_ms"], content["setup_ms"], delta=1, msg="same monotonic clock")  # each value is rounded separately
        EVENT_CONTENT_CONTRACTS["adapter.session_ready"].model_validate(content)

    def test_a_failed_open_records_nothing_and_still_raises(self):
        with self.assertRaises(ValueError):
            asyncio.run(live_providers._open_timed(self.engine, self.run, SlowAdapter(0, fail=True), {}))
        self.assertEqual(self.events(), [])

    def test_an_evidence_write_failure_fails_the_open(self):
        class Broken:
            def transaction(self, run_id):
                raise OSError("EVIDENCE_WRITE_FAILED")

        self.engine.database = Broken()
        with self.assertRaises(OSError):
            asyncio.run(live_providers._open_timed(self.engine, self.run, SlowAdapter(0), {}))

    def test_the_event_schema_is_closed(self):
        contract = EVENT_CONTENT_CONTRACTS["adapter.session_ready"]
        good = {"adapter": "a", "setup_ms": 1, "started_ms": 1, "ready_ms": 2, "clock_domain_id": "c"}
        contract.model_validate(good)
        with self.assertRaises(ValueError):
            contract.model_validate({**good, "extra": 1})
        with self.assertRaises(ValueError):
            contract.model_validate({**good, "setup_ms": -1})


def observation(provider, scenario, setup_ms):
    block = {"version": 1, "legitimate_request": True, "served": True, "turns": [], "faults": [], "setup_ms": setup_ms,
             "tools": {"executed": 1, "expected": 1, "missing": 0, "extra": 0, "unexpected": 0, "redundant": 0,
                       "failed": 0, "denied": 0}}
    if setup_ms is None:
        del block["setup_ms"]  # a run recorded before the event existed
    return {"provider": provider, "scenario_id": scenario, "repetition": 0, "completed": True, "outcome": "pass",
            "attempts": 1, "turns": [], "metrics": block, "cost": None, "earlier_costs": []}


class Report(unittest.TestCase):
    def setup(self, rows, provider="p"):
        summary = bm.summarise(["p", "q"], rows)
        return next(r for r in summary["by_provider"] if r["provider"] == provider)["latency_detail"]["setup"]

    def test_setup_median_and_p90_per_provider(self):
        rows = [observation("p", f"s{i}", ms) for i, ms in enumerate([400, 500, 600, 700, 1500])]
        setup = self.setup(rows)
        self.assertEqual((setup["recorded"], setup["runs_recorded"], setup["runs"]), (True, 5, 5))
        self.assertEqual((setup["median"], setup["p90"], setup["n"]), (600, 1180, 5))

    def test_older_runs_are_counted_not_zeroed(self):
        rows = [observation("p", "a", 300), observation("p", "b", None)]
        setup = self.setup(rows)
        self.assertEqual((setup["recorded"], setup["runs_recorded"], setup["runs"], setup["median"]), (True, 1, 2, 300))

    def test_no_recorded_setup_is_not_recorded(self):
        setup = self.setup([observation("p", "a", None)])
        self.assertEqual((setup["recorded"], setup["runs_recorded"], setup["median"], setup["p90"]), (False, 0, None, None))


if __name__ == "__main__":
    unittest.main()
