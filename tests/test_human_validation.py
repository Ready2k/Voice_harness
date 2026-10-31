import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from voice_harness import human_validation as hv


def _events(run, reply, tool_status="success"):
    def ev(seq, kind, content):
        return {"sequence": seq, "type": kind, "content": content}
    return [
        ev(1, "input.caller_audio", {"turn": 0}),
        ev(2, "tool.requested", {"call_id": "c1", "tool": "get_policy_text", "arguments": {"topic": "fees"}}),
        ev(3, "decision", {"call_id": "c1", "action": "allow"}),
        ev(4, "tool.completed", {"call_id": "c1", "status": tool_status}),
        ev(5, "audio.output", {"unit_id": "u1", "transcript": reply}),
        ev(6, "transcript.revised", {"unit_id": "u1", "new": reply + " (asr)"}),
        ev(7, "judge.offline_evaluated", {"judge": {"judge_id": "judgeA"}, "results": [{"criterion_id": "FACT-01", "verdict": "pass"}]}),
    ]


def make_fixture(root: Path):
    ledger = root / "bakeoff.sqlite"
    db = sqlite3.connect(ledger)
    db.execute("CREATE TABLE harness_runs (run_id TEXT PRIMARY KEY, state TEXT, payload TEXT)")
    db.execute("CREATE TABLE harness_events (run_id TEXT, sequence INTEGER, payload TEXT)")
    db.execute("CREATE TABLE harness_scenario_definitions (scenario_id TEXT, version INTEGER, definition TEXT)")
    db.execute("INSERT INTO harness_scenario_definitions VALUES ('scn', 1, ?)",
               (json.dumps({"goal": "g", "turns": [{"customer": "What are the fees at Gemini Bank?"}]}),))
    observations = []
    for provider in ("alpha", "beta"):
        for i in range(10):
            run = f"run_{provider[0] * 3}{i}x{ord(provider[0])}"
            db.execute("INSERT INTO harness_runs VALUES (?,?,?)", (run, "completed", json.dumps({"config": {"scenario_id": "scn"}})))
            for e in _events(run, f"Reply {i}"):
                db.execute("INSERT INTO harness_events VALUES (?,?,?)", (run, e["sequence"], json.dumps({"type": e["type"], "content": e["content"]})))
            observations.append({"run_id": run, "provider": provider, "completed": True, "group": f"g{i % 3}", "scenario_id": "scn",
                                 "scenario": "Scenario", "outcome": "pass", "criteria": {"FACT-01": "pass", "AUDIO-01": "pass"},
                                 "replies_assessed": 1, "replies_unsafe": 0, "replies_untestable": 0,
                                 "metrics": {"task_completed": True, "legitimate_request": True}})
    observations.append({"run_id": "run_missing", "provider": "alpha", "completed": True, "group": "g0", "criteria": {}})
    db.commit()
    db.close()
    (root / "report.json").write_text(json.dumps({"observations": observations}))
    return ledger


class Stats(unittest.TestCase):
    def test_kappa_and_wilson(self):
        pairs = [("pass", "pass")] * 8 + [("fail", "fail")] * 4 + [("pass", "fail")] * 2 + [("fail", "pass")] * 2
        a = hv.agreement(pairs)
        self.assertEqual((a["n"], a["agree"]), (16, 12))
        self.assertAlmostEqual(a["kappa"], 0.4667, places=3)
        self.assertLess(a["wilson_95"]["low"], 0.75)
        self.assertGreater(a["wilson_95"]["high"], 0.75)
        self.assertEqual(a["confusion"]["pass -> fail"], 2)
        self.assertIsNone(hv.cohen_kappa([("pass", "pass")] * 3))
        self.assertIsNone(hv.wilson(0, 0)["low"])


