import json
import math
import sys
import tempfile
import unittest
from pathlib import Path as _P
from unittest import mock

sys.path.insert(0, str(_P(__file__).parent))
from datetime import UTC, datetime, timedelta
from pathlib import Path

from voice_harness import resample as rs
from voice_harness.adapters import AdapterEventAdapter, LiveAdapter, ToolCall
from voice_harness.coverage import build_registry, summarize
from voice_harness.mock_provider import MockProvider

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "artifacts" / "phase0"


def tone(n, rate, hz=440, amp=20000):
    import array

    a = array.array("h", (int(amp * math.sin(2 * math.pi * hz * i / rate)) for i in range(n)))
    return a.tobytes()


def samples(b):
    import array

    a = array.array("h")
    a.frombytes(b)
    return a


class ResampleTests(unittest.TestCase):
    def test_lengths(self):
        pcm = tone(1600, 16000)  # 100 ms
        self.assertEqual(len(rs.resample(pcm, 16000, 24000)), 2400 * 2)
        self.assertEqual(len(rs.resample(tone(2400, 24000), 24000, 16000)), 1600 * 2)

    def test_identity_and_empty(self):
        pcm = tone(100, 24000)
        self.assertEqual(rs.resample(pcm, 24000, 24000), pcm)
        self.assertEqual(rs.resample(b"", 16000, 24000), b"")

    def test_deterministic(self):
        pcm = tone(800, 16000)
        self.assertEqual(rs.resample(pcm, 16000, 24000), rs.resample(pcm, 16000, 24000))

    def test_round_trip_tolerance(self):
        pcm = tone(1600, 16000, hz=300)
        back = rs.resample(rs.resample(pcm, 16000, 24000), 24000, 16000)
        a, b = samples(pcm), samples(back)
        self.assertEqual(len(a), len(b))
        err = max(abs(x - y) for x, y in zip(a[:-2], b[:-2], strict=True))
        self.assertLess(err / 32768, 0.05)

    def test_no_clipping_at_full_scale(self):
        import array

        pcm = array.array("h", [32767, -32768] * 400).tobytes()
        out = samples(rs.resample(pcm, 16000, 24000))
        self.assertTrue(all(-32768 <= v <= 32767 for v in out))
        dc = array.array("h", [32767] * 100).tobytes()
        self.assertEqual(set(samples(rs.resample(dc, 16000, 24000))), {32767})

    def test_odd_length_rejected(self):
        with self.assertRaises(ValueError):
            rs.resample(b"\x00\x00\x00", 16000, 24000)

    def test_chunking_20ms_at_24k(self):
        chunks = rs.chunk(bytes(24000 * 2))  # 1 s
        self.assertEqual(len(chunks), 50)
        self.assertTrue(all(len(c) == 960 for c in chunks))


class AdapterContractTests(unittest.TestCase):
    def test_mock_has_adapter_operations(self):
        m = MockProvider()
        for op in ("capabilities", "open_session", "send_audio", "send_text",
                   "submit_tool_result", "cancel_tool", "close"):
            self.assertTrue(callable(getattr(m, op)))

    def test_events_closed(self):
        ev = AdapterEventAdapter.validate_python(
            {"type": "tool_call", "call_id": "c", "name": "n", "arguments": {}}
        )
        self.assertIsInstance(ev, ToolCall)
        with self.assertRaises(ValueError):
            AdapterEventAdapter.validate_python({"type": "tool_call", "call_id": "c",
                                                 "name": "n", "arguments": {}, "x": 1})
        with self.assertRaises(ValueError):
            AdapterEventAdapter.validate_python({"type": "nope"})


