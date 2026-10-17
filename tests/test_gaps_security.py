"""Phase 1 gap closure: roles, TLS bind, rate limit, error shape, states, shutdown."""

import asyncio
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from voice_harness.api import create_app
from voice_harness.auth import tls_options
from voice_harness.contracts import RunConfig
from voice_harness.engine import Engine
from voice_harness.errors import CATALOGUE, tool_error
from voice_harness.evidence import Evidence
from voice_harness.mcp_gateway import Gateway
from voice_harness.ratelimit import RateLimiter
from voice_harness.shutdown import graceful_shutdown
from voice_harness.storage import Database


class Base(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = Database(str(self.root / "t.sqlite"))
        self.engine = Engine(self.database)
        self.evidence = Evidence(self.database, self.root / "objects")


class RoleTests(Base):
    def test_reviewer_is_read_only_and_gets_restricted_export(self):
        app = create_app(self.database, self.root, "op", reviewer_token="rev")
        operator = {"Authorization": "Bearer op", "Idempotency-Key": "k1"}
        reviewer = {"Authorization": "Bearer rev", "Idempotency-Key": "k2"}
        with TestClient(app) as client:
            run = client.post("/api/v1/runs", headers=operator, json={}).json()["run_ids"][0]
            app.state.jobs[run].result(timeout=20)
            self.assertEqual(client.get("/api/v1/providers", headers=reviewer).status_code, 200)
            self.assertEqual(
                client.get(f"/api/v1/runs/{run}/events", headers=reviewer).status_code, 200
            )
            for method, path in [
                ("post", "/api/v1/runs"),
                ("post", f"/api/v1/runs/{run}/cancel"),
                ("post", f"/api/v1/runs/{run}/preservation-holds"),
                ("post", f"/api/v1/runs/{run}/ws-ticket"),
            ]:
                response = getattr(client, method)(path, headers=reviewer, json={})
                self.assertEqual(response.status_code, 403, path)
                self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")
            export = client.get(f"/api/v1/runs/{run}/evidence", headers=reviewer).json()
            self.assertEqual(export["manifest"]["export_type"], "reviewer_restricted")
            self.assertTrue(all(a["data_hex"] is None for a in export["artifacts"]))
            full = client.get(f"/api/v1/runs/{run}/evidence", headers=operator).json()
            self.assertEqual(full["manifest"]["export_type"], "standard")
            self.assertEqual(
                client.get("/api/v1/providers", headers={"Authorization": "Bearer x"}).status_code,
                401,
            )

    def test_reviewer_token_cannot_open_websocket(self):
        app = create_app(self.database, self.root, "op", reviewer_token="rev")
        run = self.engine.create(RunConfig().model_dump())
        with TestClient(app) as client:
            with self.assertRaises(Exception):  # noqa: B017 -- closed with 1008
                with client.websocket_connect(
                    f"/api/v1/runs/{run}/ws",
                    headers={"Authorization": "Bearer rev", "Origin": "http://localhost:8765"},
                ):
                    pass

    def test_generated_tokens_are_distinct_and_private(self):
        create_app(self.database, self.root)
        operator = (self.root / "operator-token").read_text()
        self.assertNotEqual(operator, (self.root / "reviewer-token").read_text())
        self.assertEqual((self.root / "reviewer-token").stat().st_mode & 0o777, 0o600)


class BindTests(unittest.TestCase):
    def test_non_loopback_requires_tls(self):
        self.assertEqual(tls_options("127.0.0.1", {}), {})
        with self.assertRaises(RuntimeError):
            tls_options("0.0.0.0", {})  # noqa: S104
        self.assertEqual(
            tls_options("0.0.0.0", {"HARNESS_PUBLISHED_LOOPBACK_ONLY": "1"}), {}  # noqa: S104
        )
        with self.assertRaises(RuntimeError):
            tls_options("0.0.0.0", {"HARNESS_TLS_CERT": "/nope", "HARNESS_TLS_KEY": "/nope"})  # noqa: S104

    def test_tls_files_enable_non_loopback(self):
        with tempfile.TemporaryDirectory() as d:
            cert, key = Path(d, "c.pem"), Path(d, "k.pem")
            cert.write_text("x")
            key.write_text("x")
            options = tls_options(
                "0.0.0.0",  # noqa: S104
                {"HARNESS_TLS_CERT": str(cert), "HARNESS_TLS_KEY": str(key)},
            )
            self.assertEqual(options["ssl_certfile"], str(cert))


class ErrorShapeTests(Base):
    def test_every_code_maps_into_catalogue_with_safe_message(self):
        for code in [*CATALOGUE, "TOOL_NOT_ALLOWED", "RUN_TERMINAL", "INVALID_TOOL_RESULT",
                     "CANCELLED_BEFORE_EXECUTION", "SOMETHING_UNMAPPED"]:
            error = tool_error(code)
            self.assertIn(error["code"], CATALOGUE)
            self.assertIsInstance(error["retryable"], bool)
            self.assertTrue(error["message"])
        self.assertTrue(tool_error("RATE_LIMITED")["retryable"])
        self.assertFalse(tool_error("OUTCOME_UNKNOWN")["retryable"])
        self.assertEqual(tool_error("SOMETHING_UNMAPPED")["code"], "FORBIDDEN")

    def test_result_carries_error_and_native_envelope_is_separate(self):
        run = self.engine.create(RunConfig().model_dump())
        result = self.engine.tool(
            run, self.engine.context(run), "c1", "create_payment",
            {"payee_id": "payee_001", "amount_minor": 1, "currency": "GBP"},
        )
        self.assertEqual(result["status"], "denied")
        self.assertIn(result["error"]["code"], CATALOGUE)
        self.assertEqual(set(result["error"]), {"code", "retryable", "message"})
        unknown = self.engine.tool(run, self.engine.context(run), "c2", "nope", {})
        self.assertEqual(unknown["error_code"], "TOOL_NOT_ALLOWED")
        self.assertEqual(unknown["error"]["code"], "FORBIDDEN")


class RateLimitTests(Base):
    def test_limiter_window(self):
        now = [0.0]
        limiter = RateLimiter(2, 10.0, clock=lambda: now[0])
        self.assertEqual([limiter.allow("r") for _ in range(3)], [True, True, False])
        self.assertTrue(limiter.allow("other"))
        now[0] = 11.0
        self.assertTrue(limiter.allow("r"))

    def test_gateway_returns_rate_limited_without_dispatch(self):
        run = self.engine.create(RunConfig().model_dump())

        class Session:
            calls = 0

            async def call_tool(self, *a, **k):
                Session.calls += 1
                raise RuntimeError("must not be dispatched")

        gateway = Gateway(Session(), self.engine, run, RateLimiter(0, 10.0))
        result = asyncio.run(
            gateway.tool("c1", "get_account_summary", {"account_id": "acct_1001"})
        )
        self.assertEqual((result["status"], result["error_code"]), ("error", "RATE_LIMITED"))
        self.assertTrue(result["error"]["retryable"])
        self.assertEqual(Session.calls, 0)
        types = [e["type"] for e in self.database.events(run)]
        self.assertIn("tool.requested", types)
        self.assertIn("tool.failed", types)


class StateAndSummaryTests(Base):
    def test_run_passes_probing_and_evaluating_and_reports_metric_summary(self):
        app = create_app(self.database, self.root, "op")
        headers = {"Authorization": "Bearer op", "Idempotency-Key": "k"}
        with TestClient(app) as client:
            run = client.post("/api/v1/runs", headers=headers, json={}).json()["run_ids"][0]
            app.state.jobs[run].result(timeout=20)
            types = [e["type"] for e in self.database.events(run)]
            order = [t for t in types if t in {"run.probing", "run.running", "run.evaluating", "run.completed"}]
            self.assertEqual(order, ["run.probing", "run.running", "run.evaluating", "run.completed"])
            view = client.get(f"/api/v1/runs/{run}", headers=headers).json()
            summary = view["metric_summary"]
            self.assertEqual(summary["status"], "derived_from_ledger")
            self.assertIn("generated_audio_units", summary["values"])
            self.assertIn("audio_underruns", summary["unavailable"])


class ShutdownTests(Base):
    def test_graceful_shutdown_settles_every_active_run(self):
        run = self.engine.create(RunConfig().model_dump())
        with self.database.transaction(run) as tx:
            tx.state["state"] = "running"
            tx.state["tokens"]["t"] = {"token": "x"}
            tx.state["units"]["u"] = {
                "state": "approved", "pcm": "00", "audio_sha256": "a" * 64,
                "generation_epoch": 1, "assessment_epoch": 1, "end_sample": 480,
            }
        epoch = self.database.get(run)["generation_epoch"]
        done = self.engine.create(RunConfig().model_dump())
        with self.database.transaction(done) as tx:
            tx.state["state"] = "completed"
        graceful_shutdown(self.engine, self.evidence, self.database, ThreadPoolExecutor(1))
        state = self.database.get(run)
        self.assertEqual(state["state"], "cancelled")
        self.assertEqual(state["tokens"], {})
        self.assertEqual(state["units"]["u"]["state"], "discarded")
        self.assertGreater(state["generation_epoch"], epoch)
        types = [e["type"] for e in self.database.events(run)]
        self.assertEqual(types[-2:], ["run.shutdown_requested", "run.cancelled"])
        self.assertEqual(self.database.get(done)["state"], "completed")

    def test_unresolved_tool_outcome_requires_recovery(self):
        run = self.engine.create(RunConfig().model_dump())
        with self.database.transaction(run) as tx:
            tx.state["state"] = "running"
            tx.state["calls"]["c"] = {
                "request_hash": "h", "state": "outcome_unknown", "result": {"status": "outcome_unknown"},
                "request_event_id": "e",
            }
        graceful_shutdown(self.engine, self.evidence, self.database, ThreadPoolExecutor(1))
        self.assertEqual(self.database.get(run)["state"], "recovery_required")

    def test_intake_refused_after_shutdown_begins(self):
        app = create_app(self.database, self.root, "op")
        with TestClient(app) as client:
            app.state.accepting = False
            response = client.post(
                "/api/v1/runs",
                headers={"Authorization": "Bearer op", "Idempotency-Key": "k"},
                json={},
            )
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "SHUTTING_DOWN"))
            app.state.accepting = True


if __name__ == "__main__":
    unittest.main()
