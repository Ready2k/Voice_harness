"""OB-01: spans per turn/guardrail decision, metrics, logs and job links."""

import os
import tempfile
import unittest
from pathlib import Path

from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from voice_harness import telemetry
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.storage import Database


class Observed(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.spans, self.logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
        self.reader = InMemoryMetricReader()
        traces = TracerProvider()
        traces.add_span_processor(SimpleSpanProcessor(self.spans))
        self.telemetry = telemetry.Telemetry(
            traces=traces,
            meters=telemetry.meter_provider([self.reader]),
            logs=telemetry.logger_provider([SimpleLogRecordProcessor(self.logs)]),
        )
        telemetry.install(self.telemetry)
        self.addCleanup(telemetry.uninstall, self.telemetry)
        self.addCleanup(self.telemetry.shutdown)
        self.database = Database(
            os.environ.get(
                "HARNESS_TEST_DATABASE_URL", str(Path(directory.name) / "t.sqlite")
            )
        )
        self.engine = Engine(self.database)
        self.run = self.engine.create(RunConfig().model_dump())
        self.context = self.engine.context(self.run)

    def metric(self, name):
        data = self.reader.get_metrics_data()
        points = []
        for resource in data.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    if metric.name == name:
                        points.extend(metric.data.data_points)
        return points

    def test_turn_and_guardrail_spans_metrics_and_logs(self):
        self.engine.tool(
            self.run, self.context, "c1", "get_account_summary", {"account_id": "acct_1001"}
        )
        unit = self.engine.hold_audio(self.run, bytes(960), transcript="You should invest now")
        self.assertIsNone(self.engine.approve_audio(self.run, unit, scripted_safe=True))
        self.telemetry.shutdown()
        spans = {s.name: s for s in self.spans.get_finished_spans()}
        self.assertIn("turn", spans)
        decisions = [s for s in self.spans.get_finished_spans() if s.name == "guardrail.decision"]
        self.assertGreaterEqual(len(decisions), 2)
        replaced = [s for s in decisions if s.attributes["guardrail.action"] == "replace"]
        self.assertEqual(len(replaced), 1)
        self.assertEqual(replaced[0].attributes["run_id"], self.run)
        self.assertEqual(
            replaced[0].parent.span_id, spans["turn"].get_span_context().span_id
        )
        self.assertEqual(spans["turn"].attributes["run_id"], self.run)
        actions = {
            p.attributes["action"]: p.value for p in self.metric("voice_harness.guardrail.decisions")
        }
        self.assertEqual(actions["replace"], 1)
        self.assertTrue(self.metric("voice_harness.guardrail.latency"))
        logs = self.logs.get_finished_logs()
        warned = [r.log_record for r in logs if "guardrail replace" in str(r.log_record.body)]
        self.assertEqual(len(warned), 1)
        self.assertEqual(warned[0].span_id, replaced[0].get_span_context().span_id)

    def test_observer_errors_never_reach_the_ledger_path(self):
        self.telemetry.observe({"type": "decision"})  # malformed: must not raise
        self.assertTrue(self.database.events(self.run))

    def test_job_span_is_a_new_trace_linked_to_the_request(self):
        with self.telemetry.tracer.start_as_current_span("request") as request:
            links = telemetry.capture_link()
        with self.telemetry.job_span("run.job", links, run_id="r"):
            pass
        job = next(s for s in self.spans.get_finished_spans() if s.name == "run.job")
        self.assertNotEqual(job.context.trace_id, request.get_span_context().trace_id)
        self.assertEqual(
            [link.context.span_id for link in job.links], [request.get_span_context().span_id]
        )
        self.assertIsNone(job.parent)


if __name__ == "__main__":
    unittest.main()
