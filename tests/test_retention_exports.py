"""RT-02 metadata retention, OB-04 export forms, AP-07 download link, HD-04 hold listing."""

import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.contracts import HoldRecord, RunConfig
from voice_harness.engine import Engine, digest
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.exports import from_ndjson, reviewer_export, to_ndjson
from voice_harness.retention import METADATA_TTL_SECONDS
from voice_harness.scenarios import run_scenario
from voice_harness.storage import Database

DAY = 86400


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = Database(
            os.environ.get("HARNESS_TEST_DATABASE_URL", str(self.root / "t.sqlite"))
        )
        self.offset = [0.0]  # added to real time so events age without sleeping
        clock = lambda: time.time() + self.offset[0]  # noqa: E731
        self.engine = Engine(self.database, clock=clock)
        self.evidence = Evidence(self.database, self.root / "objects", clock=clock)
        self.run = self.engine.create(RunConfig().model_dump())
        run_scenario(self.engine, self.run, self.evidence)
        with self.database.transaction(self.run) as tx:
            tx.state["state"] = "completed"

    def age(self, days):
        self.offset[0] += days * DAY


class MetadataRetentionTests(Base):
    def test_nothing_is_purged_before_thirty_days(self):
        self.age(29)
        self.assertEqual(self.evidence.purge_metadata(self.run), [])
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))

    def test_RT02_tombstones_expired_payloads_and_chain_still_verifies(self):
        before = self.evidence.bundle(self.run)
        count = before["manifest"]["event_count"]
        self.age(31)
        sequences = self.evidence.purge_metadata(self.run)
        self.assertEqual(sequences, list(range(1, count + 1)))
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(bundle))
        # Hashes and linkage retained; content gone; purge event appended and live.
        old = {e["event_id"]: e for e in before["events"]}
        for event in bundle["events"][:count]:
            self.assertEqual(event["event_hash"], old[event["event_id"]]["event_hash"])
            self.assertEqual(event["content"], {})
            self.assertEqual(event["tombstone"]["reason"], "retention_expired")
        self.assertEqual(bundle["events"][-1]["type"], "retention.metadata_purged")
        self.assertNotIn("tombstone", bundle["events"][-1])
        retention = bundle["manifest"]["retention"]
        self.assertEqual(retention["metadata_purge"], "tombstoned_after_30d_unless_held")
        self.assertEqual([t["sequence"] for t in retention["metadata_tombstones"]], sequences)
        # Idempotent: already-tombstoned events are never revisited (the fresh purge
        # record only looks expired here because the test clock is shifted).
        self.assertEqual(self.evidence.purge_metadata(self.run), [count + 1])
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))

    def test_RT02_chain_break_and_manifest_mismatch_are_detected(self):
        self.age(31)
        self.evidence.purge_metadata(self.run)
        good = self.evidence.bundle(self.run)
        broken = copy.deepcopy(good)
        broken["events"][1]["prev_hash"] = "0" * 64
        self.assertFalse(verify_bundle(broken))
        forged = copy.deepcopy(good)  # silently hide a tombstone from the manifest
        forged["manifest"]["retention"]["metadata_tombstones"].pop()
        forged["manifest_hash"] = digest(forged["manifest"])
        self.assertFalse(verify_bundle(forged))
        # A live (non-tombstone) event with edited content fails its hash.
        edited = copy.deepcopy(good)
        edited["events"][-1]["content"]["tombstoned_count"] = 0
        self.assertFalse(verify_bundle(edited))
        # A fake tombstone cannot launder a modified live event.
        fake = copy.deepcopy(self.evidence.bundle(self.run))
        fake["events"][-1].update(content={}, provider_event={}, causation_event_ids=[],
                                  trace_context={}, tombstone={"reason": "other"})
        self.assertFalse(verify_bundle(fake))

    def test_RT02_run_wide_hold_blocks_metadata_purge(self):
        self.evidence.hold(self.run, "dispute", "owner", self.evidence.clock() + 90 * DAY, "k")
        self.age(31)
        self.assertEqual(self.evidence.purge_metadata(self.run), [])
        # Released: seven-day grace still protects, then the sweep proceeds.
        hold_id = next(iter(self.database.get(self.run)["holds"]))
        self.evidence.release(self.run, hold_id, "resolved")
        self.assertEqual(self.evidence.purge_metadata(self.run), [])
        self.age(8)
        self.assertTrue(self.evidence.purge_metadata(self.run))
        self.assertTrue(verify_bundle(self.evidence.bundle(self.run)))

    def test_RT02_finding_hold_protects_only_its_closure(self):
        events = self.database.events(self.run)
        judged = next(e for e in events if e["type"] == "judge.completed")
        finding = judged["event_id"] + ":" + judged["content"]["findings"][0]["criterion_id"]
        view = self.evidence.hold(
            self.run, "dispute", "owner", self.evidence.clock() + 90 * DAY, "k2",
            finding_ids=[finding],
        )
        protected = set(view["scope"]["event_ids"]) | {view["audit_event_id"]}
        self.age(31)
        tombstoned = set(self.evidence.purge_metadata(self.run))
        by_sequence = {e["sequence"]: e["event_id"] for e in events}
        self.assertTrue(tombstoned)
        self.assertFalse(protected & {by_sequence.get(s) for s in tombstoned})
        bundle = self.evidence.bundle(self.run)
        self.assertTrue(verify_bundle(bundle))
        kept = [e for e in bundle["events"] if e["event_id"] in protected]
        self.assertTrue(all("tombstone" not in e and e["content"] for e in kept))

    def test_RT02_active_runs_are_not_purged(self):
        with self.database.transaction(self.run) as tx:
            tx.state["state"] = "running"
        self.age(40)
        self.assertEqual(self.evidence.purge_metadata(self.run), [])

    def test_metrics_tolerate_tombstoned_runs(self):
        from voice_harness.metrics import derive

        self.age(31)
        self.evidence.purge_metadata(self.run)
        derive(self.database.events(self.run))
        self.assertEqual(METADATA_TTL_SECONDS, 30 * DAY)


