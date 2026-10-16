"""Best-effort tracing; the transactional ledger remains authoritative."""

import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def provider():
    result = TracerProvider(resource=Resource.create({"service.name": "voice-harness"}))
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        result.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=endpoint.rstrip("/") + "/v1/traces", timeout=1
                ),
                max_queue_size=1024,
            )
        )
    return result
