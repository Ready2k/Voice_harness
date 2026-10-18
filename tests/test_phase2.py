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
        # full-scale DC: no sign wraparound; the filter's edge transient is bounded, interior settles
        dc = array.array("h", [32767] * 400).tobytes()
        out = samples(rs.resample(dc, 16000, 24000))
        self.assertTrue(all(v > 0 for v in out))
        self.assertTrue(all(abs(v - 32767) < 330 for v in out[100:-100]))

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

    def reg(self, ac09=None, now=None, present=lambda f: f in ("openai", "aws_nova_sonic", "xai_grok_voice")):
        with tempfile.TemporaryDirectory() as d:
            for k, v in (ac09 or {}).items():
                (Path(d) / f"{k}-ac09.json").write_text(json.dumps(v))
            return {c.provider_family: c for c in build_registry(
                REPORTS, d, now or self.NOW, present)}

    def test_no_ac09_is_unverified_and_uncounted(self):
        r = self.reg()
        for f in ("openai", "aws_nova_sonic", "xai_grok_voice"):
            self.assertEqual(r[f].coverage_state, "implemented_but_unverified")
            self.assertIn("AC09_NOT_PASSED", r[f].reason_codes)
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

    def test_xai_verified_only_with_bound_ac09(self):
        r = self.reg()
        rid = r["xai_grok_voice"].capability_report_id
        self.assertEqual(r["xai_grok_voice"].model_id, "grok-voice-3.0")
        self.assertTrue(rid.startswith("cap_xai_grok_voice_3_0"))
        ok = self.reg({"xai_grok_voice": {"passed": True, "capability_report_id": rid}})
        self.assertEqual(ok["xai_grok_voice"].coverage_state, "implemented_and_verified")
        for ac09 in ({"passed": True, "capability_report_id": "other"},
                     {"passed": True, "capability_report_id": rid, "failures": ["x"]},
                     {"passed": False, "capability_report_id": rid}):
            self.assertEqual(self.reg({"xai_grok_voice": ac09})["xai_grok_voice"].coverage_state,
                             "implemented_but_unverified")
        expired = self.reg({"xai_grok_voice": {"passed": True, "capability_report_id": rid}},
                           now=self.NOW + timedelta(days=30))
        self.assertIn("REPORT_EXPIRED", expired["xai_grok_voice"].reason_codes)
        self.assertEqual(self.reg(present=lambda f: f in ("openai",))["xai_grok_voice"].coverage_state,
                         "not_implemented")

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

from voice_harness.adapters import AudioOut, CapabilityUnsupported, Interrupted  # noqa: E402


class MockAdapter:
    """MockProvider wrapped to the LiveAdapter shape (scripted events)."""

    def __init__(self, interrupt_supported=True, drop_tool_check=False, barge=True, late_audio=False, double=False,
                 barge_item=None):
        self.barge, self.late_audio, self.double, self.barge_item = barge, late_audio, double, barge_item
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
        elif dim == "interruption" and self.sup and self.barge:
            for _ in range(3):
                self.q.put_nowait(AudioOut(pcm=bytes(960), sample_count=480, item_id="it1", ts_ms=1000))
            self.q.put_nowait(Interrupted(item_id=self.barge_item, audio_end_ms=60, ts_ms=1100))
            if self.late_audio:
                self.q.put_nowait(AudioOut(pcm=bytes(960), sample_count=480, item_id="it1", ts_ms=1100 + 5000))
            if self.double:
                self.q.put_nowait(Interrupted(item_id=self.barge_item, audio_end_ms=60, ts_ms=1110))
            return {"barge_in": "scripted"}


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

    def test_interruption_requires_interrupted_event(self):
        r = self.run_it(barge=False)
        self.assertEqual(r["dimensions"]["interruption"]["status"], "fail")
        self.assertIn("no Interrupted", r["dimensions"]["interruption"]["reason"])
        self.assertFalse(r["passed"])

    def test_interruption_fails_if_audio_continues_or_duplicates(self):
        self.assertIn("audio continued", self.run_it(late_audio=True)["dimensions"]["interruption"]["reason"])
        self.assertIn("2 Interrupted", self.run_it(double=True)["dimensions"]["interruption"]["reason"])

    def test_interruption_records_barge_in_evidence(self):
        d = self.run_it()["dimensions"]["interruption"]
        self.assertEqual(d["status"], "pass")
        self.assertEqual(d["generated_audio_ms"], 60)
        self.assertEqual(d["barge_in"], {"barge_in": "scripted"})

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


class CoveragePageTests(unittest.TestCase):
    def test_page_served_with_csp_and_no_inline_script(self):
        from fastapi.testclient import TestClient

        from voice_harness.api import create_app

        with tempfile.TemporaryDirectory() as d:
            c = TestClient(create_app(root=d, token="t", recover=False))
            page = c.get("/client/coverage.html")
            self.assertEqual(page.status_code, 200)
            self.assertIn("script-src 'self'", page.text)
            self.assertNotIn("<script>", page.text)
            self.assertEqual(c.get("/client/coverage.mjs").status_code, 200)
            # data stays behind auth even though the page shell is static
            self.assertEqual(c.get("/api/v1/providers").status_code, 401)


class StreamResamplerTests(unittest.TestCase):
    def test_chunked_matches_one_shot_length_and_has_no_seams(self):
        import array

        tone = array.array("h", [int(8000 * math.sin(2 * math.pi * 440 * n / 24000)) for n in range(2400)])
        pcm = tone.tobytes()
        st = rs.StreamResampler(24000, 16000)
        out = b"".join(st.process(c) for c in rs.chunk(pcm))
        got = array.array("h")
        got.frombytes(out)
        self.assertGreaterEqual(len(got), 1400)  # filter delay withholds the tail
        # no seam discontinuities: successive-sample jumps stay within a sine's maximum slope
        self.assertLess(max(abs(got[i + 1] - got[i]) for i in range(200, len(got) - 1)), 2 * 8000 * 2 * math.pi * 440 / 16000)
