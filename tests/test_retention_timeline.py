"""Retention over (simulated) time: hold expiry/grace/renewal, 7-day audio, long sweep horizons, exports and the scheduler.

Complements test_retention_exports (single-step RT-02 checks). Time is advanced with a clock offset, never by sleeping.
"""

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.exports import from_ndjson, reviewer_export, to_ndjson
from voice_harness.scenarios import run_scenario
from voice_harness.storage import Database

DAY = 86400


def prose(events):
    """Human-readable strings in ledger content (transcripts, messages, rationales): what retention should remove."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and " " in value and len(value) >= 15:
            found.add(value)

    for event in events:
        walk(event["content"])
    return found


class Timeline(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(self.root / "t.sqlite")))
        self.offset = [0.0]
        clock = lambda: time.time() + self.offset[0]  # noqa: E731
        # Event.occurred_at reads time.gmtime(); route it through the simulated clock so records written
        # during a sweep are dated like they would be in production.
        real_gmtime = time.gmtime
        patch = mock.patch("time.gmtime", lambda seconds=None: real_gmtime(clock() if seconds is None else seconds))
        patch.start()
        self.addCleanup(patch.stop)
        self.engine = Engine(self.database, clock=clock)
        self.evidence = Evidence(self.database, self.root / "objects", clock=clock)
        self.run = self.new_run()

    def new_run(self):
        run = self.engine.create(RunConfig().model_dump())
        run_scenario(self.engine, run, self.evidence)
        with self.database.transaction(run) as tx:
            tx.state["state"] = "completed"
        return run

    def age_to(self, day):
        self.offset[0] = day * DAY

    def sweep(self, run=None):
        run = run or self.run
        return self.evidence.purge_metadata(run)

    def assert_verifies(self, run=None):
        self.assertTrue(verify_bundle(self.evidence.bundle(run or self.run)))

    def test_hold_lifecycle_expiry_notice_grace_then_purge(self):
        self.age_to(5)
        view = self.evidence.hold(self.run, "dispute", "owner", self.evidence.clock() + 90 * DAY, "k")  # expires day 95
        for day in (31, 60, 94):
            self.age_to(day)
            self.assertEqual(self.sweep(), [], f"purged while the hold was live (day {day})")
        self.age_to(89)  # within seven days of expiry
        self.evidence.purge(self.run)
        notices = [e for e in self.database.events(self.run) if e["type"] == "preservation.expiry_notice"]
        self.assertEqual([n["content"]["hold_id"] for n in notices], [view["hold_id"]])
        self.evidence.purge(self.run)
        self.assertEqual(sum(e["type"] == "preservation.expiry_notice" for e in self.database.events(self.run)), 1)
        for day in (96, 101):  # expired, inside the seven-day grace
            self.age_to(day)
            self.assertEqual(self.sweep(), [], f"purged inside the grace window (day {day})")
        self.age_to(103)
        purged = self.sweep()
        self.assertTrue(purged)
        self.assert_verifies()
        notice = lambda: next(e for e in self.database.events(self.run) if e["type"] == "preservation.expiry_notice")  # noqa: E731
        self.assertNotIn("tombstone", notice(), "the notice is only 14 days old at day 103")
        self.age_to(120)  # 31 days after the notice, hold long gone
        self.assertTrue(self.sweep())
        self.assertIn("tombstone", notice(), "hold lifecycle records must expire with the hold, not outlive it")
        self.assert_verifies()

    def test_renewal_extends_protection_past_the_original_expiry(self):
        self.age_to(5)
        view = self.evidence.hold(self.run, "dispute", "owner", self.evidence.clock() + 30 * DAY, "k")  # expires day 35
        self.age_to(30)
        self.evidence.renew(self.run, view["hold_id"], self.evidence.clock() + 60 * DAY, "still disputed")  # day 90
        for day in (50, 91):  # unrenewed, day 43+ would already be purgeable
            self.age_to(day)
            self.assertEqual(self.sweep(), [], f"renewal did not protect the run (day {day})")
        self.age_to(98)
        self.assertTrue(self.sweep())
        self.assert_verifies()

    def test_audio_expires_after_seven_days_but_metadata_waits_thirty(self):
        sha = self.evidence.put(self.run, bytes(960))
        path = self.root / "objects" / self.run / sha
        self.assertTrue(path.exists())
        self.age_to(6)
        self.assertEqual(self.evidence.purge(self.run), [])
        self.assertTrue(path.exists())
        self.age_to(8)
        self.assertEqual(self.evidence.purge(self.run), [sha])
        self.assertEqual(self.sweep(), [], "metadata swept at day 8")
        self.assertFalse(path.exists())
        bundle = self.evidence.bundle(self.run)
        self.assertFalse(next(a for a in bundle["artifacts"] if a.get("sha256") == sha)["available"])
        self.assertIn("artifact.purged", [e["type"] for e in bundle["events"]])
        self.assertFalse([e for e in bundle["events"] if "tombstone" in e], "metadata tombstoned at day 8, before the 30-day mark")
        self.assertTrue(verify_bundle(bundle))

    def test_a_held_artifact_survives_its_seven_days_and_goes_after_the_grace(self):
        sha = self.evidence.put(self.run, bytes(960))
        self.evidence.hold(self.run, "dispute", "owner", self.evidence.clock() + 20 * DAY, "k")  # expires day 20
        path = self.root / "objects" / self.run / sha
        self.age_to(8)
        self.assertEqual(self.evidence.purge(self.run), [])
        self.assertTrue(path.exists())
        self.age_to(26)  # expired at day 20, grace to day 27
        self.assertEqual(self.evidence.purge(self.run), [])
        self.age_to(28)
        self.assertEqual(self.evidence.purge(self.run), [sha])
        self.assertFalse(path.exists())
        self.assert_verifies()

    def test_daily_sweeps_for_five_months_grow_the_ledger_by_at_most_one_event_a_month(self):
        base = len(self.database.events(self.run))
        for day in range(1, 151):
            self.age_to(day)
            self.evidence.purge(self.run)
            self.sweep()
            if day % 30 == 0:
                self.assert_verifies()
        events = self.database.events(self.run)
        purges = [e for e in events if e["type"] == "retention.metadata_purged"]
        self.assertTrue(1 <= len(purges) <= 5, f"{len(purges)} purge events in 150 days")
        self.assertEqual(len(events), base + len(purges), "sweeps appended something other than purge records")
        self.assertEqual(self.sweep(), [])
        self.assert_verifies()

    def test_ndjson_and_reviewer_exports_of_a_tombstoned_run_verify_and_leak_nothing(self):
        originals = prose(self.database.events(self.run))
        self.assertTrue(originals)
        self.age_to(31)
        self.assertTrue(self.sweep())
        for name, bundle in (("bundle", self.evidence.bundle(self.run)), ("reviewer", reviewer_export(self.evidence, self.run))):
            self.assertTrue(verify_bundle(bundle), name)
            self.assertTrue(verify_bundle(from_ndjson(to_ndjson(bundle))), name + " via ndjson")
            text = json.dumps(bundle)
            self.assertEqual([s for s in originals if s in text], [], f"{name} still carries purged content")

    def test_exports_taken_before_the_purge_still_verify_after_it(self):
        before = self.evidence.bundle(self.run)
        self.age_to(31)
        self.sweep()
        self.assertTrue(verify_bundle(before), "an earlier export must stay valid on its own")
        after = self.evidence.bundle(self.run)
        self.assertEqual(before["manifest"]["event_count"] + 1, after["manifest"]["event_count"])

    def tables_text(self):
        """Per-table text of this run's rows only (PostgreSQL tests share one database across runs)."""
        with self.database.connection() as db:
            if self.database.postgres:
                tables = [r["table_name"] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()]
                has_run = lambda t: any(r["name"] == "run_id" for r in db.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_name=?", (t,)).fetchall())  # noqa: E731
            else:
                tables = [r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                has_run = lambda t: any(r["name"] == "run_id" for r in db.execute(f"SELECT name FROM pragma_table_xinfo('{t}')").fetchall())  # noqa: E731, S608
            out = {}
            for name in tables:
                if has_run(name):  # tables without run_id hold global definitions, not this run's content
                    rows = db.execute(f"SELECT * FROM {name} WHERE run_id=?", (self.run,)).fetchall()  # noqa: S608 - names come from the catalogue
                    out[name] = " ".join(json.dumps(dict(r), default=str) for r in rows)
            return out

    def test_tombstoning_removes_content_from_projection_tables_and_run_state(self):
        """Transcript text, tool-result messages and judge rationales are purged everywhere with the ledger payloads."""
        originals = prose(self.database.events(self.run))
        self.age_to(31)
        self.assertTrue(self.sweep())
        leaks = {table: [s for s in originals if s in text] for table, text in self.tables_text().items()}
        leaks = {table: found for table, found in leaks.items() if found and table != "harness_events"}
        self.assertEqual(leaks, {}, "purged content is still readable outside the ledger")

    def test_scrubbed_rows_keep_their_hashes_outcomes_and_citations(self):
        before = self.database.get(self.run)
        call_id, call = next(iter(before["calls"].items()))
        with self.database.connection() as db:
            hashes = {r["revision_id"]: r["text_hash"] for r in db.execute("SELECT revision_id, text_hash FROM harness_transcript_segments WHERE run_id=?", (self.run,)).fetchall()}
            verdicts = {r["finding_id"]: r["verdict"] for r in db.execute("SELECT finding_id, verdict FROM harness_judge_findings WHERE run_id=?", (self.run,)).fetchall()}
        self.assertTrue(hashes and verdicts)
        self.age_to(31)
        self.sweep()
        after = self.database.get(self.run)["calls"][call_id]["result"]
        self.assertEqual((after["status"], after["error_code"], after["evidence_event_ids"]), (call["result"]["status"], call["result"]["error_code"], call["result"]["evidence_event_ids"]))
        self.assertNotIn("error", after)
        self.assertEqual(after["tombstone"]["reason"], "retention_expired")
        with self.database.connection() as db:
            self.assertEqual(hashes, {r["revision_id"]: r["text_hash"] for r in db.execute("SELECT revision_id, text_hash FROM harness_transcript_segments WHERE run_id=?", (self.run,)).fetchall()})
            self.assertEqual(verdicts, {r["finding_id"]: r["verdict"] for r in db.execute("SELECT finding_id, verdict FROM harness_judge_findings WHERE run_id=?", (self.run,)).fetchall()})
            self.assertEqual({r["text_value"] for r in db.execute("SELECT text_value FROM harness_transcript_segments WHERE run_id=?", (self.run,)).fetchall()}, {""})
        self.assert_verifies()

    def test_runs_tombstoned_before_the_scrub_existed_are_backfilled(self):
        self.age_to(31)
        self.sweep()
        with self.database.transaction(self.run) as tx:  # put back what the old sweep left behind
            tx.db.execute("UPDATE harness_transcript_segments SET text_value='legacy transcript text' WHERE run_id=?", (self.run,))
            tx.db.execute("UPDATE harness_judge_findings SET rationale='legacy judge rationale' WHERE run_id=?", (self.run,))
        text = " ".join(self.tables_text().values())
        self.assertIn("legacy transcript text", text)
        self.assertEqual(self.sweep(), [], "nothing new is eligible, yet the next sweep must still scrub")
        text = " ".join(self.tables_text().values())
        self.assertNotIn("legacy transcript text", text)
        self.assertNotIn("legacy judge rationale", text)

    def test_a_run_wide_hold_keeps_projection_content_until_the_hold_and_grace_end(self):
        self.age_to(5)
        self.evidence.hold(self.run, "dispute", "owner", self.evidence.clock() + 60 * DAY, "k")  # expires day 65
        originals = prose(self.database.events(self.run))
        for day in (31, 66):
            self.age_to(day)
            self.assertEqual(self.sweep(), [])
            text = " ".join(self.tables_text().values())
            self.assertTrue([s for s in originals if s in text], f"content scrubbed under a live hold (day {day})")
        self.age_to(73)
        self.assertTrue(self.sweep())
        self.assertEqual({t: [s for s in originals if s in x] for t, x in self.tables_text().items() if t != "harness_events" and [s for s in originals if s in x]}, {})

    def test_a_finding_hold_keeps_its_judge_run_rationale_and_scrubs_the_rest(self):
        events = self.database.events(self.run)
        judged = next(e for e in events if e["type"] == "judge.completed")
        finding = judged["event_id"] + ":" + judged["content"]["findings"][0]["criterion_id"]
        self.evidence.hold(self.run, "dispute", "owner", self.evidence.clock() + 90 * DAY, "k", finding_ids=[finding])
        self.age_to(31)
        self.assertTrue(self.sweep())
        with self.database.connection() as db:
            rationales = [r["rationale"] for r in db.execute("SELECT rationale FROM harness_judge_findings WHERE run_id=? AND judge_run_id=?", (self.run, judged["event_id"])).fetchall()]
        self.assertTrue(rationales and all(rationales), "a held finding must stay readable")
        self.assert_verifies()

    def new_unfinished_run(self, pcm):
        run = self.engine.create(RunConfig().model_dump())
        self.engine.hold_audio(run, pcm, transcript="held words that must not outlive the audio")
        return run

    def run_state_text(self, run):
        with self.database.connection() as db:
            return json.dumps(self.database.decoded(db.execute("SELECT payload FROM harness_runs WHERE run_id=?", (run,)).fetchone()["payload"]))

    def test_audio_copy_in_run_state_expires_with_the_seven_day_audio(self):
        import base64

        pcm = bytes(range(1, 251)) * 4
        run = self.new_unfinished_run(pcm)
        encoded = base64.b64encode(pcm).decode()
        with self.database.transaction(run) as tx:
            tx.state["state"] = "completed"
        for day in (0, 6):
            self.age_to(day)
            self.evidence.purge(run)
            self.assertIn(encoded, self.run_state_text(run), f"audio dropped early (day {day})")
        self.age_to(8)
        self.evidence.purge(run)
        state = self.run_state_text(run)
        self.assertNotIn(encoded, state)
        self.assertIn("pcm_purged_at", state)
        self.assertTrue(verify_bundle(self.evidence.bundle(run)))

    def test_audio_copy_is_kept_while_the_run_can_still_release_it(self):
        import base64

        pcm = bytes(range(1, 251)) * 4
        run = self.new_unfinished_run(pcm)
        self.age_to(40)
        self.evidence.purge(run)
        self.assertIn(base64.b64encode(pcm).decode(), self.run_state_text(run))

    def test_a_hold_keeps_the_audio_copy_through_its_grace(self):
        import base64

        pcm = bytes(range(1, 251)) * 4
        run = self.new_unfinished_run(pcm)
        with self.database.transaction(run) as tx:
            tx.state["state"] = "completed"
        self.evidence.hold(run, "dispute", "owner", self.evidence.clock() + 20 * DAY, "k")  # expires day 20, grace to 27
        encoded = base64.b64encode(pcm).decode()
        for day in (8, 26):
            self.age_to(day)
            self.evidence.purge(run)
            self.assertIn(encoded, self.run_state_text(run), f"held audio dropped (day {day})")
        self.age_to(28)
        self.evidence.purge(run)
        self.assertNotIn(encoded, self.run_state_text(run))

    def test_ledger_table_itself_holds_no_purged_content(self):
        originals = prose(self.database.events(self.run))
        self.age_to(31)
        self.assertTrue(self.sweep())
        text = self.tables_text()["harness_events"]
        self.assertTrue(text)
        self.assertEqual([s for s in originals if s in text], [])


class Scheduler(unittest.TestCase):
    def test_the_api_background_sweep_tombstones_an_aged_terminal_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "t.sqlite")))
            app = create_app(database, root / "objects", token="t" * 32, recover=False)
            run = app.state.engine.create(RunConfig().model_dump())
            run_scenario(app.state.engine, run, app.state.evidence)
            with database.transaction(run) as tx:
                tx.state["state"] = "completed"
            app.state.evidence.clock = lambda: time.time() + 31 * DAY
            real_sleep = asyncio.sleep

            async def fast(_delay, *args, **kwargs):
                await real_sleep(0.01)

            with mock.patch("asyncio.sleep", fast), TestClient(app):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if any(e["type"] == "retention.metadata_purged" for e in database.events(run)):
                        break
                    time.sleep(0.05)
            purged = [e for e in database.events(run) if e["type"] == "retention.metadata_purged"]
            self.assertTrue(purged, "the scheduler never ran the metadata sweep")
            self.assertTrue(verify_bundle(app.state.evidence.bundle(run)))


if __name__ == "__main__":
    unittest.main()
