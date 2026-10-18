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
import time

from .adapters import AUDIO_OVERFLOW_CODE
from .asr import AsrUnavailable, strict_speech_status
from .engine import decision_record
from .events import PROCESS_CLOCK_DOMAIN
from .resample import resample
from .semantic_checks import semantic_check

BOUNDARY = "whole_response_no_alignment"
HARNESS_RATE = 24000
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
        """Assess one pending unit; returns the outcome dict (token present only when approved)."""
        unit = self.engine.database.get(run_id)["units"][unit_id]
        if unit["state"] != "pending" or "pcm" not in unit:
            raise ValueError("INVALID_ASSESSMENT_STATE")
        pcm = base64.b64decode(unit["pcm"])
        if self.backend is None:
            return self._unavailable(run_id, unit, "NO_ASR_BACKEND_CONFIGURED")
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
            return self._unavailable(run_id, unit, reason)
        if self.cost_recorder:
            self.cost_recorder.record_asr(result, f"asr:{unit_id}")
        if self.engine.database.get(run_id)["units"][unit_id]["state"] != "pending":
            # Interrupted (or otherwise superseded) while the ASR ran: a late result must never approve.
            return {"disposition": "untestable", "reason_codes": ["SUPERSEDED_DURING_ASSESSMENT"],
                    "unit_id": unit_id, "approval": None, "live": result.live,
                    "strict_speech_approval": "superseded"}
        check = semantic_check(result.text, expected)
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
        token = self.engine.approve_audio(
            run_id,
            replacement,
            scripted_safe=check["disposition"] == "safe",
            assessment_source="independent_asr",
        )
        return {
            "disposition": check["disposition"],
            "reason_codes": check["reason_codes"],
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
        self._text, self._turn_id = [], None
        self._pcm_bytes, self._overflow = 0, None

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
            if self._overflow:
                return None  # already over budget: unreleased audio is discarded, never buffered
            if self._first_audio is None:
                self._first_audio = event.ts_ms
            self._turn_id = event.turn_id or self._turn_id
            pcm = event.pcm
            if event.sample_rate != HARNESS_RATE:
                pcm = resample(pcm, event.sample_rate, HARNESS_RATE)
            self._pcm_bytes += len(pcm)
            limits = self.engine.limits
            if (self._pcm_bytes > limits["pending_audio_bytes"]
                    or self._pcm_bytes / 2 / HARNESS_RATE > limits["pending_audio_seconds"]):
                self._discard("PENDING_AUDIO_LIMIT_EXCEEDED")  # accounted from ingress, not at response end
            else:
                self._pcm.append(pcm)
        elif kind == "interrupted":
            self._reset()  # unreleased audio is never flushed; the buffer is dropped
            self.interrupt()
        elif kind == "turn_end":
            return self._finish(event)
        return None

    def interrupt(self, provider_receipt=None):
        """Provider or client interruption: advance the epoch, revoke tokens, discard unreleased units."""
        self.engine.interrupt(self.run_id, provider_receipt)
        self.interruptions_applied += 1

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
        if self._overflow:
            reason = self._overflow
            self._reset()
            outcome = {"approval": None, "strict_speech_approval": "audio_overflow", "reason_codes": [reason]}
            self.results.append(outcome)
            return outcome
        pcm = b"".join(self._pcm)
        if not pcm:
            self._reset()
            return None
        started = self.now_ms()
        try:
            unit_id = self.engine.hold_audio(
                self.run_id,
                pcm,
                transcript="".join(self._text),
                response_id=self._turn_id,
            )
        except ValueError as error:
            if str(error) != "AUDIO_BACKPRESSURE":
                raise
            self._reset()
            self.cancel_requested = True
            outcome = {"approval": None, "strict_speech_approval": "audio_overflow",
                       "reason_codes": ["AUDIO_BACKPRESSURE"]}
            self.results.append(outcome)
            return outcome
        outcome = self.assessor.assess(self.run_id, unit_id, self.expected_claims)
        decided = self.now_ms()
        released = None
        if outcome["approval"]:
            try:
                self.engine.release_audio(self.run_id, outcome["approval"])
                released = self.now_ms()
            except ValueError as error:  # superseded while assessing (interruption revoked the token)
                outcome = {**outcome, "approval": None, "strict_speech_approval": "superseded",
                           "reason_codes": [str(error)]}
        self._record(unit_id, outcome, end_event, started, decided, released)
        self.results.append(outcome)
        self._reset()
        return outcome

    def _record(self, unit_id, outcome, end_event, held_at, decided_at, released_at):
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
            since(self.speech_start_ms, self._first_transcript, "input.speech_start",
                  "first_nonempty_transcript", "first_transcript"),
            since(end, self._first_audio, "input.speech_end", "first_provider_audio",
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
                    "response_id": self._turn_id,
                    "unit_id": unit_id,
                    "source": "adapter_events",
                    "clock_domain_id": PROCESS_CLOCK_DOMAIN,
                    "adapter_clock_domain_id": self.adapter_clock_domain,
                    "metrics": metrics,
                },
            )