class CoverageTests(unittest.TestCase):
    NOW = datetime(2026, 10, 17, tzinfo=UTC)

    def reg(self, ac09=None, now=None, present=lambda f: f in ("openai", "aws_nova_sonic")):
        with tempfile.TemporaryDirectory() as d:
            for k, v in (ac09 or {}).items():
                (Path(d) / f"{k}-ac09.json").write_text(json.dumps(v))
            return {c.provider_family: c for c in build_registry(
                REPORTS, d, now or self.NOW, present)}

    def test_no_ac09_is_unverified_and_uncounted(self):
        r = self.reg()
        for f in ("openai", "aws_nova_sonic"):
            self.assertEqual(r[f].coverage_state, "implemented_but_unverified")
            self.assertIn("AC09_NOT_PASSED", r[f].reason_codes)
        self.assertEqual(r["xai_grok_voice"].coverage_state, "not_implemented")
        self.assertEqual(r["google_gemini_live"].coverage_state, "not_implemented")
        s = summarize(list(r.values()))
        self.assertEqual(s["verified_count"], 0)
        self.assertIsNone(s["ranking"])
        self.assertEqual(len(s["items"]), 4)

    def test_verified_requires_ac09_bound_to_report(self):
        rid = self.reg()["openai"].capability_report_id
        r = self.reg({"openai": {"passed": True, "capability_report_id": rid}})
        self.assertEqual(r["openai"].coverage_state, "implemented_and_verified")
        self.assertEqual(summarize(list(r.values()))["verified_count"], 1)
        stale = self.reg({"openai": {"passed": True, "capability_report_id": "other"}})
        self.assertEqual(stale["openai"].coverage_state, "implemented_but_unverified")
        failing = self.reg({"openai": {"passed": True, "capability_report_id": rid,
                                       "failures": ["x"]}})
        self.assertEqual(failing["openai"].coverage_state, "implemented_but_unverified")

    def test_expired_report_downgrades(self):
        rid = self.reg()["openai"].capability_report_id
        r = self.reg({"openai": {"passed": True, "capability_report_id": rid}},
                     now=self.NOW + timedelta(days=30))
        self.assertEqual(r["openai"].coverage_state, "implemented_but_unverified")
        self.assertIn("REPORT_EXPIRED", r["openai"].reason_codes)

    def test_failed_closed_report_downgrades(self):
        with tempfile.TemporaryDirectory() as d:
            from voice_harness.capability_report import unknown_report

            rep = unknown_report("cap_x", "openai", "m", self.NOW)
            (Path(d) / "openai-capability-report.json").write_text(rep.model_dump_json())
            (Path(d) / "openai-ac09.json").write_text(
                json.dumps({"passed": True, "capability_report_id": "cap_x"}))
            c = {x.provider_family: x for x in build_registry(d, d, self.NOW, lambda f: True)}
            self.assertEqual(c["openai"].coverage_state, "implemented_but_unverified")
            self.assertIn("REPORT_FAILED_CLOSED", c["openai"].reason_codes)

    def test_no_adapter_is_not_implemented_even_with_report(self):
        r = self.reg(present=lambda f: False)
        self.assertTrue(all(c.coverage_state == "not_implemented" for c in r.values()))
        self.assertIn("REPORT_WITHOUT_ADAPTER", r["openai"].reason_codes)

    def test_api_endpoints(self):
        from fastapi.testclient import TestClient

        from voice_harness.api import create_app

        with tempfile.TemporaryDirectory() as d, mock.patch(
            "voice_harness.coverage.DEFAULT_AC09_DIR", Path(d)
        ):  # empty AC-09 dir: no verified providers
            app = create_app(root=d, token="t", recover=False)
            c = TestClient(app)
            h = {"Authorization": "Bearer t"}
            for path in ("/api/v1/providers", "/api/v1/providers/export"):
                body = c.get(path, headers=h).json()
                self.assertEqual(len(body["items"]), 4)
                self.assertEqual(body["verified_count"], 0)
                self.assertIsNone(body["ranking"])


if __name__ == "__main__":
    unittest.main()


# ---- AC-09 harness proven against MockProvider ----
import asyncio  # noqa: E402

from adapter_conformance import LiveConformance, run_conformance, write_result  # noqa: E402,F401

from voice_harness.adapters import AudioOut, CapabilityUnsupported  # noqa: E402


class MockAdapter:
    """MockProvider wrapped to the LiveAdapter shape (scripted events)."""

    def __init__(self, interrupt_supported=True, drop_tool_check=False):
        self.m = MockProvider()
        self.q = asyncio.Queue()
        self.sup = interrupt_supported
        self.lax = drop_tool_check
        self.known = set()

    def capabilities(self):
        return {**self.m.capabilities(), "interrupt_truncate": "supported" if self.sup else "unsupported"}

    async def open_session(self, c):
        return await self.m.open_session(c)

    async def send_audio(self, d, md):
        return await self.m.send_audio(d, md)

    async def send_text(self, t):
        return await self.m.send_text(t)

    async def submit_tool_result(self, cid, r):
        if not self.lax and cid not in self.known:
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        return await self.m.submit_tool_result(cid, {**r, "call_id": cid})

    async def cancel_tool(self, cid, reason):
        return await self.m.cancel_tool(cid, reason)

    async def interrupt(self, item_id, audio_end_ms):
        if not self.sup:
            raise CapabilityUnsupported("interrupt_truncate")
        return await self.m.interrupt(item_id, audio_end_ms, "barge_in")

    async def receive(self):
        while not self.q.empty():
            yield self.q.get_nowait()

    async def close(self, reason):
        await self.m.close(reason)

    async def prime(self, dim):
        if dim == "audio_out":
            self.q.put_nowait(AudioOut(pcm=bytes(960), sample_count=480))
        elif dim == "tool_result_correlation":
            self.known.add("call-1")
            self.q.put_nowait(ToolCall(call_id="call-1", name="get_balance", arguments={}))


class ConformanceHarnessTests(unittest.TestCase):
    def run_it(self, **kw):
        a = MockAdapter(**kw)
        self.assertIsInstance(a, LiveAdapter)
        return asyncio.run(run_conformance(a, prime=a.prime, timeout=1))

    def test_mock_passes(self):
        r = self.run_it()
        self.assertTrue(r["passed"], r)
        self.assertEqual(r["excluded"], {})

    def test_unsupported_interrupt_is_excluded_not_passed(self):
        r = self.run_it(interrupt_supported=False)
        self.assertEqual(r["dimensions"]["interruption"]["status"], "excluded")
        self.assertIn("interruption", r["excluded"])
        self.assertTrue(r["passed"])

    def test_missing_correlation_check_fails(self):
        r = self.run_it(drop_tool_check=True)
        self.assertFalse(r["passed"])
        self.assertEqual(r["dimensions"]["tool_result_correlation"]["status"], "fail")

    def test_no_events_fails(self):
        a = MockAdapter()
        r = asyncio.run(run_conformance(a, timeout=0.2))
        self.assertFalse(r["passed"])

    def test_ac09_result_drives_coverage(self):
        rid = CoverageTests().reg()["openai"].capability_report_id
        r = self.run_it()
        with tempfile.TemporaryDirectory() as d:
            write_result(r, "openai", rid, d)
            c = {x.provider_family: x for x in build_registry(
                REPORTS, d, CoverageTests.NOW, lambda f: f == "openai")}
            self.assertEqual(c["openai"].coverage_state, "implemented_and_verified")
