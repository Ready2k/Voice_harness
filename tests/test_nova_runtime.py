"""Nova bridge runtime: offline container checks (no network, no credentials) and coverage honesty."""
import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from voice_harness import coverage
from voice_harness.adapters import nova_sonic

REPORTS = Path(__file__).resolve().parents[1] / "artifacts" / "phase0"
REQUIRED = os.environ.get("HARNESS_REQUIRE_NOVA_RUNTIME") == "1"
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")} | {
    "AWS_EC2_METADATA_DISABLED": "true", "AWS_CONFIG_FILE": os.devnull,
    "AWS_SHARED_CREDENTIALS_FILE": os.devnull}


class BridgeRuntime(unittest.TestCase):
    def setUp(self):
        if not nova_sonic.runtime_available():
            if REQUIRED:
                self.fail(f"Nova runtime required in this image: {nova_sonic.runtime_status()}")
            self.skipTest("node/bridge deps not available on this host")

    def test_bridge_speaks_json_lines_and_fails_cleanly_without_credentials(self):
        open_msg = json.dumps({"_open": {"region": "us-east-1", "modelId": nova_sonic.MODEL_ID}}) + "\n"
        p = subprocess.run([nova_sonic.NODE_BIN, str(nova_sonic.BRIDGE_SCRIPT)], input=open_msg, text=True,
                           capture_output=True, timeout=30, env=CLEAN_ENV, cwd=tempfile.gettempdir())
        lines = [json.loads(x) for x in p.stdout.splitlines()]
        self.assertTrue(lines, p.stderr)
        self.assertIn("_error", lines[0])
        self.assertTrue(lines[0]["_error"]["name"])
        self.assertEqual(lines[-1], {"_closed": True})
        self.assertEqual(p.returncode, 0)

    def test_adapter_transport_reports_provider_error_without_credentials(self):
        async def run():
            a = nova_sonic.NovaSonicAdapter(pump_interval_s=0.05)
            await a.open_session({"env": CLEAN_ENV})
            ev = []
            async for e in a.receive():
                ev.append(e)
                if getattr(e, "fatal", False):
                    break
            await a.close("test")
            return ev
        ev = asyncio.run(asyncio.wait_for(run(), 40))
        self.assertTrue(any(getattr(e, "fatal", False) for e in ev), ev)

    def test_image_has_no_baked_secrets(self):
        if not REQUIRED:
            self.skipTest("container-only")
        root = Path(nova_sonic.BRIDGE_SCRIPT).parents[2]
        self.assertFalse((root / ".local").exists())


class RuntimeHonesty(unittest.TestCase):
    NOW = datetime(2026, 10, 17, tzinfo=UTC)

    def test_status_reports_missing_pieces(self):
        with mock.patch.object(nova_sonic, "NODE_BIN", "definitely-not-node"):
            self.assertIn("NODE_MISSING", nova_sonic.runtime_status())
        with mock.patch.object(nova_sonic, "BRIDGE_SCRIPT", Path("/nonexistent/nova_bridge.mjs")):
            self.assertIn("BRIDGE_MISSING", nova_sonic.runtime_status())
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "nova_bridge.mjs"
            script.write_text("")
            with mock.patch.object(nova_sonic, "BRIDGE_SCRIPT", script):
                self.assertIn("BRIDGE_DEPS_MISSING", nova_sonic.runtime_status())

    def test_unrunnable_adapter_is_never_verified(self):
        with tempfile.TemporaryDirectory() as d:
            reg = {c.provider_family: c for c in coverage.build_registry(
                REPORTS, d, self.NOW, lambda f: f == "aws_nova_sonic",
                runtime_reasons=lambda f: ["ADAPTER_RUNTIME_UNAVAILABLE"])}
        n = reg["aws_nova_sonic"]
        self.assertEqual(n.coverage_state, "implemented_but_unverified")
        self.assertIn("ADAPTER_RUNTIME_UNAVAILABLE", n.reason_codes)
        self.assertEqual(n.evidence_ids, ())

    def test_runtime_reasons_wiring(self):
        with mock.patch.object(nova_sonic, "runtime_status", lambda: ["NODE_MISSING"]):
            self.assertEqual(coverage._runtime_reasons("aws_nova_sonic"), ["ADAPTER_RUNTIME_UNAVAILABLE"])
        with mock.patch.object(nova_sonic, "runtime_status", lambda: []):
            self.assertEqual(coverage._runtime_reasons("aws_nova_sonic"), [])
