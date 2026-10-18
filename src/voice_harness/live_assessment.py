"""AC-14 live assessment: adapter PCM -> response boundary -> independent ASR -> semantic checks.

Strict speech approval on real adapter audio. Flow per completed response (the default
approval unit; incremental semantic segmentation stays disabled, so the boundary is
``whole_response_no_alignment``):

1. ``IndependentAssessor.assess`` transcribes the held PCM with an independent ASR backend.
2. The transcript is checked deterministically (spoken amounts/dates normalised; ambiguity
   is untestable). Disposition safe approves, unsafe/untestable holds.
3. The ASR transcript is fed through ``Engine.revise_audio(source="independent_asr")``, so
   the approval token binds the new assessment epoch and the ASR transcript hash.
4. With no usable backend the unit is blocked and strict speech approval is recorded as
   ``unavailable``. A mock/scripted backend is never reported as live evidence.

``AdapterTurnDriver`` consumes normalised adapter events, holds each completed response and
records adapter-run metric boundaries (time to first audio, first release, etc.).
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from dataclasses import dataclass

from .adapters import AUDIO_OVERFLOW_CODE
from .asr import AsrUnavailable, strict_speech_status
from .engine import decision_record
from .events import PROCESS_CLOCK_DOMAIN
from .resample import resample
from .semantic_checks import semantic_check

BOUNDARY = "whole_response_no_alignment"
HARNESS_RATE = 24000
MAX_CONCURRENT_ASSESSMENTS = 2
POLICY_ID = "banking.v1.playback_release"


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class IndependentAssessor:
    def __init__(self, engine, backend=None, cost_recorder=None):
        self.engine, self.backend = engine, backend
        self.cost_recorder = cost_recorder  # cost.UsageRecorder: records ASR usage (or unknown) per assessment

    def status(self):
        return strict_speech_status(self.backend)

    def assess(self, run_id, unit_id, expected=None):
        """Analyse then commit one pending unit; returns the outcome dict (token present only when approved)."""
        return self.commit(run_id, unit_id, self.analyse(run_id, unit_id, expected), expected)

    def analyse(self, run_id, unit_id, expected=None):
        """Read-only half, safe on a worker thread: ASR plus the semantic check over a frozen fact context.

        Nothing is approved or released here; ``commit`` applies the result after a validity check."""
        unit = self.engine.database.get(run_id)["units"][unit_id]
        if unit["state"] != "pending" or "pcm" not in unit:
            raise ValueError("INVALID_ASSESSMENT_STATE")
        pcm = base64.b64decode(unit["pcm"])
        if self.backend is None:
            return {"kind": "unavailable", "unit": unit, "reason": "NO_ASR_BACKEND_CONFIGURED"}
        try:
            ok, reason = self.backend.available()
            if not ok:
                raise AsrUnavailable(reason)
            result = self.backend.transcribe(pcm, unit["rate"])
        except AsrUnavailable as error:
            reason = str(error) or "ASR_UNAVAILABLE"
            if self.cost_recorder and reason.startswith(("ASR_HTTP_", "ASR_TRANSPORT", "ASR_MALFORMED")):
                # the request left the harness, so a charge cannot be ruled out: unknown, not zero
                self.cost_recorder.record_asr(None, f"asr:{unit_id}", failed_reason="ASR_REQUEST_FAILED_USAGE_UNKNOWN")
            return {"kind": "unavailable", "unit": unit, "reason": reason}
        if self.cost_recorder:
            self.cost_recorder.record_asr(result, f"asr:{unit_id}")
        return {"kind": "checked", "unit": unit, "result": result, "check": semantic_check(result.text, expected)}

    def commit(self, run_id, unit_id, analysis, expected=None):
        """Apply an analysis on the orchestration path; a unit no longer pending is superseded, never approved."""
        unit = analysis["unit"]
        if self.engine.database.get(run_id)["units"][unit_id]["state"] != "pending":
            # Interrupted (or otherwise superseded) while the ASR ran: a late result must never approve.
            live = analysis["result"].live if analysis["kind"] == "checked" else False
            return {"disposition": "untestable", "reason_codes": ["SUPERSEDED_DURING_ASSESSMENT"],
                    "unit_id": unit_id, "approval": None, "live": live,
                    "strict_speech_approval": "superseded"}
        if analysis["kind"] == "unavailable":
            return self._unavailable(run_id, unit, analysis["reason"])
        result, check = analysis["result"], analysis["check"]
        mapped = {"safe": "safe", "unsafe": "unsafe", "untestable": "unassessable"}[check["disposition"]]
        # The independent transcript becomes authoritative: new assessment epoch, replacement unit.
        replacement = self.engine.revise_audio(
            run_id, unit_id, result.text, actual_audio_assessment=mapped, source="independent_asr"
        )
        scope = "live_asr" if result.live else "offline_fixture_not_live_evidence"
        with self.engine.database.transaction(run_id) as tx:
            tx.event(
                "orchestrator",
                "assessment.independent_asr",
                {
                    "unit_id": unit_id,
                    "replacement_unit_id": replacement,
                    "backend": result.backend,
                    "model": result.model,
                    "live": result.live,
                    "assessment_scope": scope,
                    "audio_sha256": unit["audio_sha256"],
                    "transcript_sha256": _sha(result.text),
                    "disposition": check["disposition"],
                    "reason_codes": check["reason_codes"],
                    "claims": check["claims"],
                    "expected": expected or {},
                    "boundary": BOUNDARY,
                },
            )
        # A spoken amount was accepted on the strength of the identity frozen into the job; the engine
        # re-checks that identity is still current, transactionally, when it issues the approval.
        expected = expected or {}
        bound = None
        if expected.get("verified_account_id") and check["claims"]["amounts_minor"]:
            bound = {"account_id": expected["verified_account_id"], "identity_version": expected.get("identity_version")}
        token = self.engine.approve_audio(
            run_id,
            replacement,
            scripted_safe=check["disposition"] == "safe",
            assessment_source="independent_asr",
            identity_binding=bound,
        )
        withheld = self.engine.database.get(run_id)["units"][replacement].get("blocked_reason") if not token else None
        return {
            "disposition": check["disposition"],
            "reason_codes": [withheld, *check["reason_codes"]] if withheld else check["reason_codes"],
            "unit_id": replacement,
            "approval": token,
            "live": result.live,
            "transcript": result.text,
            "strict_speech_approval": "approved" if token else "held",
        }

    def _unavailable(self, run_id, unit, reason):
        """Fail closed: block the unit; strict speech approval is unavailable, never passed."""
        started = time.perf_counter()
        with self.engine.database.transaction(run_id) as tx:
            stored = tx.state["units"][unit["unit_id"]]
            stored["state"] = "blocked"
            stored.pop("pcm", None)
            marker = tx.event(
                "orchestrator",
                "assessment.asr_unavailable",
                {
                    "unit_id": unit["unit_id"],
                    "audio_sha256": unit["audio_sha256"],
                    "reason": reason,
                    "strict_speech_approval": "unavailable",
                },
            )
            tx.event(
                "guardrail",
                "decision",
                {
                    "unit_id": unit["unit_id"],
                    "reason": "STRICT_SPEECH_APPROVAL_UNAVAILABLE",
                    "assessment_source": "independent_asr",
                    **decision_record(
                        "audio_chunk",
                        "deny",
                        policy_id=POLICY_ID,
                        policy_version=unit["policy_version"],
                        reason_codes=["STRICT_SPEECH_APPROVAL_UNAVAILABLE", reason],
                        input_hash=unit["audio_sha256"],
                        evidence_event_ids=[marker],
                        started=started,
                    ),
                },
                causes=[marker],
            )
        return {
            "disposition": "untestable",
            "reason_codes": [reason],
            "unit_id": unit["unit_id"],
            "approval": None,
            "live": False,
            "strict_speech_approval": "unavailable",
        }


@dataclass(frozen=True)
class AssessmentJob:
    """Immutable per-response assessment: identity, generation, deadline origin and frozen fact context."""

    job_id: int
    unit_id: str
    item_ids: frozenset
    turn_id: str | None
    epoch: int  # engine generation epoch when the response ended
    facts_json: str  # deep-frozen assessment context
    facts_hash: str
    snapshot: dict
    end_event: object
    held_at: int
    unkeyed: bool = False  # some of the response's frames carried no provider item id

    @property
    def facts(self):
        return json.loads(self.facts_json)


def _metric(name, value, start, end, reason=None):
    if value is None and reason is None:
        raise ValueError("NULL_METRIC_REQUIRES_REASON")
    return {"metric": name, "unit": "ms", "value": value, "start": start, "end": end, "exclusion_reason": reason}


class AdapterTurnDriver:
    """Feeds normalised adapter events into the gate and records metric boundaries.

    ``speech_start_ms``/``speech_end_ms`` are harness-clock input boundaries in the same
    monotonic domain as the adapter's ``ts_ms``; when they are absent, or the adapter
    declares another domain, the dependent metrics are null with a reason.
    """

    def __init__(
        self,
        engine,
        run_id,
        assessor,
        *,
        expected_claims=None,
        speech_start_ms=None,
        speech_end_ms=None,
        adapter_clock_domain=PROCESS_CLOCK_DOMAIN,
        now_ms=lambda: int(time.monotonic() * 1000),
        cost_recorder=None,
    ):
        self.engine, self.run_id, self.assessor = engine, run_id, assessor
        self.expected_claims = expected_claims or {}
        self.speech_start_ms, self.speech_end_ms = speech_start_ms, speech_end_ms
        self.adapter_clock_domain, self.now_ms = adapter_clock_domain, now_ms
        self.results = []
        self.interruptions_applied, self.cancel_requested = 0, False
        self._fence = 0  # advanced on abandon/timeout: a worker started before it can never release
        self._dead_responses = set()  # provider item ids retired by interruption/timeout; never accepted again
        self._jobs = {}  # job_id -> AssessmentJob for responses whose assessment has not been committed
        self._job_seq = 0
        self._asr_slots = threading.BoundedSemaphore(MAX_CONCURRENT_ASSESSMENTS)
        # After a retirement that could not be keyed (no provider item id), unkeyed audio is refused until the
        # next response boundary, because it cannot be told apart from the retired response's late frames.
        self._quarantine_unkeyed = False
        # Provider usage -> cost.usage_reported; declares the expected components for this run.
        self.cost_recorder = cost_recorder
        if cost_recorder is not None:
            if assessor.cost_recorder is None:
                assessor.cost_recorder = cost_recorder
            asr_live = assessor.backend is not None and assessor.backend.available()[0]
            cost_recorder.declare(cost_recorder.component, *(["asr"] if asr_live else []))
        self._reset()

    def _reset(self):
        self._pcm, self._first_audio, self._first_transcript = [], None, None
        self._text, self._turn_id, self._item_id = [], None, None
        self._item_ids = set()
        self._pcm_bytes, self._overflow = 0, None
        self._unkeyed_frames = False

    def feed(self, event):
        if self.cost_recorder is not None:
            self.cost_recorder.observe(event)
        return self._feed(event)

    def close(self):
        """Session end: cumulative-usage providers that never reported usage record unknown, not zero."""
        return self.cost_recorder.close() if self.cost_recorder is not None else []

    def _feed(self, event):
        kind = event.type
        if kind == "transcript_delta" or kind == "transcript_final":
            if event.text.strip() and self._first_transcript is None:
                self._first_transcript = event.ts_ms
            if event.role == "assistant":
                if kind == "transcript_final":
                    self._text = [event.text]
                else:
                    self._text.append(event.text)
        elif kind == "error" and event.code == AUDIO_OVERFLOW_CODE:
            self._discard("ADAPTER_QUEUE_OVERFLOW")
        elif kind == "audio_out":
            if getattr(event, "item_id", None) and event.item_id in self._dead_responses:
                return None  # late audio of an interrupted response must not start a unit in the new epoch
            if self._quarantine_unkeyed and not getattr(event, "item_id", None):
                return None
            if self._overflow:
                return None  # already over budget: unreleased audio is discarded, never buffered
            if self._first_audio is None:
                self._first_audio = event.ts_ms
            self._turn_id = event.turn_id or self._turn_id
            self._item_id = getattr(event, "item_id", None) or self._item_id
            if getattr(event, "item_id", None):
                self._item_ids.add(event.item_id)
            else:
                self._unkeyed_frames = True
            pcm = event.pcm
            if event.sample_rate != HARNESS_RATE:
                pcm = resample(pcm, event.sample_rate, HARNESS_RATE)
            self._pcm_bytes += len(pcm)
            limits = self.engine.limits
            # one response is capped separately from the session-wide pending budget (which headless runs fill up)
            if (self._pcm_bytes > limits.get("response_audio_bytes", limits["pending_audio_bytes"])
                    or self._pcm_bytes / 2 / HARNESS_RATE
                    > limits.get("response_audio_seconds", limits["pending_audio_seconds"])):
                self._discard("PENDING_AUDIO_LIMIT_EXCEEDED")  # accounted from ingress, not at response end
            else:
                self._pcm.append(pcm)
        elif kind == "interrupted":
            self._mark_dead(getattr(event, "item_id", None))
            self._reset()  # unreleased audio is never flushed; the buffer is dropped
            self.interrupt()
        elif kind == "turn_start":
            self._quarantine_unkeyed = False
        elif kind == "turn_end":
            return self._finish(event)
        return None

    def interrupt(self, provider_receipt=None):
        """Provider or client interruption: advance the epoch, revoke tokens, discard unreleased units."""
        self._mark_dead()
        self._reset()
        self.engine.interrupt(self.run_id, provider_receipt)
        self.interruptions_applied += 1

    def _mark_dead(self, item_id=None):
        """Output items are keyed by provider item id (Nova's turn id spans the whole session)."""
        self._dead_responses.update(i for i in (item_id, self._item_id, *self._item_ids) if i)
        # Nothing to key the retirement on (no item id given, buffered or in flight), or unkeyed frames already seen:
        # the retired response's late unkeyed frames cannot be told apart from a new one, even before its first frame.
        unkeyed = self._unkeyed_frames or not (item_id or self._item_id or self._item_ids)
        for job in self._jobs.values():  # identities of responses still being assessed are retired too
            self._dead_responses.update(job.item_ids)
            unkeyed = unkeyed or not job.item_ids or job.unkeyed
        if unkeyed:
            self._quarantine_unkeyed = True
        self._jobs.clear()  # every in-flight job is retired: its commit can only report 'superseded'

    def abandon(self, reason="ABANDONED"):
        """Timeout or fatal failure: drop buffers, revoke approvals, retire identities and record the disposition."""
        self._fence += 1
        retired = [{"job_id": j.job_id, "unit_id": j.unit_id, "item_ids": sorted(j.item_ids)}
                   for j in self._jobs.values()]
        buffered = sorted(self._item_ids | ({self._item_id} if self._item_id else set()))
        self._mark_dead()
        self._reset()
        self.interrupt()
        with self.engine.database.transaction(self.run_id) as tx:
            tx.event("orchestrator", "assessment.abandoned", {
                "reason": reason, "retired_jobs": retired, "buffered_item_ids": buffered,
                "generation_epoch": tx.state["generation_epoch"], "released": False,
                "unkeyed_quarantine": self._quarantine_unkeyed,
            })

    def _discard(self, reason):
        """Drop buffered audio and record the typed failure; the response is never assessed or released."""
        buffered, self._pcm, self._pcm_bytes = self._pcm_bytes, [], 0
        if self._overflow is None:
            self._overflow = reason
            with self.engine.database.transaction(self.run_id) as tx:
                tx.event("orchestrator", "audio.backpressure", {
                    "failure_code": "AUDIO_BACKPRESSURE", "buffered_bytes": buffered, "incoming_bytes": 0,
                    "limit_bytes": self.engine.limits["pending_audio_bytes"], "discarded_unit_ids": [],
                    "generation_epoch": tx.state["generation_epoch"], "provider_cancellation": "requested",
                    "fallback_audio": "none_configured",
                })
            self.cancel_requested = True  # the runner asks the provider to stop generating

    def _finish(self, end_event):
        """Synchronous convenience path: begin, analyse and commit in one call."""
        job, outcome = self.begin(end_event)
        if job is None:
            return outcome
        return self.commit(job, self.analyse(job))

    def begin(self, end_event):
        """Response end: freeze identity, epoch and facts into an immutable job. Returns (job, immediate_outcome)."""
        if self._overflow:
            reason = self._overflow
            self._reset()
            outcome = {"approval": None, "strict_speech_approval": "audio_overflow", "reason_codes": [reason]}
            self.results.append(outcome)
            return None, outcome
        self._quarantine_unkeyed = False  # a response boundary: new unkeyed audio belongs to a new response
        pcm = b"".join(self._pcm)
        snapshot = {"turn_id": self._turn_id, "first_audio": self._first_audio,
                    "first_transcript": self._first_transcript, "text": "".join(self._text)}
        items = frozenset(self._item_ids | ({self._item_id} if self._item_id else set()))
        unkeyed_frames = self._unkeyed_frames
        self._reset()  # the next response buffers while this one is assessed
        if not pcm:
            return None, None
        started = self.now_ms()
        try:
            unit_id = self.engine.hold_audio(
                self.run_id, pcm, transcript=snapshot["text"], response_id=snapshot["turn_id"],
            )
        except ValueError as error:
            if str(error) != "AUDIO_BACKPRESSURE":
                raise
            self.cancel_requested = True
            outcome = {"approval": None, "strict_speech_approval": "audio_overflow",
                       "reason_codes": ["AUDIO_BACKPRESSURE"]}
            self.results.append(outcome)
            return None, outcome
        state = self.engine.database.get(self.run_id)
        verified = state.get("identity") == "verified" and state["identity_expires"] > self.engine.clock()
        context = {**self.expected_claims,
                   "verified_account_id": state["account_id"] if verified else None,
                   "identity_version": state.get("identity_version") if verified else None}
        facts_json = json.dumps(context, sort_keys=True, default=str)  # deep copy, frozen
        self._job_seq += 1
        job = AssessmentJob(
            job_id=self._job_seq, unit_id=unit_id, item_ids=items, turn_id=snapshot["turn_id"],
            epoch=state["generation_epoch"], facts_json=facts_json,
            facts_hash=_sha(facts_json), snapshot=snapshot, end_event=end_event, held_at=started,
            unkeyed=unkeyed_frames,
        )
        self._jobs[job.job_id] = job
        return job, None

    def analyse(self, job):
        """Worker-safe half: ASR and checks against the job's frozen facts. Never approves or releases."""
        with self._asr_slots:
            try:
                return self.assessor.analyse(self.run_id, job.unit_id, job.facts)
            except ValueError as error:  # the unit was discarded before ASR started
                return {"kind": "error", "reason": str(error)}

    def commit(self, job, analysis):
        """Orchestration path (event-loop thread): validity check, then approve/release, then record."""
        active = self._jobs.pop(job.job_id, None)
        decided = self.now_ms()
        if active is None or analysis.get("kind") == "error":
            reason = "RETIRED_BEFORE_COMMIT" if active is None else analysis["reason"]
            outcome = {"approval": None, "strict_speech_approval": "superseded", "reason_codes": [reason]}
            self.results.append(outcome)
            return outcome
        try:
            outcome = self.assessor.commit(self.run_id, job.unit_id, analysis, job.facts)
        except ValueError as error:
            outcome = {"approval": None, "strict_speech_approval": "superseded", "reason_codes": [str(error)]}
        outcome["facts_hash"] = job.facts_hash
        released = None
        if outcome["approval"]:
            try:
                self.engine.release_audio(self.run_id, outcome["approval"])
                released = self.now_ms()
            except ValueError as error:  # superseded while assessing (interruption revoked the token)
                outcome = {**outcome, "approval": None, "strict_speech_approval": "superseded",
                           "reason_codes": [str(error)]}
        self._record(job.unit_id, outcome, job.end_event, job.held_at, decided, released, job.snapshot)
        self.results.append(outcome)
        return outcome

    def _record(self, unit_id, outcome, end_event, held_at, decided_at, released_at, snap):
        same = self.adapter_clock_domain == PROCESS_CLOCK_DOMAIN
        end = self.speech_end_ms

        def since(origin, point, label_from, label_to, name):
            if origin is None:
                return _metric(name, None, label_from, label_to, "NO_INPUT_BOUNDARY_SUPPLIED")
            if point is None:
                return _metric(name, None, label_from, label_to, "ADAPTER_EVENT_MISSING")
            if not same:
                return _metric(name, None, label_from, label_to, "CROSS_CLOCK_DOMAIN")
            return _metric(name, float(point - origin), label_from, label_to)

        metrics = [
            since(self.speech_start_ms, snap["first_transcript"], "input.speech_start",
                  "first_nonempty_transcript", "first_transcript"),
            since(end, snap["first_audio"], "input.speech_end", "first_provider_audio",
                  "time_to_first_generated_audio"),
            since(end, released_at, "input.speech_end", "first_gate_release", "time_to_first_released_audio")
            if released_at is not None
            else _metric("time_to_first_released_audio", None, "input.speech_end", "first_gate_release",
                         "NOT_RELEASED_" + outcome["strict_speech_approval"].upper()),
            since(end, end_event.ts_ms, "input.speech_end", "turn_end", "turn_completion"),
            _metric("adapter_assessment_hold_duration", float(decided_at - held_at), "unit_held",
                    "assessment_decision"),
        ]
        with self.engine.database.transaction(self.run_id) as tx:
            tx.event(
                "orchestrator",
                "benchmark.adapter_metrics",
                {
                    "response_id": snap["turn_id"],
                    "unit_id": unit_id,
                    "source": "adapter_events",
                    "clock_domain_id": PROCESS_CLOCK_DOMAIN,
                    "adapter_clock_domain_id": self.adapter_clock_domain,
                    "metrics": metrics,
                },
            )
