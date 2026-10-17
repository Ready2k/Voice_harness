"""Run a catalogue scenario against a live adapter (Phase 4).

The caller side is synthetic speech turned into 20 ms PCM chunks, optionally degraded by
the scenario's audio profile.  Adapter events flow through ``AdapterTurnDriver`` (strict
speech approval, independent ASR); tool calls go through the MCP gateway so the ledger,
policy and fault evidence are the same as for mock runs.  Declared faults:

* ``tool_error``   -> engine fault ``timeout`` for the tool for the whole session (``fault.injected`` is recorded)
* ``tool_latency`` -> delay before the gateway call
* ``caller_interrupts`` -> the next caller turn starts while the assistant is still speaking
* ``background_noise`` / audio profile -> deterministic noise at the declared SNR
"""

from __future__ import annotations

import array
import asyncio
import random
import time

from .live_assessment import AdapterTurnDriver
from .mcp_gateway import gateway
from .resample import chunk
from .scenario_catalogue import AUDIO_PROFILES, catalogue_entry

CHUNK_MS = 20
RATE = 24000
TURN_TIMEOUT_S = 45.0


def degrade(pcm: bytes, *, snr_db=None, narrowband=False, seed=0) -> bytes:
    """Deterministic noise at ``snr_db`` and optional narrowband (8 kHz sample-and-hold) shaping."""
    samples = array.array("h")
    samples.frombytes(pcm)
    if narrowband:
        for i in range(len(samples)):
            samples[i] = samples[i - i % 3]  # 24 kHz -> 8 kHz zero-order hold
    if snr_db is not None and len(samples):
        power = sum(s * s for s in samples) / len(samples)
        sigma = (power / (10 ** (snr_db / 10))) ** 0.5
        rng = random.Random(seed)  # noqa: S311  # nosec B311 -- reproducible test noise, not security
        for i in range(len(samples)):
            samples[i] = max(-32768, min(32767, int(samples[i] + rng.gauss(0, sigma))))
    return samples.tobytes()


def apply_profile(pcm: bytes, profile: str, faults, seed=0) -> bytes:
    spec = AUDIO_PROFILES.get(profile, AUDIO_PROFILES["clean_studio"])
    snr = spec["snr_db"]
    for fault in faults:
        if fault["kind"] == "background_noise":
            snr = fault["snr_db"] if snr is None else min(snr, fault["snr_db"])
    if snr is None and not spec["narrowband"]:
        return pcm
    return degrade(pcm, snr_db=snr, narrowband=spec["narrowband"], seed=seed)


class LiveScenarioRunner:
    def __init__(self, engine, run_id, adapter, assessor, scenario_id, *, caller_audio, cost_recorder=None,
                 seed=0, turn_timeout_s=TURN_TIMEOUT_S):
        entry = catalogue_entry(scenario_id)
        if entry is None:
            raise ValueError("SCENARIO_NOT_IN_CATALOGUE")
        self.engine, self.run_id, self.adapter, self.entry = engine, run_id, adapter, entry
        self.caller_audio, self.seed, self.turn_timeout_s = caller_audio, seed, turn_timeout_s
        self.driver = AdapterTurnDriver(engine, run_id, assessor, cost_recorder=cost_recorder)
        self.tool_calls, self._fault_profile = [], {}

    def _faults(self, kind, turn=None, tool=None):
        return [
            f for f in self.entry["faults"]
            if f["kind"] == kind and (turn is None or f["at_turn"] == turn) and (tool is None or f["tool"] == tool)
        ]

    async def _stream(self, pcm):
        for part in chunk(pcm, RATE, CHUNK_MS):
            await self.adapter.send_audio(part, {"sample_count": len(part) // 2, "sample_rate": RATE})
            await asyncio.sleep(CHUNK_MS / 1000)

    async def _tool(self, bank, event):
        started = time.monotonic()
        for fault in self._faults("tool_latency", tool=event.name):
            await asyncio.sleep(fault["delay_ms"] / 1000)
        injected = event.name in self._fault_profile
        result = await bank.tool(event.call_id, event.name, event.arguments)
        receipt = await self.adapter.submit_tool_result(event.call_id, result)
        self.tool_calls.append({
            "call_id": event.call_id, "name": event.name, "status": result["status"],
            "error_code": result.get("error_code"), "fault_injected": injected,
            "elapsed_ms": round((time.monotonic() - started) * 1000), "delivery": receipt,
        })

    async def _await_turn(self, bank):
        """Pump adapter events until a turn_end (or fatal error / timeout)."""
        errors = []

        async def pump():
            async for event in self.adapter.receive():
                if event.type == "tool_call":
                    await self._tool(bank, event)
                    continue
                if event.type == "error":
                    errors.append({"code": event.code, "fatal": event.fatal})
                    if event.fatal:
                        return None
                outcome = self.driver.feed(event)
                if event.type == "turn_end" and outcome is not None:
                    return outcome
                if event.type == "turn_end":
                    return {"approval": None, "strict_speech_approval": "no_audio"}
            return None

        try:
            return await asyncio.wait_for(pump(), self.turn_timeout_s), errors
        except TimeoutError:
            return {"approval": None, "strict_speech_approval": "turn_timeout"}, errors

    async def run(self):
        definition = self.entry["definition"]
        outcomes, turn_errors = [], []
        # The MCP server process reads the fault profile once at launch, so it is set before the gateway starts.
        self._fault_profile = {f["tool"]: "timeout" for f in self._faults("tool_error")}
        previous = dict(self.engine.faults)
        self.engine.faults.update(self._fault_profile)
        try:
            return await self._run_turns(definition, outcomes, turn_errors)
        finally:
            self.engine.faults.clear()
            self.engine.faults.update(previous)

    async def _run_turns(self, definition, outcomes, turn_errors):
        async with gateway(self.engine, self.run_id) as bank:
            for index, turn in enumerate(definition["turns"][: definition["max_turns"]]):
                pcm = apply_profile(self.caller_audio(turn["customer"]), definition["audio_profile"],
                                    self._faults("background_noise"), self.seed + index)
                if self._faults("caller_interrupts", turn=index):
                    pcm = bytes(RATE * 2 * 3 // 2) + pcm  # speech begins after 1.5 s of assistant audio
                await self._stream(pcm)
                outcome, errors = await self._await_turn(bank)
                outcomes.append({"turn": index, "strict_speech_approval": outcome["strict_speech_approval"]})
                turn_errors.extend(errors)
        usage = self.driver.close()
        return {
            "scenario_id": definition["id"], "run_id": self.run_id,
            "scenario_hash": self.entry["content_hash"], "turns": outcomes,
            "tool_calls": self.tool_calls, "adapter_errors": turn_errors,
            "faults_declared": self.entry["faults"], "usage_lines": len(usage or []),
        }
