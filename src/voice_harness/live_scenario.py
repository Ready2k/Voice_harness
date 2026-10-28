"""Run a catalogue scenario against a live adapter (Phase 4).

The caller side is synthetic speech turned into 20 ms PCM chunks, optionally degraded by
the scenario's audio profile.  Adapter events flow through ``AdapterTurnDriver`` (strict
speech approval, independent ASR); tool calls go through the MCP gateway so the ledger,
policy and fault evidence are the same as for mock runs.  Declared faults:

* ``tool_error``   -> engine fault ``timeout`` for the tool for the whole session (``fault.injected`` is recorded)
* ``tool_latency`` -> delay before the gateway call
* ``caller_interrupts`` -> the next caller turn starts while the assistant is still speaking
* ``background_noise`` / audio profile -> deterministic noise at the declared SNR

Unless ``caller_interrupts`` targets it, a caller turn waits until the previous reply would have finished playing
at real time (replies arrive faster than real time and are held for assessment) plus ``CALLER_TURN_GAP_MS``, as a
real caller would. A provider
interruption for a response that has already ended is stale: it is recorded (``interruption.stale_ignored``) and
never cancels the current turn's reply.
"""

from __future__ import annotations

import array
import asyncio
import hashlib
import json
import random
import time

from .adapters import CapabilityUnsupported
from .events import PROCESS_CLOCK_DOMAIN
from .fault_dispositions import classify
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
# A verified OpenAI policy reply lasted 15.35 s; allow bounded short explanations across all providers.
LIVE_LIMITS = {"pending_audio_seconds": 120.0, "pending_audio_bytes": 120 * RATE * 2,
               "response_audio_seconds": 20.0, "response_audio_bytes": 20 * RATE * 2}
CANCEL_TIMEOUT_S = 5.0
# Silence after each synthetic caller utterance (lets provider VAD end the turn).
CALLER_TRAIL_MS = 1200
# Common pause between the end of the previous reply's real-time playback and the caller's next turn.
CALLER_TURN_GAP_MS = 300


def _b64_len(value):
    return len(value) * 3 // 4 - value.count("=", -2) if isinstance(value, str) else 0


