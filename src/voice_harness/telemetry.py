"""Best-effort traces, metrics and logs; the transactional ledger remains authoritative.

Nothing here may raise into, or block, the evidence path: every observer call is
wrapped, exporters use short timeouts, and a failed export loses telemetry only.
"""

import logging
import os
import threading
import time

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

RESOURCE = {"service.name": "voice-harness"}
TERMINAL = {"run.completed", "run.cancelled", "run.failed"}
MAX_OPEN_TURNS = 256


def _endpoint():
    value = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    return value.rstrip("/") if value else None


def provider():
    """Tracer provider (kept for callers that only need traces)."""
    result = TracerProvider(resource=Resource.create(RESOURCE))
    endpoint = _endpoint()
    if endpoint:
        result.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=1),
                max_queue_size=1024,
            )
        )
    return result


def meter_provider(readers=()):
    endpoint = _endpoint()
    readers = list(readers)
    if endpoint:
        readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=1),
                export_interval_millis=10_000,
                export_timeout_millis=1_000,
            )
        )
    return MeterProvider(resource=Resource.create(RESOURCE), metric_readers=readers)


def logger_provider(processors=()):
    result = LoggerProvider(resource=Resource.create(RESOURCE))
    endpoint = _endpoint()
    if endpoint:
        result.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=endpoint + "/v1/logs", timeout=1),
                max_queue_size=1024,
            )
        )
    for processor in processors:
        result.add_log_record_processor(processor)
    return result


def capture_link():
    """Links to the current span, for work that continues in another trace."""
    context = trace.get_current_span().get_span_context()
    return [trace.Link(context)] if context.is_valid else []


class Telemetry:
    """Run/turn/guardrail spans, metrics and structured logs derived from ledger events."""

    def __init__(self, traces=None, meters=None, logs=None):
        self.traces = traces or provider()
        self.meters = meters or meter_provider()
        self.logs = logs or logger_provider()
        self.tracer = self.traces.get_tracer("voice-harness.engine")
        meter = self.meters.get_meter("voice-harness")
        self.decisions = meter.create_counter(
            "voice_harness.guardrail.decisions", description="Guardrail decisions by action"
        )
        self.guardrail_latency = meter.create_histogram(
            "voice_harness.guardrail.latency", unit="ms", description="Guardrail decision latency"
        )
        self.runs = meter.create_counter(
            "voice_harness.runs.terminal", description="Runs reaching a terminal state"
        )
        self.turns = meter.create_counter("voice_harness.turns", description="Turns started")
        self.logger = self.logs.get_logger("voice-harness.audit")
        self._open = {}
        self._lock = threading.Lock()

    def job_span(self, name, links=(), **attributes):
        """A new root span (own trace) linked to the request that scheduled it."""
        return self.tracer.start_as_current_span(
            name, context=otel_context.Context(), links=list(links), attributes=attributes
        )

    def observe(self, event):
        """Called after a ledger append. Never raises."""
        try:
            self._observe(event)
        except Exception:  # noqa: BLE001 -- telemetry must not affect evidence
            logging.getLogger(__name__).debug("telemetry observe failed", exc_info=True)

    def _observe(self, event):
        kind, content, run = event["type"], event["content"], event["run_id"]
        base = {"run_id": run, "event_id": event["event_id"], "event_type": kind}
        if event.get("session_id"):
            base["session_id"] = event["session_id"]
        if event.get("turn_id"):
            base["turn_id"] = event["turn_id"]
        if kind == "turn.started":
            self.turns.add(1)
            with self._lock:
                self._end_turn(run)
                if len(self._open) >= MAX_OPEN_TURNS:
                    self._end_turn(next(iter(self._open)))
                self._open[run] = self.tracer.start_span("turn", attributes=base)
        elif kind == "decision":
            self._decision(run, content, base)
        elif kind in TERMINAL:
            self.runs.add(1, {"terminal_state": kind.removeprefix("run.")})
            self._log(SeverityNumber.INFO, kind, base)
            with self._lock:
                self._end_turn(run)

    def _end_turn(self, run):
        span = self._open.pop(run, None)
        if span is not None:
            span.end()

    def _decision(self, run, content, base):
        action, target = content.get("action", "unknown"), content.get("target", "unknown")
        latency_ms = float(content.get("latency_ms") or 0)
        attributes = {
            **base,
            "guardrail.action": action,
            "guardrail.target": target,
            "guardrail.policy_id": str(content.get("policy_id")),
            "guardrail.policy_version": str(content.get("policy_version")),
            "guardrail.reason_codes": [str(c) for c in content.get("reason_codes", [])],
        }
        end = time.time_ns()
        parent = self._open.get(run)
        span = self.tracer.start_span(
            "guardrail.decision",
            context=trace.set_span_in_context(parent) if parent is not None else None,
            start_time=end - int(latency_ms * 1e6),
            attributes=attributes,
        )
        span.end(end_time=end)
        labels = {"action": action, "target": target}
        self.decisions.add(1, labels)
        self.guardrail_latency.record(latency_ms, labels)
        if action != "allow":
            self._log(
                SeverityNumber.WARN,
                "guardrail " + action,
                {
                    **base,
                    "guardrail.target": target,
                    "guardrail.reason_codes": ",".join(attributes["guardrail.reason_codes"]),
                },
                span,
            )

    def _log(self, severity, body, attributes, span=None):
        context = span.get_span_context() if span is not None else None
        self.logger.emit(
            LogRecord(
                timestamp=time.time_ns(),
                severity_number=severity,
                severity_text=severity.name,
                body=body,
                attributes=attributes,
                trace_id=context.trace_id if context else None,
                span_id=context.span_id if context else None,
                trace_flags=context.trace_flags if context else None,
            )
        )

    def shutdown(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        with self._lock:
            for run in list(self._open):
                self._end_turn(run)
        for part in (self.traces, self.meters, self.logs):
            try:
                part.shutdown()
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).debug("telemetry shutdown failed", exc_info=True)


_active = None


def install(telemetry):
    """Set the process-wide observer used by the ledger writer (None disables)."""
    global _active
    _active = telemetry


def uninstall(telemetry):
    global _active
    if _active is telemetry:
        _active = None


def observe(event):
    active = _active
    if active is not None:
        active.observe(event)
