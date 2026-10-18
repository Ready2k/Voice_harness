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
import hashlib
import json
import random
import time

from .events import PROCESS_CLOCK_DOMAIN
from .live_assessment import AdapterTurnDriver
from .mcp_gateway import gateway
from .resample import chunk
from .scenario_catalogue import AUDIO_PROFILES, catalogue_entry

CHUNK_MS = 20
RATE = 24000
TURN_TIMEOUT_S = 45.0
# Replies are held for whole-response assessment, so none of an interrupted reply was ever played.
PLAYED_MS_WHILE_HELD = 0
NATIVE_FLUSH_EVERY = 100
# Whole-response approval holds a full reply; live replies exceed the 2 s mock default.
# Headless runs never receive playback acknowledgements, so released audio stays "pending" in the engine's buffer
# accounting for the whole session; the budget therefore covers a session of replies (a per-response cap of the
# same size still applies at ingress). Engines for live runs are built with these limits, which the run manifest records.
LIVE_LIMITS = {"pending_audio_seconds": 120.0, "pending_audio_bytes": 120 * RATE * 2,
               "response_audio_seconds": 15.0, "response_audio_bytes": 15 * RATE * 2}
CANCEL_TIMEOUT_S = 5.0


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


CONTINUE = object()  # sentinel: the event does not settle the turn


