"""AC-09 conformance harness: run any LiveAdapter through shared probes.

Dimensions: audio_in, audio_out, tool_result_correlation, interruption, error.
A dimension an adapter declares unsupported (via capabilities) is reported as
"excluded" with a reason, never as "pass". `report["passed"]` requires every
non-excluded dimension to pass and audio_in/audio_out/tool_result_correlation
not to be excluded. Live adapters opt in with VOICE_HARNESS_LIVE_ADAPTERS
(comma list of: openai, aws_nova_sonic); see LiveConformance below.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from voice_harness.adapters import AudioOut, CapabilityUnsupported, ProviderError, ToolCall

DIMENSIONS = ("audio_in", "audio_out", "tool_result_correlation", "interruption", "error")
MANDATORY = ("audio_in", "audio_out", "tool_result_correlation")
SAMPLES_20MS = 480  # 24 kHz harness chunk


async def _collect(adapter, want, timeout: float) -> list:
    """Collect events until want(events) is true or timeout."""
    events: list = []

    async def pump():
        async for ev in adapter.receive():
            events.append(ev)
            if want(events):
                return

    try:
        await asyncio.wait_for(pump(), timeout)
    except TimeoutError:
        pass
    return events


async def run_conformance(
    adapter,
    *,
    prime: Callable[[str], Awaitable[None]] | None = None,
    timeout: float = 30.0,
    session_id: str = "conformance-1",
) -> dict[str, Any]:
    """``prime(dimension)`` lets a driver provoke provider behaviour: for
    audio_out/tool_result_correlation it must cause the adapter to emit
    AudioOut / ToolCall (live adapters: send_text prompt; mock: script queue)."""
    prime = prime or (lambda _d: asyncio.sleep(0))
    caps = adapter.capabilities()
    res: dict[str, dict[str, Any]] = {}

    def ok(dim, **kw):
        res[dim] = {"status": "pass", **kw}

    def fail(dim, why):
        res[dim] = {"status": "fail", "reason": why}

    def exclude(dim, why):
        res[dim] = {"status": "excluded", "reason": why}

    await adapter.open_session({"session_id": session_id})
    try:
        # audio_in
        if not caps.get("audio_in"):
            exclude("audio_in", "capabilities.audio_in false")
        else:
            try:
                pcm = bytes(SAMPLES_20MS * 2)
                await adapter.send_audio(pcm, {"sample_count": SAMPLES_20MS, "sample_rate": 24000})
                ok("audio_in")
            except Exception as e:  # noqa: BLE001
                fail("audio_in", f"valid chunk rejected: {e!r}")

        # audio_out
        if not caps.get("audio_out"):
            exclude("audio_out", "capabilities.audio_out false")
        else:
            await prime("audio_out")
            evs = await _collect(adapter, lambda e: any(isinstance(x, AudioOut) for x in e), timeout)
            audio = [e for e in evs if isinstance(e, AudioOut)]
            if not audio:
                fail("audio_out", "no AudioOut event")
            elif any(len(a.pcm) != a.sample_count * 2 or a.sample_count <= 0 for a in audio):
                fail("audio_out", "AudioOut length != sample_count*2")
            else:
                ok("audio_out", chunks=len(audio))

        # tool_result_correlation
        await prime("tool_result_correlation")
        evs = await _collect(adapter, lambda e: any(isinstance(x, ToolCall) for x in e), timeout)
        calls = [e for e in evs if isinstance(e, ToolCall)]
        if not calls:
            fail("tool_result_correlation", "no ToolCall event")
        else:
            cid = calls[0].call_id
            try:
                ack = await adapter.submit_tool_result(cid, {"call_id": cid, "ok": True})
                bad_rejected = False
                try:
                    await adapter.submit_tool_result("unknown-call", {"call_id": cid})
                except Exception:  # noqa: BLE001
                    bad_rejected = True
                if ack.get("call_id") != cid:
                    fail("tool_result_correlation", "ack call_id mismatch")
                elif not bad_rejected:
                    fail("tool_result_correlation", "mismatched call_id accepted")
                else:
                    ok("tool_result_correlation", call_id=cid)
            except Exception as e:  # noqa: BLE001
                fail("tool_result_correlation", f"matching result rejected: {e!r}")

        # interruption
        if caps.get("interrupt_truncate") != "supported":
            try:
                await adapter.interrupt(None, 0)
                fail("interruption", "declared unsupported but interrupt succeeded")
            except CapabilityUnsupported:
                exclude("interruption", "interrupt_truncate unsupported (explicit)")
            except Exception as e:  # noqa: BLE001
                fail("interruption", f"unsupported must raise CapabilityUnsupported, got {e!r}")
        else:
            await prime("interruption")
            try:
                r = await adapter.interrupt(None, 100)
                ok("interruption", result=sorted(r) if isinstance(r, dict) else None)
            except Exception as e:  # noqa: BLE001
                fail("interruption", repr(e))

        # error: malformed audio must raise or yield ProviderError, not hang/succeed
        err_ok = False
        try:
            await adapter.send_audio(b"\x00\x01\x02", {"sample_count": 99})
        except Exception:  # noqa: BLE001
            err_ok = True
        if not err_ok:
            evs = await _collect(adapter, lambda e: any(isinstance(x, ProviderError) for x in e), 2.0)
            err_ok = any(isinstance(e, ProviderError) for e in evs)
        ok("error") if err_ok else fail("error", "malformed audio silently accepted")
    finally:
        await adapter.close("conformance-done")
    # after close, sends must fail
    try:
        await adapter.send_text("x")
        fail("error", "send after close accepted")
    except Exception:  # noqa: BLE001, S110 - rejection is the expected outcome
        pass

    failed = [d for d, r in res.items() if r["status"] == "fail"]
    mandatory_excluded = [d for d in MANDATORY if res[d]["status"] == "excluded"]
    return {
        "provider": caps.get("provider"),
        "model_id": caps.get("model_id"),
        "dimensions": res,
        "excluded": {d: r["reason"] for d, r in res.items() if r["status"] == "excluded"},
        "failures": [f"{d}: {res[d]['reason']}" for d in failed],
        "passed": not failed and not mandatory_excluded,
    }


def write_result(report: dict, family: str, capability_report_id: str, out_dir) -> Path:
    """Persist an AC-09 result bound to a capability report (read by coverage.py)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{family}-ac09.json"
    path.write_text(json.dumps({**report, "capability_report_id": capability_report_id}, indent=2))
    return path


class LiveConformance(unittest.TestCase):
    """Opt-in: VOICE_HARNESS_LIVE_ADAPTERS=openai,aws_nova_sonic with credentials.
    Each adapter module must expose ``conformance_factory() -> (adapter, prime)``."""

    MODULES = {
        "openai": "voice_harness.adapters.openai_realtime",
        "aws_nova_sonic": "voice_harness.adapters.nova_sonic",
    }

    def test_live(self):
        wanted = [x for x in os.environ.get("VOICE_HARNESS_LIVE_ADAPTERS", "").split(",") if x]
        if not wanted:
            self.skipTest("set VOICE_HARNESS_LIVE_ADAPTERS to run live conformance")
        import importlib

        for fam in wanted:
            with self.subTest(fam):
                mod = importlib.import_module(self.MODULES[fam])
                factory = getattr(mod, "conformance_factory", None)
                if factory is None:
                    self.fail(f"{fam}: adapter lacks conformance_factory()")
                adapter, prime = factory()
                report = asyncio.run(run_conformance(adapter, prime=prime))
                print(json.dumps(report, indent=2))
                self.assertTrue(report["passed"], report["failures"])