def audio_bytes(payload):
    """Decoded bytes of provider output audio carried by one native event (0 if none), from its shape alone, so
    first-audio does not have to guess from the stored payload size. Covers Gemini ``serverContent.modelTurn``
    inline audio, OpenAI/xAI ``response.output_audio.delta``, Nova ``audioOutput`` and ElevenLabs ``audio``."""
    if not isinstance(payload, dict):
        return 0
    if payload.get("type") in ("response.output_audio.delta", "response.audio.delta"):
        return _b64_len(payload.get("delta"))
    if payload.get("type") == "audio":
        return _b64_len((payload.get("audio_event") or {}).get("audio_base_64"))
    if isinstance(payload.get("audioOutput"), dict):
        return _b64_len(payload["audioOutput"].get("content"))
    turn = (payload.get("serverContent") or {}).get("modelTurn") if isinstance(payload.get("serverContent"), dict) else None
    parts = turn.get("parts") if isinstance(turn, dict) else None
    return sum(_b64_len(p["inlineData"].get("data")) for p in parts or () if isinstance(p, dict)
               and isinstance(p.get("inlineData"), dict) and str(p["inlineData"].get("mimeType", "audio")).startswith("audio"))


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
                 before_turn=None, provider_session=None, trail_ms=0):
        entry = catalogue_entry(scenario_id)
        if entry is None:
            raise ValueError("SCENARIO_NOT_IN_CATALOGUE")
        self.engine, self.run_id, self.adapter, self.entry = engine, run_id, adapter, entry
        self.caller_audio, self.seed, self.turn_timeout_s = caller_audio, seed, turn_timeout_s
        self.provider_session = provider_session  # requested/returned session metadata, recorded in the binding
        transport = (provider_session or {}).get("harness_transport") or {}
        self.turn_commit, self.trail_ms = transport.get("turn_commit"), trail_ms  # trail_ms: silence ending each clip
        self.commit_delay_ms = transport.get("commit_delay_ms") if self.turn_commit == "explicit" else None
        self.before_turn = before_turn  # async (index) hook run before a caller turn, e.g. a trusted confirmation
        self.interrupt_mode = interrupt_mode  # "client": runner calls interrupt(); "vad": caller speech overlaps, provider VAD cuts in
        self.driver = AdapterTurnDriver(engine, run_id, assessor, cost_recorder=cost_recorder)
        self.tool_calls, self._fault_profile, self.interruptions = [], {}, 0
        self.interrupt_results, self._cancelled_ends = [], 0
        self.seed_value = seed
        self.pending_caller_pcm = None
        self._native = []  # provider events received since the last flush (type, size, hash; never the payload)
        if hasattr(adapter, "native_sink"):
            adapter.native_sink = self._on_native
        self.tool_calls_expected = barge_after_tool  # barge in on the reply that follows a tool result
        self.turn_gap_ms = transport.get("caller_turn_gap_ms", CALLER_TURN_GAP_MS)  # None: never wait (microphone)
        self._playback_end = None  # monotonic ms at which received reply audio would finish playing at real time
        # Provider responses as seen by the runner: an interruption is only applied to one still open.
        self._response_open, self._open_items, self._ended_items = False, set(), set()
        self._stale_end, self._turn_index = (), 0  # (turn_id,): the stale interruption's own turn_end, still due

    def _note_audio(self, event):
        """Reply audio plays back to back at real time from receipt; audio of a retired response is never heard."""
        item = getattr(event, "item_id", None)
        if item and item in getattr(self.driver, "_dead_responses", ()):
            return
        now = time.monotonic() * 1000
        self._playback_end = max(self._playback_end or now, now) + len(event.pcm) / 2 / event.sample_rate * 1000
        self._response_open, self._stale_end = True, ()
        if item:
            self._open_items.add(item)

    def _end_response(self):
        self._ended_items |= self._open_items
        self._open_items, self._response_open = set(), False

    def _stale(self, event, assessing):
        """Why an interruption cannot refer to a reply still playing (None: it can). A reply still being assessed
        has not been heard yet, so its interruption is always applied."""
        if assessing:
            return None
        item = getattr(event, "item_id", None)
        if item and item in self._ended_items and item not in self._open_items:
            return "RESPONSE_ALREADY_ENDED"
        return None if self._response_open else "NO_RESPONSE_OPEN"

    def _ignore_stale(self, event, reason):
        self._stale_end = (event.turn_id,)  # a provider that closes the cut response also ends it: not this turn's end
        # Its identity is still retired (late frames of it are refused) but the engine epoch and this turn are untouched.
        self.driver._mark_dead(getattr(event, "item_id", None))
        with self.engine.database.transaction(self.run_id) as tx:
            tx.event("orchestrator", "interruption.stale_ignored", {
                "turn": self._turn_index, "item_id": getattr(event, "item_id", None), "provider_turn_id": event.turn_id,
                "reason": reason, "received_ms": int(time.monotonic() * 1000), "clock_domain_id": PROCESS_CLOCK_DOMAIN})

    def _on_native(self, adapter_name, native_type, payload):
        encoded = json.dumps(payload, sort_keys=True, default=str).encode()
        self._native.append({
            "adapter": adapter_name, "native_type": str(native_type)[:120].replace(" ", "_"),
            "payload_sha256": hashlib.sha256(encoded).hexdigest(), "payload_bytes": len(encoded),
            "payload_retained": False, "omission_reason": "HASH_ONLY_PAYLOAD_NOT_STORED",
            "received_ms": int(time.monotonic() * 1000), "clock_domain_id": PROCESS_CLOCK_DOMAIN,
            "audio_bytes": audio_bytes(payload),
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
                "harness_mechanics": list(self.entry.get("mechanics", [])), "provider": self.provider_session,
            })

    def _record_input(self, index, turn, pcm, started_ms, ended_ms, *, retained=False, timing=None):
        with self.engine.database.transaction(self.run_id) as tx:
            tx.event("orchestrator", "input.caller_audio", {
                "turn": index, "source_text_sha256": hashlib.sha256(turn["customer"].encode()).hexdigest(),
                "pcm_sha256": hashlib.sha256(pcm).hexdigest(), "byte_count": len(pcm), "sample_rate": RATE,
                "audio_retained": retained, "transformations": [self.entry["definition"]["audio_profile"]],
                "stream_started_ms": started_ms, "stream_ended_ms": ended_ms,
                "clock_domain_id": PROCESS_CLOCK_DOMAIN, **(timing or {}),
            })

    async def _speak(self, index, turn, pcm, *, retained=False, hold=True):
        """Stream one caller clip, end the caller turn and record when speech ended and the turn was signalled.

        With ``hold`` (every turn but a ``caller_interrupts`` target) the caller first waits until the previous reply
        would have finished playing at real time, plus the common ``turn_gap_ms``.

        Explicit-commit providers get ``request_turn()`` a common emulated endpointing delay (``commit_delay_ms``)
        after the end of speech; the rest of the trailing silence is not sent, since Gemini reopens an activity on
        later audio and manual OpenAI/xAI buffers ignore it. Provider-VAD providers hear the whole clip and pay their
        own endpointing. Either way first audio is measured from ``speech_ended_ms``."""
        self._turn_index, reply_end, waited = index, self._playback_end, 0
        wait = "barge_in" if not hold else "first_turn" if not index else "no_reply_audio" if reply_end is None else "after_reply"
        if wait == "after_reply" and self.turn_gap_ms is not None and (
                waited := max(0, reply_end + self.turn_gap_ms - time.monotonic() * 1000)):
            await asyncio.sleep(waited / 1000)
        explicit = self.turn_commit == "explicit" and self.commit_delay_ms is not None
        speech_bytes = max(0, len(pcm) - self.trail_ms * RATE // 1000 * 2)
        started = time.monotonic() * 1000
        speech_end, sent = (None if speech_bytes else started), 0
        for part in chunk(pcm, RATE, CHUNK_MS):
            if explicit and speech_end is not None and time.monotonic() * 1000 >= speech_end + self.commit_delay_ms:
                break
            await self.adapter.send_audio(part, {"sample_count": len(part) // 2, "sample_rate": RATE})
            await asyncio.sleep(CHUNK_MS / 1000)
            sent += len(part)
            if speech_end is None and sent >= speech_bytes:  # paced in real time: back off the trail in this chunk
                speech_end = time.monotonic() * 1000 - (sent - speech_bytes) / 2 / RATE * 1000
        if explicit and (wait := speech_end + self.commit_delay_ms - time.monotonic() * 1000) > 0:
            await asyncio.sleep(wait / 1000)
        signalled = time.monotonic() * 1000
        await self.adapter.request_turn()  # Nova and other provider-VAD adapters implement this as a no-op
        self._record_input(index, turn, pcm, int(started), int(time.monotonic() * 1000), retained=retained, timing={
            "speech_ended_ms": int(speech_end), "turn_signal_ms": int(signalled) if explicit else None,
            "turn_commit": self.turn_commit, "commit_delay_ms": self.commit_delay_ms, "streamed_byte_count": sent,
            "caller_wait": wait, "reply_playback_end_ms": int(reply_end) if reply_end is not None else None,
            "caller_turn_gap_ms": self.turn_gap_ms, "caller_waited_ms": round(waited)})

    def _record_fault_dispositions(self, outcomes):
        """One typed ``fault.disposition`` per declared fault: handled, not exercised, not handled or misconduct."""
        if not self.entry["faults"]:
            return
        self._flush_native()
        found = classify(self.entry["faults"], self.engine.database.events(self.run_id), outcomes=outcomes,
                         tool_calls=self.tool_calls)
        with self.engine.database.transaction(self.run_id) as tx:
            for item in found:
                tx.event("orchestrator", "fault.disposition", item)

    def _faults(self, kind, turn=None, tool=None):
        return [
            f for f in self.entry["faults"]
            if f["kind"] == kind and (turn is None or f["at_turn"] == turn) and (tool is None or f["tool"] == tool)
        ]

    async def _tool(self, bank, event):
        started = time.monotonic()
        for fault in self._faults("tool_latency", tool=event.name):
            await asyncio.sleep(fault["delay_ms"] / 1000)
        injected = event.name in self._fault_profile
        result = await bank.tool(event.call_id, event.name, event.arguments)
        if result["status"] == "success":
            from .tool_claims import allowed_tool_claims

            amounts, dates = allowed_tool_claims(result, event.call_id, int(time.monotonic() * 1000))
            self.driver.expected_claims = {
                **self.driver.expected_claims,
                "allowed_facts": [*self.driver.expected_claims.get("allowed_facts", []), *amounts],
                "allowed_date_facts": [*self.driver.expected_claims.get("allowed_date_facts", []), *dates],
            }
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

    async def _await_turn(self, bank, until_audio=False, screen=True):
        """Pump adapter events until a turn_end (or fatal error / timeout); ``until_audio`` stops at the first reply audio.
        ``screen`` drops stale interruptions (off for a ``caller_interrupts`` target, whose overlap is the fault)."""
        errors = []
        follow_up = False  # a tool result was submitted, so the provider's next response belongs to this same turn
        tool_seen = False  # a tool-using reply speaks again after its result; that long reply is the barge-in target

        async def step(event, outcome, *, continues=False):
            """Decide what a fed event means for the turn; returns an outcome dict, or CONTINUE."""
            if self.driver.cancel_requested:
                self.driver.cancel_requested = False
                await self._cancel_generation()
                # Cancellation resets the driver's buffer, so retain the ingress failure here.
                # Do not wait for another provider turn boundary and mislabel it as a timeout.
                return {"approval": None, "strict_speech_approval": "audio_overflow",
                        "reason_codes": ["AUDIO_BACKPRESSURE"]}
            if until_audio and event.type == "audio_out" and (tool_seen or not self.tool_calls_expected):
                # Playback is simulated at real time: the caller cuts in 500 ms into the reply.
                await asyncio.sleep(0.5)
                self._cancelled_ends += 1
                self._playback_end = time.monotonic() * 1000  # playback stops where the caller cut in
                if self.interrupt_mode == "client":
                    try:
                        receipt = await self.adapter.interrupt(getattr(event, "item_id", None), PLAYED_MS_WHILE_HELD)
                    except CapabilityUnsupported:
                        # No cancel/truncate primitive (Gemini, Nova): local playback still stops and the
                        # receipt says so, so the history is recorded as diverged rather than the run failing.
                        receipt = {"generation_cancelled": "unsupported", "history_truncation": "unsupported"}
                    self.interrupt_results.append(receipt)
                    self.driver.interrupt(receipt)  # engine epoch, tokens, queued tools and history state
                return {"approval": None, "strict_speech_approval": "barge_in_not_assessed"}
            if event.type == "turn_end" and continues:
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
            response_active = False
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
                        job, ended, continues = running.pop(task)
                        result = await step(ended, self.driver.commit(job, task.result()), continues=continues)
                        if result is not CONTINUE and (settled is None or settled["strict_speech_approval"] == "no_audio"):
                            settled = result
                    if settled is not None and not running and not follow_up and not response_active:
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
                        settled = None
                        await self._tool(bank, event)
                        tool_seen = True
                        follow_up = getattr(self.adapter, "TOOL_CALL_ENDS_RESPONSE", True)
                        continue
                    if event.type == "error":
                        errors.append({"code": event.code, "fatal": event.fatal})
                        if event.fatal:
                            return None
                    if event.type == "interrupted":
                        if screen and (stale := self._stale(event, running)):
                            self._ignore_stale(event, stale)
                            continue
                        self.interruptions += 1
                        if self._playback_end is not None:  # the cut reply stops playing now
                            self._playback_end = min(self._playback_end, time.monotonic() * 1000)
                        self._end_response()
                    if event.type == "audio_out":
                        self._note_audio(event)
                    if event.type == "turn_start":
                        response_active, self._response_open, self._stale_end = True, True, ()
                        settled = None
                    if event.type == "turn_end":
                        if self._stale_end and self._stale_end[0] == event.turn_id and not self._response_open:
                            self._stale_end = ()  # the stale cut response's own end; this turn's reply is still due
                            continue
                        self._end_response()
                        response_active = False
                        # Bind continuation to this response before ASR can finish out of order.
                        continues, follow_up = follow_up, False
                        job, immediate = self.driver.begin(event)
                        if job is None:  # nothing to assess (no audio) or an immediate typed failure
                            result = await step(event, immediate, continues=continues)
                            if result is not CONTINUE and (settled is None or settled["strict_speech_approval"] == "no_audio"):
                                settled = result
                        else:  # keyed work item: assessed on a worker while intake and controls keep running
                            running[asyncio.ensure_future(asyncio.to_thread(self.driver.analyse, job))] = (job, event, continues)
                        continue
                    result = await step(event, self.driver.feed(event))
                    if result is not CONTINUE and (settled is None or settled["strict_speech_approval"] == "no_audio"):
                        settled = result
                    if settled is not None and settled["strict_speech_approval"] in {"barge_in_not_assessed", "audio_overflow"}:
                        return settled
                    if settled is not None and not running and not follow_up and not response_active:
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
        # Per run, never written to the engine: live runs share one engine, and a concurrent run's gateway would
        # otherwise launch with this run's faults.
        return await self._run_turns(definition, outcomes, turn_errors)

    async def _run_turns(self, definition, outcomes, turn_errors):
        self._bind(definition)
        try:
            async with gateway(self.engine, self.run_id, faults={**self.engine.faults, **self._fault_profile}) as bank:
                for index, turn in enumerate(definition["turns"][: definition["max_turns"]]):
                    pcm = apply_profile(self.caller_audio(turn["customer"]), definition["audio_profile"],
                                        self._faults("background_noise"), self.seed + index)
                    # Trusted tool amounts are allowed for the response they were returned in, not for later turns.
                    self.driver.expected_claims = {k: v for k, v in self.driver.expected_claims.items()
                                                   if k not in ("allowed_amounts_minor", "allowed_facts", "allowed_date_facts")}
                    self.pending_caller_pcm = pcm  # the audio about to be spoken, for observing a confirmation
                    if self.before_turn is not None:
                        await self.before_turn(index)
                    barged = bool(self._faults("caller_interrupts", turn=index))  # this turn speaks over the reply
                    await self._speak(index, turn, pcm, hold=not barged)
                    # The next turn interrupts: stop at the first reply audio and speak over it.
                    barge = self._faults("caller_interrupts", turn=index + 1)
                    outcome, errors = await self._await_turn(bank, until_audio=bool(barge), screen=not barged)
                    outcomes.append({"turn": index, "strict_speech_approval": outcome["strict_speech_approval"]})
                    turn_errors.extend(errors)
                    self._flush_native()
                    if outcome["strict_speech_approval"] in ("provider_error", "stream_ended"):
                        break  # the session is unusable; later turns would only time out
            self._record_fault_dispositions(outcomes)
        finally:
            self._flush_native()
            usage = self.driver.close()  # cost lines are closed out even when a turn raises
        return {
            "scenario_id": definition["id"], "run_id": self.run_id,
            "scenario_hash": self.entry["content_hash"], "turns": outcomes,
            "tool_calls": self.tool_calls, "adapter_errors": turn_errors, "interruptions": self.interruptions, "interrupt_results": self.interrupt_results,
            "faults_declared": self.entry["faults"], "usage_lines": len(usage or []),
        }