class LiveScenarioRunner:
    def __init__(self, engine, run_id, adapter, assessor, scenario_id, *, caller_audio, cost_recorder=None,
                 seed=0, turn_timeout_s=TURN_TIMEOUT_S, interrupt_mode="client", barge_after_tool=False,
                 before_turn=None):
        entry = catalogue_entry(scenario_id)
        if entry is None:
            raise ValueError("SCENARIO_NOT_IN_CATALOGUE")
        self.engine, self.run_id, self.adapter, self.entry = engine, run_id, adapter, entry
        self.caller_audio, self.seed, self.turn_timeout_s = caller_audio, seed, turn_timeout_s
        self.before_turn = before_turn  # async (index) hook run before a caller turn, e.g. a trusted confirmation
        self.interrupt_mode = interrupt_mode  # "client": runner calls interrupt(); "vad": caller speech overlaps, provider VAD cuts in
        self.driver = AdapterTurnDriver(engine, run_id, assessor, cost_recorder=cost_recorder)
        self.tool_calls, self._fault_profile, self.interruptions = [], {}, 0
        self.interrupt_results, self._cancelled_ends = [], 0
        self.seed_value = seed
        self._native = []  # provider events received since the last flush (type, size, hash; never the payload)
        if hasattr(adapter, "native_sink"):
            adapter.native_sink = self._on_native
        self.tool_calls_expected = barge_after_tool  # barge in on the reply that follows a tool result

    def _on_native(self, adapter_name, native_type, payload):
        encoded = json.dumps(payload, sort_keys=True, default=str).encode()
        self._native.append({
            "adapter": adapter_name, "native_type": str(native_type)[:120].replace(" ", "_"),
            "payload_sha256": hashlib.sha256(encoded).hexdigest(), "payload_bytes": len(encoded),
            "payload_retained": False, "omission_reason": "HASH_ONLY_PAYLOAD_NOT_STORED",
            "received_ms": int(time.monotonic() * 1000), "clock_domain_id": PROCESS_CLOCK_DOMAIN,
        })
        if len(self._native) >= NATIVE_FLUSH_EVERY:
            self._flush_native()  # bounded memory, and a mid-turn crash loses at most this many records

    def _flush_native(self):
        pending, self._native = self._native, []
        if pending:
            with self.engine.database.transaction(self.run_id) as tx:
                for record in pending:
                    tx.event("provider", "adapter.native_event", record)

    def _bind(self, definition):
        with self.engine.database.transaction(self.run_id) as tx:
            tx.event("orchestrator", "run.live_binding", {
                "scenario_id": definition["id"], "scenario_hash": self.entry["content_hash"].removeprefix("sha256:"),
                "adapter": type(self.adapter).__name__, "seed": self.seed_value,
                "interrupt_mode": self.interrupt_mode, "audio_profile": definition["audio_profile"],
                "faults": self.entry["faults"], "limits": dict(self.engine.limits),
            })

    def _record_input(self, index, turn, pcm, started_ms, ended_ms):
        with self.engine.database.transaction(self.run_id) as tx:
            tx.event("orchestrator", "input.caller_audio", {
                "turn": index, "source_text_sha256": hashlib.sha256(turn["customer"].encode()).hexdigest(),
                "pcm_sha256": hashlib.sha256(pcm).hexdigest(), "byte_count": len(pcm), "sample_rate": RATE,
                "audio_retained": False, "transformations": [self.entry["definition"]["audio_profile"]],
                "stream_started_ms": started_ms, "stream_ended_ms": ended_ms,
                "clock_domain_id": PROCESS_CLOCK_DOMAIN,
            })

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
        if result["status"] == "success":
            # Amounts returned by a trusted tool may be spoken; they are allowed, not required.
            # Typed facts, rebuilt not mutated: jobs already frozen keep the context they were started with.
            now = int(time.monotonic() * 1000)
            facts = list(self.driver.expected_claims.get("allowed_facts", []))
            facts.extend(
                {"account_id": (result.get("structured_content") or {}).get("account_id"), "field": k,
                 "amount_minor": v, "currency": (result.get("structured_content") or {}).get("currency", "GBP"),
                 "source_call_id": event.call_id, "source_ts_ms": now}
                for k, v in (result.get("structured_content") or {}).items()
                if k.endswith("_minor") and isinstance(v, int))
            self.driver.expected_claims = {**self.driver.expected_claims, "allowed_facts": facts}
        receipt = await self.adapter.submit_tool_result(event.call_id, result)
        self.tool_calls.append({
            "call_id": event.call_id, "name": event.name, "status": result["status"],
            "error_code": result.get("error_code"), "fault_injected": injected,
            "elapsed_ms": round((time.monotonic() - started) * 1000), "delivery": receipt,
        })

    async def _cancel_generation(self):
        """Overflow or similar: stop the provider generating a reply nobody can release."""
        try:
            self.interrupt_results.append(
                await asyncio.wait_for(self.adapter.interrupt(None, PLAYED_MS_WHILE_HELD), CANCEL_TIMEOUT_S))
        except Exception as error:  # noqa: BLE001  # best effort; the failure is recorded, not hidden
            self.interrupt_results.append({"generation_cancelled": "failed", "error": type(error).__name__})
        self.driver.interrupt()

    async def _await_turn(self, bank, until_audio=False):
        """Pump adapter events until a turn_end (or fatal error / timeout); ``until_audio`` stops at the first reply audio."""
        errors = []
        follow_up = False  # a tool result was submitted, so the provider's next response belongs to this same turn
        tool_seen = False  # a tool-using reply speaks again after its result; that long reply is the barge-in target

        async def step(event, outcome):
            """Decide what a fed event means for the turn; returns an outcome dict, or CONTINUE."""
            nonlocal follow_up
            if self.driver.cancel_requested:
                self.driver.cancel_requested = False
                await self._cancel_generation()
            if until_audio and event.type == "audio_out" and (tool_seen or not self.tool_calls_expected):
                # Playback is simulated at real time: the caller cuts in 500 ms into the reply.
                await asyncio.sleep(0.5)
                self._cancelled_ends += 1
                if self.interrupt_mode == "client":
                    receipt = await self.adapter.interrupt(getattr(event, "item_id", None), PLAYED_MS_WHILE_HELD)
                    self.interrupt_results.append(receipt)
                    self.driver.interrupt(receipt)  # engine epoch, tokens, queued tools and history state
                return {"approval": None, "strict_speech_approval": "barge_in_not_assessed"}
            if event.type == "turn_end" and follow_up:
                follow_up = False  # end of the tool-calling response; wait for the one that uses the result
                return CONTINUE
            if event.type == "turn_end" and outcome is not None:
                return outcome
            if event.type == "turn_end" and self._cancelled_ends:
                self._cancelled_ends -= 1  # the interrupted reply's own turn_end, not the new turn's
                return CONTINUE
            if event.type == "turn_end":
                return {"approval": None, "strict_speech_approval": "no_audio"}
            return CONTINUE

        async def pump():
            nonlocal tool_seen, follow_up
            events = self.adapter.receive().__aiter__()
            nxt, running, settled, exhausted = None, {}, None, False  # running: task -> (job, response-end event)
            try:
                while True:
                    if nxt is None and not exhausted:
                        nxt = asyncio.ensure_future(events.__anext__())
                    waiting = {*running, *([nxt] if nxt is not None else [])}
                    if not waiting:  # stream closed and every assessment settled
                        return settled
                    await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    # Commits and control events are both handled here, on this one thread, in arrival order.
                    for task in [t for t in running if t.done()]:
                        job, ended = running.pop(task)
                        result = await step(ended, self.driver.commit(job, task.result()))
                        if result is not CONTINUE and (settled is None or settled["strict_speech_approval"] == "no_audio"):
                            settled = result
                    if settled is not None and not running:
                        return settled
                    if nxt is None or not nxt.done():
                        continue
                    finished, nxt = nxt, None
                    try:
                        event = finished.result()
                    except StopAsyncIteration:
                        exhausted = True
                        continue
                    if event.type == "tool_call":
                        await self._tool(bank, event)
                        tool_seen = True
                        follow_up = getattr(self.adapter, "TOOL_CALL_ENDS_RESPONSE", True)
                        continue
                    if event.type == "error":
                        errors.append({"code": event.code, "fatal": event.fatal})
                        if event.fatal:
                            return None
                    if event.type == "interrupted":
                        self.interruptions += 1
                    if event.type == "turn_end":
                        job, immediate = self.driver.begin(event)
                        if job is None:  # nothing to assess (no audio) or an immediate typed failure
                            result = await step(event, immediate)
                            if result is not CONTINUE and (settled is None or settled["strict_speech_approval"] == "no_audio"):
                                settled = result
                        else:  # keyed work item: assessed on a worker while intake and controls keep running
                            running[asyncio.ensure_future(asyncio.to_thread(self.driver.analyse, job))] = (job, event)
                        continue
                    result = await step(event, self.driver.feed(event))
                    if result is not CONTINUE and (settled is None or settled["strict_speech_approval"] == "no_audio"):
                        settled = result
                    if settled is not None and not running:
                        return settled
            finally:
                for pending in (nxt, *running):
                    if pending is None:
                        continue
                    if pending.done():
                        if not pending.cancelled():
                            pending.exception()  # mark retrieved (e.g. the stream closed as the turn settled)
                    else:
                        pending.cancel()

        try:
            outcome = await asyncio.wait_for(pump(), self.turn_timeout_s)
        except TimeoutError:
            # A worker may still be running ASR: fence it, revoke approvals and drop the half-received reply.
            self.driver.abandon("TURN_TIMEOUT")
            return {"approval": None, "strict_speech_approval": "turn_timeout"}, errors
        if outcome is None:  # fatal provider error, or the event stream ended before a turn_end
            self.driver.abandon("PROVIDER_FAILURE")
            fatal = any(e["fatal"] for e in errors)
            return {"approval": None, "strict_speech_approval": "provider_error" if fatal else "stream_ended"}, errors
        return outcome, errors

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
        self._bind(definition)
        try:
            async with gateway(self.engine, self.run_id) as bank:
                for index, turn in enumerate(definition["turns"][: definition["max_turns"]]):
                    pcm = apply_profile(self.caller_audio(turn["customer"]), definition["audio_profile"],
                                        self._faults("background_noise"), self.seed + index)
                    # Trusted tool amounts are allowed for the response they were returned in, not for later turns.
                    self.driver.expected_claims = {k: v for k, v in self.driver.expected_claims.items()
                                                   if k not in ("allowed_amounts_minor", "allowed_facts")}
                    if self.before_turn is not None:
                        await self.before_turn(index)
                    started_ms = int(time.monotonic() * 1000)
                    await self._stream(pcm)
                    self._record_input(index, turn, pcm, started_ms, int(time.monotonic() * 1000))
                    # The next turn interrupts: stop at the first reply audio and speak over it.
                    barge = self._faults("caller_interrupts", turn=index + 1)
                    outcome, errors = await self._await_turn(bank, until_audio=bool(barge))
                    outcomes.append({"turn": index, "strict_speech_approval": outcome["strict_speech_approval"]})
                    turn_errors.extend(errors)
                    self._flush_native()
                    if outcome["strict_speech_approval"] in ("provider_error", "stream_ended"):
                        break  # the session is unusable; later turns would only time out
        finally:
            self._flush_native()
            usage = self.driver.close()  # cost lines are closed out even when a turn raises
        return {
            "scenario_id": definition["id"], "run_id": self.run_id,
            "scenario_hash": self.entry["content_hash"], "turns": outcomes,
            "tool_calls": self.tool_calls, "adapter_errors": turn_errors, "interruptions": self.interruptions, "interrupt_results": self.interrupt_results,
            "faults_declared": self.entry["faults"], "usage_lines": len(usage or []),
        }