class ExportFormTests(Base):
    def test_OB04_bundle_embeds_hash_bound_schemas_and_versions(self):
        bundle = self.evidence.bundle(self.run)
        self.assertIn("canonical-event", bundle["schemas"])
        self.assertEqual(bundle["versions"]["event_schema"], "0.3")
        self.assertEqual(
            {n: digest(s) for n, s in bundle["schemas"].items()},
            bundle["manifest"]["schema_digests"],
        )
        self.assertTrue(verify_bundle(bundle))
        tampered = copy.deepcopy(bundle)
        tampered["schemas"]["canonical-event"]["title"] = "tampered"
        self.assertFalse(verify_bundle(tampered))

    def test_OB04_ndjson_round_trip_verifies(self):
        bundle = self.evidence.bundle(self.run)
        text = to_ndjson(bundle)
        lines = text.splitlines()
        self.assertTrue(all(json.loads(line) for line in lines))
        self.assertEqual(
            sum(json.loads(line)["record_type"] == "event" for line in lines),
            bundle["manifest"]["event_count"],
        )
        restored = from_ndjson(text)
        self.assertTrue(verify_bundle(restored))
        mutated = text.replace('"final_disposition"', '"final_dispositionX"', 1)
        self.assertFalse(verify_bundle(from_ndjson(mutated)))

    def test_OB04_reviewer_export_is_a_newly_sealed_derived_view(self):
        source = self.evidence.bundle(self.run)
        derived = reviewer_export(self.evidence, self.run)
        manifest = derived["manifest"]
        self.assertTrue(verify_bundle(derived))
        self.assertNotEqual(derived["manifest_hash"], source["manifest_hash"])
        self.assertEqual(manifest["derived_from"]["source_manifest_hash"], source["manifest_hash"])
        self.assertFalse(manifest["transformation"]["events_modified"])
        self.assertEqual(derived["events"], source["events"])  # ledger verbatim
        self.assertTrue(manifest["fixture_oracle"]["available"])
        self.assertNotIn("fixture_oracle", manifest["omitted_from_standard_export"])
        self.assertNotIn("fixture_oracle", source["manifest"])
        self.assertNotIn("answer", json.dumps(manifest["fixture_oracle"]).lower().replace("answers", ""))
        # Provenance is checked: a different source manifest or edited transformation fails.
        wrong = copy.deepcopy(derived)
        wrong["source_manifest"]["state"] = "other"
        self.assertFalse(verify_bundle(wrong))
        lie = copy.deepcopy(derived)
        lie["manifest"]["transformation"]["events_modified"] = True
        lie["manifest_hash"] = digest(lie["manifest"])
        self.assertFalse(verify_bundle(lie))

    def test_OB04_provider_coverage_section_still_required(self):
        bundle = self.evidence.bundle(self.run)
        bundle["manifest"].pop("provider_coverage")
        bundle["manifest_hash"] = digest(bundle["manifest"])
        self.assertFalse(verify_bundle(bundle))

    def test_OB04_derived_view_ndjson_verifies(self):
        self.assertTrue(
            verify_bundle(from_ndjson(to_ndjson(reviewer_export(self.evidence, self.run))))
        )

    def test_hold_record_contract_accepts_notice_event_id(self):
        view = self.evidence.hold(self.run, "r", "o", self.evidence.clock() + 5 * DAY, "k")
        self.evidence.purge(self.run)
        hold = self.evidence.bundle(self.run)["manifest"]["holds"][0]
        HoldRecord.model_validate(hold)
        self.assertEqual(view["hold_id"], hold["hold_id"])
        self.assertTrue(hold["expiry_notice_event_id"])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        database = Database(
            os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "t.sqlite"))
        )
        self.app = create_app(database, root / "objects", token="t" * 32, recover=False)
        self.headers = {"Authorization": "Bearer " + "t" * 32}
        self.database = database
        self.evidence = self.app.state.evidence
        engine = self.app.state.engine
        self.run = engine.create(RunConfig().model_dump())
        run_scenario(engine, self.run, self.evidence)

    def test_AP07_authenticated_download_links(self):
        with TestClient(self.app) as client:
            url = f"/api/v1/runs/{self.run}/evidence"
            summary = client.get(url + "?view=summary", headers=self.headers).json()
            self.assertNotIn("events", summary)
            self.assertIn(self.run, summary["download_links"][0]["href"])
            full = client.get(url, headers=self.headers).json()
            self.assertTrue(verify_bundle(full))
            for link in summary["download_links"]:
                self.assertEqual(client.get(link["href"]).status_code, 401)
                response = client.get(link["href"], headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response.headers["content-disposition"])
                self.assertEqual(response.headers["content-type"].split(";")[0], link["media_type"])
                bundle = (
                    from_ndjson(response.text) if link["format"] == "ndjson" else response.json()
                )
                self.assertTrue(verify_bundle(bundle))
                self.assertEqual(
                    response.headers["x-manifest-hash"], bundle["manifest_hash"]
                )
            self.assertEqual(
                client.get(url + "/download?format=xml", headers=self.headers).status_code, 422
            )

    def test_HD04_unresolved_hold_listing_and_expiry_notice_event(self):
        now = self.evidence.clock()
        near = self.evidence.hold(self.run, "disputed", "reviewer", now + 3 * DAY, "n")
        far = self.evidence.hold(self.run, "other", "reviewer", now + 60 * DAY, "f")
        released = self.evidence.hold(self.run, "done", "reviewer", now + 60 * DAY, "r")
        self.evidence.release(self.run, released["hold_id"], "resolved")
        self.evidence.purge(self.run)  # emits the notice as a ledger event
        with TestClient(self.app) as client:
            listing = client.get("/api/v1/preservation-holds", headers=self.headers)
            self.assertEqual(listing.status_code, 200)
            items = {h["hold_id"]: h for h in listing.json()["items"]}
            self.assertEqual(set(items), {near["hold_id"], far["hold_id"]})
            self.assertTrue(items[near["hold_id"]]["expiry_notice_due"])
            self.assertFalse(items[far["hold_id"]]["expiry_notice_due"])
            notice = items[near["hold_id"]]["expiry_notice_event_id"]
            event = next(e for e in self.database.events(self.run) if e["event_id"] == notice)
            self.assertEqual(event["type"], "preservation.expiry_notice")
            self.assertEqual(items[near["hold_id"]]["dispute"]["reason"], "disputed")
            page = client.get("/api/v1/preservation-holds?limit=1", headers=self.headers).json()
            self.assertEqual(len(page["items"]), 1)
            self.assertIsNotNone(page["next_cursor"])
            self.assertEqual(
                client.get("/api/v1/preservation-holds").status_code, 401
            )
            per_run = client.get(
                f"/api/v1/runs/{self.run}/preservation-holds", headers=self.headers
            ).json()
            self.assertEqual(len(per_run["items"]), 3)


if __name__ == "__main__":
    unittest.main()
