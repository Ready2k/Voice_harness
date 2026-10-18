"""AC-20 export: provider coverage is sealed into the evidence manifest."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from voice_harness.contracts import RunConfig
from voice_harness.coverage import FAMILIES, Coverage, summarize
from voice_harness.engine import Engine
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.storage import Database


def reseal(bundle):
    """Recompute the manifest hash so only semantic checks can catch the edit."""
    from voice_harness.engine import digest

    bundle["manifest_hash"] = digest(bundle["manifest"])
    return bundle


class CoverageExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.database = Database(
            os.environ.get("HARNESS_TEST_DATABASE_URL", str(root / "t.sqlite"))
        )
        self.run = Engine(self.database).create(RunConfig().model_dump())
        self.evidence = Evidence(self.database, root / "objects")

    def bundle(self):
        return self.evidence.bundle(self.run)

    def test_export_contains_all_families_unranked_and_verifies(self):
        bundle = self.bundle()
        self.assertTrue(verify_bundle(bundle))
        cov = bundle["manifest"]["provider_coverage"]
        self.assertEqual([i["provider_family"] for i in cov["items"]], list(FAMILIES))
        self.assertIsNone(cov["ranking"])
        text = json.dumps(cov).lower()
        for word in ("score", "rank\"", "rating"):
            self.assertNotIn(word, text)

    def test_unverified_families_never_counted_as_supported(self):
        cov = self.bundle()["manifest"]["provider_coverage"]
        verified = [i for i in cov["items"] if i["coverage_state"] == "implemented_and_verified"]
        self.assertEqual(cov["verified_count"], len(verified))
        for item in cov["items"]:
            if item["coverage_state"] != "implemented_and_verified":
                self.assertTrue(item["reason_codes"])
        google = next(i for i in cov["items"] if i["provider_family"] == "google_gemini_live")
        self.assertEqual(google["coverage_state"], "not_implemented")
        xai = next(i for i in cov["items"] if i["provider_family"] == "xai_grok_voice")
        self.assertIn(xai["coverage_state"], ("implemented_but_unverified", "implemented_and_verified"))

    def test_injected_unverified_snapshot_is_not_supported(self):
        items = [Coverage(f, None, None, None, "not_implemented", reason_codes=("NO_ADAPTER",)) for f in FAMILIES]
        self.evidence.coverage = lambda: summarize(items)
        bundle = self.bundle()
        self.assertTrue(verify_bundle(bundle))
        self.assertEqual(bundle["manifest"]["provider_coverage"]["verified_count"], 0)

    def test_hash_detects_any_edit_to_coverage(self):
        original = self.bundle()
        edits = {
            "flip_state": lambda c: c["items"][3].update(coverage_state="implemented_and_verified"),
            "add_ranking": lambda c: c.update(ranking=["openai"]),
            "add_score": lambda c: c["items"][0].update(score=0.9),
            "drop_family": lambda c: c["items"].pop(),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                altered = copy.deepcopy(original)
                edit(altered["manifest"]["provider_coverage"])
                self.assertFalse(verify_bundle(altered))

    def test_resealed_semantic_tampering_is_rejected(self):
        original = self.bundle()
        edits = {
            "flip_state_keep_reasons": lambda c: c["items"][3].update(coverage_state="implemented_and_verified"),
            "verified_without_report": lambda c: c["items"][3].update(
                coverage_state="implemented_and_verified", reason_codes=[]),
            "inflate_count": lambda c: c.update(verified_count=4),
            "forged_statement": lambda c: c.update(statement="verified for 4 of 4 requested provider families"),
            "add_ranking": lambda c: c.update(ranking=["openai"]),
            "add_score": lambda c: c["items"][0].update(score=0.9),
            "extra_key": lambda c: c.update(rank=1),
            "drop_family": lambda c: c["items"].pop(),
            "reorder": lambda c: c["items"].reverse(),
            "clear_reasons": lambda c: c["items"][3].update(reason_codes=[]),
            "bad_state": lambda c: c["items"][0].update(coverage_state="supported"),
        }
        for name, edit in edits.items():
            with self.subTest(name):
                altered = copy.deepcopy(original)
                edit(altered["manifest"]["provider_coverage"])
                self.assertFalse(verify_bundle(reseal(altered)))

    def test_legacy_bundle_without_coverage_fails(self):
        legacy = copy.deepcopy(self.bundle())
        del legacy["manifest"]["provider_coverage"]
        self.assertFalse(verify_bundle(reseal(legacy)))
        self.assertFalse(verify_bundle(legacy))


if __name__ == "__main__":
    unittest.main()
