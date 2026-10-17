"""AC-08 signature gate: signed manifests, key lifecycle, offline verification."""

import base64
import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine, digest
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.exports import from_ndjson, reviewer_export, to_ndjson
from voice_harness.scenarios import run_scenario
from voice_harness.signing import KeyStore, SigningError, check_bundle_signature
from voice_harness.storage import Database

PYTHON = sys.executable


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = Database(
            os.environ.get("HARNESS_TEST_DATABASE_URL", str(self.root / "t.sqlite"))
        )
        self.store = KeyStore(self.root)
        self.engine = Engine(self.database)
        self.evidence = Evidence(
            self.database, self.root / "objects", signer=self.store.signer()
        )
        self.run = self.engine.create(RunConfig().model_dump())
        run_scenario(self.engine, self.run, self.evidence)
        with self.database.transaction(self.run) as tx:
            tx.state["state"] = "completed"
        self.bundle = self.evidence.bundle(self.run)
        self.ring = self.store.public_keys()


class SigningTests(Base):
    def test_signed_bundle_verifies_offline_against_trusted_key(self):
        self.assertEqual(self.bundle["manifest"]["signature_status"], "signed_ed25519")
        envelope = self.bundle["signature"]
        self.assertEqual(envelope["algorithm"], "Ed25519")
        self.assertEqual(envelope["key_id"], self.store.active_key_id())
        self.assertTrue(verify_bundle(self.bundle, self.ring))
        self.assertTrue(verify_bundle(json.loads(json.dumps(self.bundle)), self.ring))

    def test_missing_signature_fails_even_with_allow_unsigned(self):
        stripped = {k: v for k, v in self.bundle.items() if k != "signature"}
        self.assertEqual(check_bundle_signature(stripped, self.ring), "SIGNATURE_MISSING")
        self.assertFalse(verify_bundle(stripped, self.ring, allow_unsigned=True))

    def test_altered_manifest_fails(self):
        for edit in (
            lambda m: m.update(state="failed"),
            lambda m: m["signature"].update(key_id="ed25519-" + "0" * 32),
        ):
            bundle = copy.deepcopy(self.bundle)
            edit(bundle["manifest"])
            self.assertFalse(verify_bundle(bundle, self.ring))
        # Even with manifest_hash recomputed by an attacker, the signature fails.
        bundle = copy.deepcopy(self.bundle)
        bundle["manifest"]["known_limitations"].append("forged")
        bundle["manifest_hash"] = digest(bundle["manifest"])
        self.assertEqual(check_bundle_signature(bundle, self.ring), "SIGNATURE_INVALID")

    def test_event_tamper_still_fails_integrity(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["events"][0]["content"]["x"] = 1
        self.assertFalse(verify_bundle(bundle, self.ring))

    def test_swapped_or_corrupt_signature_fails(self):
        other = self.engine.create(RunConfig().model_dump())
        run_scenario(self.engine, other, self.evidence)
        swapped = copy.deepcopy(self.bundle)
        swapped["signature"] = self.evidence.bundle(other)["signature"]
        self.assertFalse(verify_bundle(swapped, self.ring))
        bad = copy.deepcopy(self.bundle)
        raw = bytearray(base64.b64decode(bad["signature"]["value"]))
        raw[0] ^= 1
        bad["signature"]["value"] = base64.b64encode(bytes(raw)).decode()
        self.assertEqual(check_bundle_signature(bad, self.ring), "SIGNATURE_INVALID")

    def test_unknown_key_and_no_trusted_key_fail(self):
        other_ring = KeyStore(self.root / "other").public_keys()
        self.assertEqual(check_bundle_signature(self.bundle, other_ring), "KEY_UNKNOWN")
        self.assertEqual(check_bundle_signature(self.bundle, None), "NO_TRUSTED_KEY")

    def test_algorithm_confusion_rejected(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["signature"]["algorithm"] = "none"
        self.assertEqual(
            check_bundle_signature(bundle, self.ring), "SIGNATURE_ALGORITHM_UNSUPPORTED"
        )

    def test_unsigned_policy(self):
        bundle = Evidence(self.database, self.root / "o2").bundle(self.run)
        self.assertEqual(bundle["manifest"]["signature_status"], "unsigned_local")
        self.assertTrue(verify_bundle(bundle))  # legacy integrity-only
        self.assertFalse(verify_bundle(bundle, self.ring))  # strict by default
        self.assertFalse(verify_bundle(bundle, allow_unsigned=False))
        self.assertTrue(verify_bundle(bundle, allow_unsigned=True))
        # Claiming to be unsigned does not excuse an attached wrong signature.
        bundle["signature"] = self.bundle["signature"]
        self.assertFalse(verify_bundle(bundle, self.ring, allow_unsigned=True))

    def test_provider_coverage_and_tombstones_are_covered(self):
        bundle = copy.deepcopy(self.bundle)
        bundle["manifest"]["provider_coverage"]["statement"] = "x"
        self.assertFalse(verify_bundle(bundle, self.ring))
        self.assertIn("provider_coverage", self.bundle["manifest"])
        self.assertIn("metadata_tombstones", self.bundle["manifest"]["retention"])

    def test_ndjson_round_trip_keeps_signature(self):
        self.assertTrue(verify_bundle(from_ndjson(to_ndjson(self.bundle)), self.ring))

    def test_reviewer_derived_view_signed_and_linked(self):
        view = reviewer_export(self.evidence, self.run)
        self.assertTrue(verify_bundle(view, self.ring))
        self.assertNotEqual(view["signature"]["value"], view["source_signature"]["value"])
        self.assertEqual(
            view["manifest"]["derived_from"]["source_manifest_hash"],
            view["source_manifest"] and digest(view["source_manifest"]),
        )
        self.assertTrue(verify_bundle(from_ndjson(to_ndjson(view)), self.ring))
        broken = copy.deepcopy(view)
        del broken["source_signature"]
        self.assertEqual(check_bundle_signature(broken, self.ring), "SOURCE_SIGNATURE_MISSING")
        forged = copy.deepcopy(view)
        forged["source_manifest"]["state"] = "failed"
        self.assertFalse(verify_bundle(forged, self.ring))


class KeyLifecycleTests(Base):
    def test_private_key_mode_0600_and_history_chain(self):
        pem = next(self.store.keys.glob("*.pem"))
        self.assertEqual(stat.S_IMODE(pem.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.history_path.stat().st_mode), 0o600)
        self.assertEqual(self.store.history()[0]["event"], "generated")
        line = self.store.history_path.read_text().splitlines()[0]
        self.store.history_path.write_text(line.replace("initial", "forged") + "\n")
        with self.assertRaises(SigningError):
            self.store.history()

    def test_rotation_old_bundles_still_verify_with_new_ring(self):
        old = self.store.active_key_id()
        new = self.store.rotate("test")
        self.assertNotEqual(old, new)
        self.assertFalse((self.store.keys / (old + ".pem")).exists())
        ring = self.store.public_keys()
        self.assertEqual(ring["active_key_id"], new)
        self.assertTrue(verify_bundle(self.bundle, ring))  # signed by retired key
        self.evidence.signer = self.store.signer()
        fresh = self.evidence.bundle(self.run)
        self.assertEqual(fresh["signature"]["key_id"], new)
        self.assertTrue(verify_bundle(fresh, ring))
        self.assertFalse(verify_bundle(fresh, self.ring))  # old ring lacks new key
        self.assertEqual(KeyStore(self.root).public_keys(), ring)

    def test_revocation_fails_old_bundles_and_survives_reload(self):
        old = self.store.active_key_id()
        self.store.revoke(old, "compromised")
        ring = self.store.public_keys()
        self.assertIn(old, ring["revoked"])
        self.assertNotEqual(ring["active_key_id"], old)
        self.assertEqual(check_bundle_signature(self.bundle, ring), "KEY_REVOKED")
        self.assertEqual(
            check_bundle_signature(self.bundle, self.ring, revoked=[old]), "KEY_REVOKED"
        )
        self.assertEqual(KeyStore(self.root).public_keys()["revoked"], [old])

    def test_tampered_keyring_key_id_rejected(self):
        ring = copy.deepcopy(self.ring)
        ring["keys"][0]["key_id"] = "ed25519-" + "1" * 32
        with self.assertRaises(SigningError):
            check_bundle_signature(self.bundle, ring)


class CliAndApiTests(Base):
    def run_cli(self, *args):
        env = {**os.environ, "PYTHONPATH": "src"}
        return subprocess.run(
            [PYTHON, "-m", "voice_harness", "verify-bundle", *args],
            capture_output=True, text=True, env=env, check=False,
        )

    def test_cli_requires_key_or_allow_unsigned(self):
        path = self.root / "b.json"
        path.write_text(json.dumps(self.bundle))
        keys = self.root / "keys.json"
        keys.write_text(json.dumps(self.ring))
        self.assertEqual(self.run_cli(str(path)).returncode, 2)
        ok = self.run_cli(str(path), "--public-key", str(keys))
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertEqual(json.loads(ok.stdout)["signature"], "ok")
        revoked = self.run_cli(
            str(path), "--public-key", str(keys), "--revoked", self.bundle["signature"]["key_id"]
        )
        self.assertEqual(revoked.returncode, 1)
        self.assertEqual(json.loads(revoked.stdout)["signature"], "KEY_REVOKED")
        raw = self.root / "pub.txt"
        raw.write_text(self.ring["keys"][0]["public_key"])
        self.assertEqual(self.run_cli(str(path), "--public-key", str(raw)).returncode, 0)
        unsigned = self.root / "u.json"
        unsigned.write_text(json.dumps(Evidence(self.database, self.root / "o").bundle(self.run)))
        self.assertEqual(self.run_cli(str(unsigned), "--public-key", str(keys)).returncode, 1)
        self.assertEqual(self.run_cli(str(unsigned), "--allow-unsigned").returncode, 0)

    def test_api_serves_public_keys_and_signed_bundles(self):
        app = create_app(root=self.root / "api", token="op", reviewer_token="rv", recover=False)
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer op"}
            self.assertEqual(client.get("/api/v1/signing/public-keys").status_code, 401)
            ring = client.get("/api/v1/signing/public-keys", headers=headers).json()
            self.assertEqual(len(ring["keys"]), 1)
            run = app.state.engine.create(RunConfig().model_dump())
            run_scenario(app.state.engine, run, app.state.evidence)
            bundle = client.get(f"/api/v1/runs/{run}/evidence", headers=headers).json()
            bundle.pop("download_links")
            self.assertTrue(verify_bundle(bundle, ring))
            reviewer = {"Authorization": "Bearer rv"}
            self.assertEqual(
                client.post("/api/v1/signing/rotate", headers=reviewer).status_code, 403
            )
            rotated = client.post(
                "/api/v1/signing/rotate", headers=headers, json={"reason": "t"}
            ).json()
            self.assertEqual(len(rotated["keys"]), 2)
            self.assertTrue(verify_bundle(bundle, rotated))
            revoked = client.post(
                "/api/v1/signing/revoke",
                headers=headers,
                json={"key_id": ring["keys"][0]["key_id"], "reason": "t"},
            ).json()
            self.assertFalse(verify_bundle(bundle, revoked))
            history = client.get("/api/v1/signing/key-history", headers=headers).json()
            self.assertEqual(
                [h["event"] for h in history["items"]],
                ["generated", "generated", "retired", "revoked"],
            )


if __name__ == "__main__":
    unittest.main()