class Sampling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = make_fixture(self.root)
        self.obs = hv.load_observations(self.root)
        self.db = hv.open_readonly(self.ledger)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_stratified_reproducible_and_skips_missing_ledger(self):
        available = hv.run_ids_in_ledger(self.db)
        a = hv.draw_sample(self.obs, available, fraction=0.1, min_per_provider=3, seed="s")
        b = hv.draw_sample(self.obs, available, fraction=0.1, min_per_provider=3, seed="s")
        c = hv.draw_sample(self.obs, available, fraction=0.1, min_per_provider=3, seed="other")
        self.assertEqual([o["run_id"] for o in a], [o["run_id"] for o in b])
        self.assertNotEqual([o["run_id"] for o in a], [o["run_id"] for o in c])
        self.assertEqual(sum(o["provider"] == "alpha" for o in a), 3)
        self.assertEqual(sum(o["provider"] == "beta" for o in a), 3)
        self.assertNotIn("run_missing", [o["run_id"] for o in a])

    def test_pack_is_blind_and_key_separate(self):
        sample = hv.draw_sample(self.obs, hv.run_ids_in_ledger(self.db), min_per_provider=2)
        pack, key = hv.build_pack(self.root, self.db, self.obs, sample, seed="s", fraction=0.1, min_per_provider=2)
        out = self.root / "out"
        files = hv.write_pack(out, pack, key)
        shared = "".join(files[k].read_text() for k in ("pack_json", "csv", "html"))
        self.assertNotIn("alpha", shared)
        self.assertNotIn("beta", shared)
        self.assertNotIn("Gemini", shared)  # provider brand in caller text is masked
        self.assertIn("(asr)", shared)  # independent-ASR revision is the transcript
        self.assertNotIn('"automated"', shared)
        cell = pack["cells"][0]
        self.assertEqual(cell["tool_calls"][0]["arguments"], {"topic": "fees"})
        self.assertEqual(cell["tool_calls"][0]["policy_decision"], "allow")
        self.assertEqual(set(cell["labels_requested"]), {"AUDIO-01", "FACT-01", "SAFE_SPEECH", "TASK_COMPLETED"})
        rows = list(csv.DictReader(io.StringIO(files["csv"].read_text())))
        self.assertEqual(len(rows), len(sample))
        self.assertEqual(rows[0]["label_FACT-01"], "")
        self.assertEqual(key["cells"][cell["blind_code"]]["provider"] in ("alpha", "beta"), True)
        self.assertEqual(key["cells"][cell["blind_code"]]["judges"]["FACT-01"], {"judgeA": "pass"})
        self.assertNotIn("</script><script", files["html"].read_text())

    def test_score_round_trip_json_and_csv(self):
        sample = hv.draw_sample(self.obs, hv.run_ids_in_ledger(self.db), min_per_provider=4)
        pack, key = hv.build_pack(self.root, self.db, self.obs, sample, seed="s", fraction=0.1, min_per_provider=4)
        labels = {code: dict(cell["automated"]) for code, cell in key["cells"].items()}
        first = next(iter(labels))
        labels[first]["FACT-01"] = "fail"
        path = self.root / "labels.json"
        path.write_text(json.dumps({"schema": hv.LABELS_SCHEMA, "labels": {c: {**v, "_notes": "x"} for c, v in labels.items()}}))
        result = hv.score(hv.read_labels(path), key)
        fact = result["scorer_vs_human"]["by_criterion"]["FACT-01"]
        self.assertEqual((fact["n"], fact["agree"]), (8, 7))
        self.assertEqual(fact["confusion"]["pass -> fail"], 1)
        self.assertEqual(result["judge_vs_human"]["judgeA"]["by_criterion"]["FACT-01"]["agree"], 7)
        self.assertEqual(result["scorer_vs_human"]["by_criterion"]["AUDIO-01"]["raw_agreement"], 1.0)
        self.assertIn("Scorer vs human", hv.summary_markdown(result))
        # CSV path: fill the shared CSV in and read it back.
        rows = list(csv.DictReader(io.StringIO(hv.pack_csv(pack))))
        for r in rows:
            r["label_FACT-01"] = "pass"
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        (self.root / "filled.csv").write_text(buf.getvalue())
        read = hv.read_labels(self.root / "filled.csv")
        self.assertEqual(set(read[first]), {"FACT-01"})
        self.assertEqual(hv.score(read, key)["scorer_vs_human"]["by_criterion"]["FACT-01"]["agree"], 8)

    def test_unknown_codes_reported(self):
        result = hv.score({"HV-999": {"FACT-01": "pass"}}, {"cells": {}, "pack_id": "p", "source": "s"})
        self.assertEqual(result["unknown_blind_codes"], ["HV-999"])


if __name__ == "__main__":
    unittest.main()
