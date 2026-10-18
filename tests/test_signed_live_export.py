"""Signed live exports: offline keyring verification and the endpoint/region/SDK provenance in the manifest."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_phase4_scenarios import ScriptedAdapter, tone

from voice_harness.asr import ScriptedAsr
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine, digest
from voice_harness.evidence import Evidence, verify_bundle
from voice_harness.live_assessment import IndependentAssessor
from voice_harness.live_providers import connection_binding, session_metadata
from voice_harness.live_scenario import LiveScenarioRunner
from voice_harness.signing import KeyStore, load_keyring
from voice_harness.storage import Database

SECRET_URL = "wss://user:hunter2@api.example.com:8443/v1/realtime?api_key=sk-SECRET&model=x"


class Adapter(ScriptedAdapter):
    def connection_info(self):
        return {"endpoint_host": "api.example.com", "region": "us-east-1",
                "sdk_versions": {"websockets": "15.0", "@aws-sdk/client-bedrock-runtime": "3.9.9"}}


class SignedLive(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = KeyStore(root)
        self.engine = Engine(Database(str(root / "t.sqlite")))
        self.evidence = Evidence(self.engine.database, root / "objects", signer=self.store.signer())
        self.run = self.engine.create(RunConfig(provider_config_id="openai-realtime-live").model_dump())
        adapter = Adapter()
        adapter.native_sink = None
        metadata = session_metadata("openai-realtime-live", "gpt-realtime-2.1", {"model_id": "gpt-realtime-2.1"},
                                    connection_binding(adapter))
        assessor = IndependentAssessor(self.engine, ScriptedAsr(queue=["Please verify your identity."] * 2))
        runner = LiveScenarioRunner(
            self.engine, self.run, adapter, assessor, "identity-impatient-en-gb-clean-001",
            caller_audio=lambda text: tone(200), turn_timeout_s=10, provider_session=metadata)
        asyncio.run(runner.run())
        with self.engine.database.transaction(self.run) as tx:
            tx.state["state"] = "completed"
        self.bundle = self.evidence.bundle(self.run)
        self.ring = self.store.public_keys()

    def test_live_bundle_is_signed_and_verifies_offline_with_keyring(self):
        self.assertEqual(self.bundle["manifest"]["signature_status"], "signed_ed25519")
        self.assertTrue(any(e["type"] == "run.live_binding" for e in self.bundle["events"]))
        wire = json.loads(json.dumps(self.bundle))
        self.assertTrue(verify_bundle(wire, self.ring))
        self.assertTrue(verify_bundle(wire, load_keyring(json.dumps(self.ring))))
        self.assertFalse(verify_bundle(wire, allow_unsigned=False))  # signed bundle with no trusted key

    def test_tampered_live_bundle_fails(self):
        for edit in (
            lambda b: b["manifest"]["provider"].update(region="eu-west-1"),
            lambda b: b["manifest"]["provider"].update(endpoint="evil.example.com"),
            lambda b: b["events"][3]["content"].update(x=1),
        ):
            bundle = copy.deepcopy(self.bundle)
            edit(bundle)
            self.assertFalse(verify_bundle(bundle, self.ring))
        # Provider section rewritten and the manifest hash recomputed: only the signature catches it.
        bundle = copy.deepcopy(self.bundle)
        bundle["manifest"]["provider"]["sdk_versions"] = {"websockets": "1.0"}
        bundle["manifest_hash"] = digest(bundle["manifest"])
        self.assertFalse(verify_bundle(bundle, self.ring))

    def test_wrong_or_revoked_key_fails(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        wrong = KeyStore(Path(other.name))
        wrong.signer()
        self.assertFalse(verify_bundle(self.bundle, wrong.public_keys()))
        self.assertFalse(verify_bundle(self.bundle, self.ring, revoked=[self.bundle["signature"]["key_id"]]))

    def test_manifest_provider_records_endpoint_region_and_sdk_versions_from_ledger(self):
        provider = self.bundle["manifest"]["provider"]
        self.assertEqual((provider["endpoint"], provider["region"]), ("api.example.com", "us-east-1"))
        self.assertEqual(provider["sdk_versions"]["websockets"], "15.0")
        self.assertTrue(provider["connection_recorded"])
        binding = next(e["content"] for e in self.bundle["events"] if e["type"] == "run.live_binding")
        self.assertEqual(binding["provider"]["endpoint_host"], provider["endpoint"])


    def test_cli_verifies_live_bundle_offline_and_rejects_tamper_and_wrong_key(self):
        import os
        import subprocess
        import sys

        def verify(bundle, ring):
            folder = Path(tempfile.mkdtemp())
            self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
            (folder / "b.json").write_text(json.dumps(bundle))
            (folder / "k.json").write_text(json.dumps(ring))
            src = str(Path(__file__).resolve().parents[1] / "src")
            return subprocess.run(
                [sys.executable, "-m", "voice_harness", "verify-bundle", str(folder / "b.json"), "--public-key", str(folder / "k.json")],
                capture_output=True, text=True, env={**os.environ, "PYTHONPATH": src}, check=False).returncode

        self.assertEqual(verify(self.bundle, self.ring), 0)
        tampered = copy.deepcopy(self.bundle)
        tampered["manifest"]["provider"]["region"] = "eu-west-1"
        self.assertNotEqual(verify(tampered, self.ring), 0)
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        wrong = KeyStore(Path(other.name))
        wrong.signer()
        self.assertNotEqual(verify(self.bundle, wrong.public_keys()), 0)


class Sanitising(unittest.TestCase):
    def test_openai_adapter_reports_host_only(self):
        from voice_harness.adapters.openai_realtime import OpenAIRealtimeAdapter

        info = connection_binding(OpenAIRealtimeAdapter(api_key="sk-SECRET", url=SECRET_URL))
        self.assertEqual(info["endpoint_host"], "api.example.com")
        text = json.dumps(info)
        for leaked in ("hunter2", "sk-SECRET", "api_key", "8443", "v1/realtime"):
            self.assertNotIn(leaked, text)

    def test_nova_adapter_reports_region_and_derived_host(self):
        from voice_harness.adapters.nova_sonic import NovaSonicAdapter

        info = connection_binding(NovaSonicAdapter())
        self.assertEqual(info["region"], "us-east-1")
        self.assertEqual(info["endpoint_host"], "bedrock-runtime.us-east-1.amazonaws.com")
        self.assertIn("@aws-sdk/client-bedrock-runtime", info["sdk_versions"])

    def test_untrusted_values_are_dropped(self):
        class Bad:
            def connection_info(self):
                return {"endpoint_host": "a.com/path?token=1", "region": "x y", "sdk_versions": {}}

        self.assertIsNone(connection_binding(Bad())["endpoint_host"])
        self.assertIsNone(connection_binding(Bad())["region"])

    def test_adapter_without_connection_info_yields_unknowns(self):
        self.assertEqual(connection_binding(object()), {"endpoint_host": None, "region": None, "sdk_versions": {}})


if __name__ == "__main__":
    unittest.main()
