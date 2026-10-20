"""Authenticated loopback control plane and bounded reference WebSocket framing."""

import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import live_providers
from .approvals import public_key
from .auth import bearer_matches, load_token, restrict_bundle, reviewer_may, role_for, tls_options
from .bakeoff import BakeoffRequest, BakeoffStore, Weights
from .bakeoff import summary as bakeoff_summary
from .bakeoff import tick as bakeoff_step
from .bakeoff_export import render as render_bakeoff
from .bakeoff_export import report_data as bakeoff_report_data
from .clock_runtime import ClockSyncSession, invalidate_prior_connection
from .contracts import AudioInputPayload, CancelRun, HoldRequest, ManualSessionConfig, RenewHold, RunConfig
from .cost_api import register_cost_routes
from .coverage import build_registry, summarize
from .engine import Engine
from .evidence import Evidence
from .exports import download_links
from .inbound_queue import InboundQueue
from .judges import service as judge_service
from .live_scenario import LIVE_LIMITS
from .manual_session import MANUAL_LIMITS, Bridge
from .manual_session import journeys as manual_journeys
from .manual_session import scenario_for as manual_scenario
from .metrics import derive as derive_metrics
from .review_api import register_review_routes
from .scenario_catalogue import CATALOGUE, presentation
from .scenario_defs import SCENARIOS, ScenarioDefinition, definition_hash
from .scenarios import run_scenario
from .shutdown import graceful_shutdown
from .storage import Database
from .verdicts import aggregate


def create_app(
    database=None, root=None, token=None, recover=True, reviewer_token=None
):
    root = Path(root or os.environ.get("HARNESS_DATA", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    if token is None:
        token = load_token(root, "operator-token")
        reviewer_token = reviewer_token or load_token(root, "reviewer-token")
    database = database or Database(
        os.environ.get("HARNESS_DATABASE_URL", str(root / "development.sqlite"))
    )
    from .signing import KeyStore, SigningError

    keystore = KeyStore(root)
    engine = Engine(database)
    live_engine = Engine(database, limits=LIVE_LIMITS)  # live runs record, and are bound by, the live budgets
    manual_engine = Engine(database, limits=MANUAL_LIMITS)  # human-driven sessions run for up to 15 minutes
    manual_bridges = {}  # run_id -> Bridge while the page's WebSocket is attached
    evidence = Evidence(database, root / "objects", signer=keystore.signer())
    from . import telemetry as telemetry_module

    telemetry = telemetry_module.Telemetry()
    telemetry_module.install(telemetry)
    tracer = telemetry.traces.get_tracer("voice-harness.control")
    job_links = {}
    jobs, job_lock = {}, threading.Lock()
    tickets = {}
    executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="mock-run")

    def work(run_id):
        # The job is its own trace, linked to the request that scheduled it.
        with telemetry.job_span("run.job", job_links.pop(run_id, ()), run_id=run_id):
            run_work(run_id)

    def run_work(run_id):
        try:
            with database.transaction(run_id) as tx:
                if tx.state["state"] != "queued":
                    return
                tx.state["state"] = "probing"
                provider_config = tx.state["config"]["provider_config_id"]
                if provider_config in live_providers.LIVE_PROVIDERS:
                    # Credentials alone are not capability verification: record the bound report, or the
                    # fact that this is an unverified diagnostic run.
                    probe = live_providers.capability_binding(provider_config)
                    tx.event("orchestrator", "run.probing", probe)
                else:
                    # Mock capability probe: fixture providers have nothing to discover.
                    tx.event("orchestrator", "run.probing", {"capability_status": "mock_only"})
            with database.transaction(run_id) as tx:
                if tx.state["state"] != "probing":
                    return
                tx.state["state"] = "running"
                tx.event("orchestrator", "run.running", {})
            config = database.get(run_id)["config"]
            if config.get("input_mode") == "manual":
                run_manual_work(run_id, config)
            elif config["provider_config_id"] in live_providers.LIVE_PROVIDERS:
                run_live_work(run_id, config)
            else:
                run_scenario(engine, run_id, evidence)
        except Exception:  # noqa: BLE001 -- protected worker boundary fails closed
            logging.getLogger("voice_harness.run").exception("run worker failed: %s", run_id)
            with database.transaction(run_id) as tx:
                if tx.state["state"] not in {"cancelled", "recovery_required"}:
                    tx.state["state"] = "failed"
                    # A failed run must not keep buffered PCM beyond its retention class.
                    for unit in tx.state["units"].values():
                        if unit["state"] in {"pending", "approved", "scheduled"}:
                            unit["state"] = "discarded"
                        unit.pop("pcm", None)
                    tx.state["tokens"].clear()
                    tx.event("orchestrator", "run.failed", {"reason": "WORKER_FAILED"})
        finally:
            evidence.save_manifest(run_id)

    live_tasks = {}  # run_id -> (loop, task) for runs whose provider session is open

    async def live_task(run_id, config, session=None):
        """Run the live scenario (or ``session``) as a cancellable task; returns None if the run was cancelled."""
        task = asyncio.ensure_future(
            session if session is not None else live_providers.run_live(
                live_engine, run_id, config["provider_config_id"], config["scenario_id"], seed=config["fixture_seed"],
                caller=config.get("caller"), evidence=evidence,
                prompt_profile=config.get("prompt_profile", "standardised-v2"),
            )
        )
        with job_lock:
            live_tasks[run_id] = (asyncio.get_running_loop(), task)
        try:
            # A cancel that landed before registration has already settled the state; honour it now.
            if database.get(run_id)["state"] in {"cancelled", "cancel_requested"}:
                task.cancel()
            return await task
        except asyncio.CancelledError:
            if database.get(run_id)["state"] in {"cancelled", "cancel_requested", "failed"}:
                return None
            raise RuntimeError("UNEXPECTED_PROVIDER_CANCELLATION") from None
        finally:
            with job_lock:
                live_tasks.pop(run_id, None)

    bakeoffs = BakeoffStore(database)
    bakeoff_lock = threading.Lock()  # one scheduler pass at a time (background loop and launch requests)
    BAKEOFF_CONCURRENCY = 6  # leaves executor room for manual sessions and single runs

    def start_live_run(config, request_key):
        """Queue one live catalogue run, as POST /api/v1/runs does for a single repetition."""
        body = RunConfig(**config)
        if not live_providers.availability(body.provider_config_id, caller=body.caller.model_dump())[0]:
            raise ValueError("PROVIDER_NOT_CONFIGURED")
        run_id = live_engine.create(body.model_dump(), request_key)
        with job_lock:
            if database.get(run_id)["state"] == "queued" and run_id not in jobs:
                job_links[run_id] = telemetry_module.capture_link()
                jobs[run_id] = executor.submit(contextvars.copy_context().run, work, run_id)
        return run_id

    def bakeoff_capacity():
        with job_lock:
            return BAKEOFF_CONCURRENCY - sum(not job.done() for job in jobs.values())

    def bakeoff_tick():
        if not app.state.accepting:
            return 0
        with bakeoff_lock:
            return bakeoff_step(bakeoffs, database, start_live_run, capacity=bakeoff_capacity)

    def stop_live(run_id):
        with job_lock:
            entry = live_tasks.get(run_id)
        if entry is not None:
            loop, task = entry
            loop.call_soon_threadsafe(task.cancel)

    def run_live_work(run_id, config):
        """A live adapter run: provider failures are recorded in the ledger, never silently completed."""
        summary = asyncio.run(live_task(run_id, config))
        if summary is None:  # cancelled: Engine.cancel already settled the ledger and discarded held audio
            return
        outcomes = {t["strict_speech_approval"] for t in summary["turns"]}
        if summary.get("caller_failure") or summary.get("setup_failure"):
            failure = summary.get("caller_failure") or summary["setup_failure"]
        elif "turn_timeout" in outcomes:
            failure = "TURN_TIMEOUT"
        elif outcomes & {"provider_error", "stream_ended"} or summary["adapter_errors"]:
            failure = "PROVIDER_FAILURE"
        else:
            failure = None
        evaluate_live(run_id, config, failure)

    def run_manual_work(run_id, config):
        """A human-driven session over the page's WebSocket, then the same assessment as a live run."""
        bridge = manual_bridges.get(run_id)
        if bridge is None:
            raise RuntimeError("MANUAL_SESSION_NOT_ATTACHED")
        summary = asyncio.run(live_task(run_id, config, live_providers.run_manual(manual_engine, run_id, config, bridge)))
        if summary is None:
            return
        bridge.emit({"type": "status", "stage": "evaluating"})
        evaluate_live(run_id, config, "PROVIDER_FAILURE" if summary["fatal"] else None)

    def evaluate_live(run_id, config, failure):
        with database.transaction(run_id) as tx:
            if tx.state["state"] != "running":
                return
            if failure:
                tx.state["state"] = "failed"
                tx.event("orchestrator", "run.failed", {"reason": failure})
                return
            # The terminal state is written once, after the assessment: a failure while assessing is `failed`,
            # never `completed` followed by `failed`.
            tx.state["state"] = "evaluating"
            tx.event("orchestrator", "run.evaluating", {})
        # Deterministic rubric checks always run. Judges named in HARNESS_AUTO_JUDGES run too (the assessment
        # decides eligibility against the tested model); a judge that cannot start is logged and the criteria
        # that need one stay untestable. Any assessment failure fails the run closed.
        backends, unavailable = live_providers.auto_judges()
        for entry in unavailable:
            logging.getLogger("voice_harness.run").warning("auto judge unavailable: %s", entry)
        result = judge_service.assess_run(
            database, run_id, tested=live_providers.TESTED_MODELS[config["provider_config_id"]], backends=backends,
            while_evaluating=True,
        )
        assurance = {
            "model_behaviour": result["model_behaviour"],
            "platform_enforcement": result["platform_enforcement"],
            "overall_assurance": result["overall_assurance"],
        }
        with database.transaction(run_id) as tx:
            if tx.state["state"] != "evaluating":  # cancelled while assessing
                return
            tx.state["assurance"] = assurance
            tx.state["state"] = "completed"
            tx.event("orchestrator", "run.completed", {"assurance": assurance})

    @asynccontextmanager
    async def lifespan(app):
        if recover:
            for run_id in engine.recover():
                evidence.save_manifest(run_id)

        async def retention():
            cursor = ""
            while True:
                await asyncio.sleep(30)

                def batch(after=cursor):
                    with database.connection() as db:
                        return [
                            row["run_id"]
                            for row in db.execute(
                                "SELECT run_id FROM harness_runs WHERE run_id>? ORDER BY run_id LIMIT 100",
                                (after,),
                            ).fetchall()
                        ]

                ids = await asyncio.to_thread(batch)
                for run_id in ids:
                    await asyncio.to_thread(evidence.purge, run_id)
                    await asyncio.to_thread(evidence.purge_metadata, run_id)
                cursor = ids[-1] if ids else ""

        async def bakeoff_scheduler():
            while True:
                await asyncio.sleep(3)
                try:
                    await asyncio.to_thread(bakeoff_tick)
                except Exception:  # noqa: BLE001 -- a failed pass is logged and retried on the next one
                    logging.getLogger("voice_harness.bakeoff").exception("bake-off scheduler pass failed")

        maintenance = asyncio.create_task(retention())
        scheduler = asyncio.create_task(bakeoff_scheduler())
        yield
        app.state.accepting = False  # stop intake before anything else
        maintenance.cancel()
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler
        with suppress(asyncio.CancelledError):
            await maintenance
        await asyncio.to_thread(
            graceful_shutdown, engine, evidence, database, executor
        )
        telemetry.shutdown()
        telemetry_module.uninstall(telemetry)

    app = FastAPI(
        title="Voice Harness Phase 1",
        version="0.3",
        # Spans come from origin_policy below; FastAPI's native OTel would duplicate them.
        telemetry={"tracing": False, "metrics": False, "logs": False},
        lifespan=lifespan,
    )
    app.state.engine, app.state.evidence = engine, evidence
    app.state.jobs = jobs
    app.state.accepting = True
    client_assets = Path(__file__).resolve().parents[2] / "apps" / "web"
    if client_assets.exists():
        app.mount(
            "/client", StaticFiles(directory=client_assets), name="playback-client"
        )
    origins = {
        "http://localhost:8765",
        "http://127.0.0.1:8765",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    }
    if os.environ.get("HARNESS_PORT", "").isdigit():  # the page is same-origin on whatever port the server runs on
        origins |= {f"http://localhost:{os.environ['HARNESS_PORT']}", f"http://127.0.0.1:{os.environ['HARNESS_PORT']}"}

    def auth(request: Request, authorization: str | None = Header(default=None)):
        role = role_for(authorization, token, reviewer_token)
        if role is None:
            raise HTTPException(401, "AUTH_REQUIRED")
        if role == "reviewer" and not reviewer_may(request.method, request.url.path):
            raise HTTPException(403, "FORBIDDEN")
        request.state.role = role

    @app.middleware("http")
    async def origin_policy(request: Request, call_next):
        request.state.request_id = secrets.token_hex(8)
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return error_response(request, "ORIGIN_DENIED", 403)
        with tracer.start_as_current_span(request.method + " " + request.url.path):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            return response

    def error_response(request, code, status, retryable=False):
        return JSONResponse(
            {
                "error": {
                    "code": code,
                    "message": code,
                    "retryable": retryable,
                    "request_id": getattr(request.state, "request_id", "unknown"),
                    "details": {},
                }
            },
            status_code=status,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(request, str(error.detail), error.status_code)

    @app.exception_handler(RequestValidationError)
    async def schema_error(request, error):
        return error_response(request, "INVALID_SCHEMA", 422)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        code = str(error)
        return error_response(request, code, 409)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return error_response(request, "NOT_FOUND", 404)

    @app.exception_handler(OSError)
    async def dependency(request, error):
        return error_response(request, "EVIDENCE_UNAVAILABLE", 503, True)

    @app.get("/health")
    def health():
        return {"status": "ok", "schema_version": "0.3"}

    @app.get("/api/v1/providers", dependencies=[Depends(auth)])
    def providers():
        return summarize(build_registry())

    @app.get("/api/v1/providers/export", dependencies=[Depends(auth)])
    def providers_export():
        return {"export_type": "provider_coverage", **summarize(build_registry())}

    def stored_scenarios():
        with database.connection() as db:
            rows = db.execute(
                "SELECT scenario_id, version, content_hash, definition FROM harness_scenario_definitions ORDER BY scenario_id, version"
            ).fetchall()
        return {
            (r["scenario_id"], r["version"]): (
                r["content_hash"],
                database.decoded(r["definition"]),
            )
            for r in rows
        }

    @app.get("/api/v1/live-providers", dependencies=[Depends(auth)])
    def live_provider_configs():
        """Which live provider configs this deployment can run; reasons are codes, never credentials."""
        items = []
        for provider_config_id in live_providers.LIVE_PROVIDERS:
            missing = live_providers.requirements(provider_config_id)
            items.append({"provider_config_id": provider_config_id, "available": not missing,
                          "reason": missing[0] if missing else None, "reasons": missing,
                          "caller_modes": {mode: {"available": not reasons, "reasons": reasons}
                                           for mode in ("fixed", "live_ai")
                                           for reasons in [live_providers.requirements(provider_config_id, caller={"mode": mode})]},
                          "fixed_backends": {backend: {"available": not reasons, "reasons": reasons}
                                             for backend in ("system", "macos", "openai_tts")
                                             for reasons in [live_providers.requirements(provider_config_id, caller={"fixed_backend": backend})]}})
        backends, unavailable = live_providers.auto_judges()
        judges = [{"backend": b.config.provider, "model_id": b.config.model_id, "available": True} for b in backends]
        judges += [{"backend": u["backend"], "available": False, "reason": u["reason"]} for u in unavailable]
        from .interactive_caller import MODEL, PERSONAS
        from .interactive_caller import SCENARIOS as CALLER_SCENARIOS

        return {"items": items, "auto_judges": judges,
                "interactive_caller": {"scenario_ids": list(CALLER_SCENARIOS), "personas": list(PERSONAS),
                                       "provider": "openai", "model_id": MODEL, "delivery_mode": "gated"}}

    @app.get("/api/v1/scenarios", dependencies=[Depends(auth)])
    def scenarios(cursor: int = 0, limit: int = 100):
        if cursor < 0 or not 1 <= limit <= 1000:
            raise HTTPException(422, "INVALID_PAGINATION")
        merged = {(d["id"], d["version"]): (definition_hash(d), d) for d in SCENARIOS.values()}
        merged.update(stored_scenarios())
        merged.update({(e["scenario_id"], 1): (definition_hash(e["definition"]), e["definition"]) for e in CATALOGUE.values()})
        items = [
            {
                "scenario_id": key[0],
                "version": key[1],
                "content_hash": value[0],
                "runnable": (key[0] in SCENARIOS or key[0] in CATALOGUE) and key[1] == 1,
                "execution": "live_adapter" if key[0] in CATALOGUE else "mock",
                **({"faults": CATALOGUE[key[0]]["faults"], "mechanics": CATALOGUE[key[0]]["mechanics"]}
                   if key[0] in CATALOGUE else {}),
                "presentation": presentation(key[0]),
                "definition": value[1],
            }
            for key, value in sorted(merged.items())
        ]
        page = items[cursor : cursor + limit]
        more = cursor + limit < len(items)
        return {"items": page, "next_cursor": cursor + limit if more else None}

    @app.post("/api/v1/scenarios", status_code=201, dependencies=[Depends(auth)])
    def create_scenario(definition: ScenarioDefinition):
        body = definition.model_dump()
        content_hash = definition_hash(body)
        key = (body["id"], body["version"])
        builtin = SCENARIOS.get(body["id"])
        existing = (
            (definition_hash(builtin), builtin)
            if builtin and builtin["version"] == body["version"]
            else stored_scenarios().get(key)
        )
        if existing:
            if existing[0] != content_hash:
                raise ValueError("SCENARIO_VERSION_CONFLICT")
            return {"scenario_id": key[0], "version": key[1], "content_hash": content_hash, "created": False}
        with database.connection() as db:
            db.execute(
                "INSERT INTO harness_scenario_definitions VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (key[0], key[1], content_hash, database.encoded(body)),
            )
        winner = stored_scenarios().get(key)  # a concurrent writer may have inserted different content first
        if winner is None or winner[0] != content_hash:
            raise ValueError("SCENARIO_VERSION_CONFLICT")
        return {"scenario_id": key[0], "version": key[1], "content_hash": content_hash, "created": True}

    @app.post("/api/v1/runs", status_code=202, dependencies=[Depends(auth)])
    def launch(
        config: RunConfig, idempotency_key: str = Header(min_length=1, max_length=200)
    ):
        if not app.state.accepting:
            raise HTTPException(503, "SHUTTING_DOWN")
        is_live = config.provider_config_id in live_providers.LIVE_PROVIDERS
        if is_live != (config.scenario_id in CATALOGUE):
            raise ValueError("SCENARIO_PROVIDER_MISMATCH")
        if not is_live and config.scenario_id not in SCENARIOS:
            raise HTTPException(422, "UNKNOWN_SCENARIO")
        if is_live and not live_providers.availability(config.provider_config_id, caller=config.caller.model_dump())[0]:
            raise ValueError("PROVIDER_NOT_CONFIGURED")
        run_ids = []
        with job_lock:
            with database.connection() as db:
                awaiting = db.execute(
                    "SELECT COUNT(*) AS count FROM harness_runs WHERE state='awaiting_playback'"
                ).fetchone()["count"]
            if config.repetitions > 10 - awaiting - sum(
                not job.done() for job in jobs.values()
            ):
                raise HTTPException(429, "CONCURRENCY_LIMIT")
        for repetition in range(config.repetitions):
            with job_lock:
                for old_id in [key for key, job in jobs.items() if job.done()]:
                    del jobs[old_id]
                if sum(not job.done() for job in jobs.values()) >= 10:
                    raise HTTPException(429, "CONCURRENCY_LIMIT")
                run_id = (live_engine if is_live else engine).create(
                    config.model_dump(), idempotency_key + ":" + str(repetition)
                )
                if database.get(run_id)["state"] == "queued" and run_id not in jobs:
                    job_links[run_id] = telemetry_module.capture_link()
                    jobs[run_id] = executor.submit(
                        contextvars.copy_context().run, work, run_id
                    )
            run_ids.append(run_id)
        return {"run_ids": run_ids, "state": "queued", "schema_version": "0.3"}

    def metric_summary(run_id, events):
        observations = derive_metrics(events)
        return {
            "status": "derived_from_ledger",
            "detail": "/api/v1/metrics/" + run_id,
            "values": {o["metric"]: o["value"] for o in observations if o["value"] is not None},
            "unavailable": {
                o["metric"]: o["exclusion_reason"] for o in observations if o["value"] is None
            },
        }

    @app.get("/api/v1/runs", dependencies=[Depends(auth)])
    def list_runs(cursor: int = 0, limit: int = 50):
        """Operator list, newest first; identifiers come from indexed columns."""
        if cursor < 0 or not 1 <= limit <= 200:
            raise HTTPException(422, "INVALID_PAGINATION")
        with database.connection() as db:
            rows = db.execute(
                "SELECT r.run_id, r.state, r.session_id, r.scenario_id, r.scenario_version, r.policy_profile, r.provider_config_id, e.occurred_at AS started_at "
                "FROM harness_runs r LEFT JOIN harness_events e ON e.run_id=r.run_id AND e.sequence=1 "
                "ORDER BY e.occurred_at DESC, r.run_id LIMIT ? OFFSET ?",
                (limit + 1, cursor),
            ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {
            "items": items,
            "next_cursor": cursor + limit if len(rows) > limit else None,
        }

    @app.get("/api/v1/holds-register", dependencies=[Depends(auth)])
    def list_holds(state: str = "active", run_id: str | None = None, limit: int = 200):
        """Cross-run hold register for review: active (default), released or all."""
        if state not in {"active", "released", "all"} or not 1 <= limit <= 1000:
            raise HTTPException(422, "INVALID_FILTER")
        clauses, params = [], []
        if state == "active":
            clauses.append("released_at IS NULL")
        elif state == "released":
            clauses.append("released_at IS NOT NULL")
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        with database.connection() as db:
            rows = db.execute(
                "SELECT hold_id, run_id, owner_id, requester_id, scope_type, scope, access_classification, created_at, expires_at, released_at, release_reason, audit_event_id "
                "FROM harness_preservation_holds"
                + (" WHERE " + " AND ".join(clauses) if clauses else "")
                + " ORDER BY expires_at, hold_id LIMIT ?",
                (*params, limit),
            ).fetchall()
        now = time.time()
        items = []
        for row in rows:
            item = dict(row)
            scope = database.decoded(item.pop("scope"))
            item["finding_ids"] = scope.get("finding_ids", [])
            item["expired"] = item["released_at"] is None and item["expires_at"] <= now
            items.append(item)
        return {"items": items, "server_time": now}

    @app.get("/api/v1/runs/{run_id}", dependencies=[Depends(auth)])
    def get_run(run_id: str):
        state = database.get(run_id)
        events = database.events(run_id)
        terminal = state["state"] in {"completed", "cancelled", "failed"}
        reason = next(
            (
                e["content"].get("reason", e["type"].removeprefix("run."))
                for e in reversed(events)
                if e["type"] in {"run.completed", "run.cancelled", "run.failed"}
            ),
            None,
        )
        artifacts = state["artifacts"].values()
        return {
            "run_id": run_id,
            "state": state["state"],
            "session_id": state["session_id"],
            "assurance": state["assurance"],
            "config": state["config"],
            "scenario": {
                "scenario_id": state["config"].get("scenario_id"),
                "version": state["config"].get("scenario_version"),
            },
            "provider_config_id": state["config"].get("provider_config_id"),
            "capability_status": "mock_only",
            "capability_report_id": "mock-fixture-v1",
            "started_at": events[0]["occurred_at"] if events else None,
            "ended_at": events[-1]["occurred_at"] if terminal and events else None,
            "termination_reason": reason if terminal else None,
            "metric_summary": metric_summary(run_id, events),
            "evidence": {
                "event_count": len(events),
                "artifacts_total": len(state["artifacts"]),
                "artifacts_available": sum(a["available"] for a in artifacts),
                "manifest": "signed_ed25519" if evidence.signer else "unsigned_local",
            },
        }

    @app.post(
        "/api/v1/runs/{run_id}/cancel", status_code=202, dependencies=[Depends(auth)]
    )
    def cancel(run_id: str, body: CancelRun):
        state = engine.cancel(run_id, body.reason)
        stop_live(run_id)  # closes the provider session; the worker re-saves the manifest when it unwinds
        evidence.save_manifest(run_id)
        return {"state": state}

    @app.get("/api/v1/runs/{run_id}/events", dependencies=[Depends(auth)])
    def events(run_id: str, cursor: int = 0, limit: int = 100):
        if cursor < 0 or not 1 <= limit <= 1000:
            raise HTTPException(422, "INVALID_PAGINATION")
        database.get(run_id)
        values = database.events(run_id, after=cursor, limit=limit + 1)
        return {
            "items": values[:limit],
            "next_cursor": values[limit - 1]["sequence"]
            if len(values) > limit
            else None,
        }

    @app.get("/api/v1/metrics/{run_id}", dependencies=[Depends(auth)])
    def run_metrics(run_id: str):
        database.get(run_id)
        return {
            "run_id": run_id,
            "schema_version": "0.3",
            "observations": derive_metrics(database.events(run_id)),
        }

    @app.get("/api/v1/signing/public-keys", dependencies=[Depends(auth)])
    def signing_public_keys():
        return keystore.public_keys()

    @app.get("/api/v1/signing/key-history", dependencies=[Depends(auth)])
    def signing_key_history():
        return {"items": keystore.history()}

    def operator_only(request: Request):
        if request.state.role != "operator":
            raise HTTPException(403, "FORBIDDEN")

    @app.post("/api/v1/signing/rotate", dependencies=[Depends(auth)])
    def signing_rotate(request: Request, body: dict | None = None):
        operator_only(request)
        reason = str((body or {}).get("reason") or "operator rotation")[:200]
        keystore.rotate(reason)
        evidence.signer = keystore.signer()
        return keystore.public_keys()

    @app.post("/api/v1/signing/revoke", dependencies=[Depends(auth)])
    def signing_revoke(request: Request, body: dict):
        operator_only(request)
        try:
            keystore.revoke(str(body.get("key_id")), str(body.get("reason") or "operator revocation")[:200])
        except SigningError as error:
            raise HTTPException(404, str(error)) from error
        evidence.signer = keystore.signer()
        return keystore.public_keys()

    @app.get("/api/v1/runs/{run_id}/evidence", dependencies=[Depends(auth)])
    def bundle(request: Request, run_id: str, view: str = "full"):
        result = evidence.bundle(run_id)
        if request.state.role == "reviewer":
            result = restrict_bundle(result)
        links = download_links(run_id)
        if view == "summary":
            # Manifest and artifact availability only; the bundle is fetched via the link.
            return {
                "manifest": result["manifest"],
                "manifest_hash": result["manifest_hash"],
                "artifact_availability": {
                    a["sha256"]: a["available"] for a in result["artifacts"]
                },
                "download_links": links,
            }
        return {**result, "download_links": links}

    @app.post(
        "/api/v1/runs/{run_id}/preservation-holds",
        status_code=201,
        dependencies=[Depends(auth)],
    )
    def hold(run_id: str, body: HoldRequest):
        return evidence.hold(
            run_id,
            body.reason,
            body.owner_id,
            body.expires_at,
            body.idempotency_key,
            finding_ids=body.finding_ids,
        )

    @app.post(
        "/api/v1/runs/{run_id}/preservation-holds/{hold_id}/release",
        dependencies=[Depends(auth)],
    )
    def release_hold(run_id: str, hold_id: str, body: CancelRun):
        return {
            "state": "released",
            **evidence.release(run_id, hold_id, body.reason),
        }

    @app.post(
        "/api/v1/runs/{run_id}/preservation-holds/{hold_id}/renew",
        dependencies=[Depends(auth)],
    )
    def renew_hold(run_id: str, hold_id: str, body: RenewHold):
        return evidence.renew(run_id, hold_id, body.expires_at, body.reason)

    register_review_routes(app, database, evidence, auth)
    register_cost_routes(app, database, auth)
    from .judges.api import register_judge_routes

    register_judge_routes(app, database, auth)
    from .comparison_api import register_comparison_routes

    register_comparison_routes(app, database, auth)
    app.state.bakeoff_tick = bakeoff_tick

    def bakeoff_payload(bakeoff_id):
        try:
            return bakeoffs.get(bakeoff_id)
        except KeyError:
            raise HTTPException(404, "BAKEOFF_NOT_FOUND") from None

    @app.post("/api/v1/bakeoffs", status_code=201, dependencies=[Depends(auth)])
    def create_bakeoff(body: BakeoffRequest):
        """Launch a bake-off (scenarios x providers x repetitions), or wrap finished runs (``runs``)."""
        if not app.state.accepting:
            raise HTTPException(503, "SHUTTING_DOWN")
        for provider_config_id in body.provider_config_ids:
            if not live_providers.availability(provider_config_id)[0]:
                raise ValueError("PROVIDER_NOT_CONFIGURED:" + provider_config_id)
        try:
            payload = bakeoffs.create(body)
        except KeyError:
            raise HTTPException(404, "RUN_NOT_FOUND") from None
        bakeoff_tick()  # start the first runs (or settle imported ones) now rather than on the next pass
        return bakeoff_summary(bakeoffs.get(payload["bakeoff_id"]))

    @app.get("/api/v1/bakeoffs", dependencies=[Depends(auth)])
    def list_bakeoffs():
        return {"items": bakeoffs.list()}

    @app.get("/api/v1/bakeoffs/{bakeoff_id}", dependencies=[Depends(auth)])
    def get_bakeoff(bakeoff_id: str):
        payload = bakeoff_payload(bakeoff_id)
        cells = [{"scenario_id": c["scenario_id"], "provider_config_id": c["provider_config_id"], "repetition": c["repetition"],
                  "done": c.get("done", False), "attempts": [{k: a.get(k) for k in ("run_id", "state", "failure")}
                                                             for a in c["attempts"] if not a.get("pending")]}
                 for c in payload["cells"]]
        return {"bakeoff": bakeoff_summary(payload), "weights": payload["weights"], "cells": cells,
                "data": bakeoff_report_data(payload, source=bakeoff_id)}

    @app.post("/api/v1/bakeoffs/{bakeoff_id}/report", dependencies=[Depends(auth)])
    def bakeoff_what_if(bakeoff_id: str, weights: Weights):
        """The report recomputed with other weights; nothing is stored."""
        payload = bakeoff_payload(bakeoff_id)
        return {"data": bakeoff_report_data(payload, weights=weights.model_dump(), source=bakeoff_id)}

    @app.get("/api/v1/bakeoffs/{bakeoff_id}/report.html", dependencies=[Depends(auth)])
    def bakeoff_export(bakeoff_id: str):
        """A self-contained, interactive HTML report to share (data embedded; prints to PDF)."""
        payload = bakeoff_payload(bakeoff_id)
        filename = "bakeoff-" + bakeoff_id + ".html"
        return HTMLResponse(render_bakeoff(payload, source=bakeoff_id),
                            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/v1/bakeoffs/{bakeoff_id}/cancel", dependencies=[Depends(auth)])
    def cancel_bakeoff(bakeoff_id: str):
        """Stop launching, cancel runs in flight; finished runs stay in the report."""
        with bakeoff_lock:
            payload = bakeoff_payload(bakeoff_id)
            if payload["state"] != "running":
                raise ValueError("BAKEOFF_NOT_RUNNING")
            for cell in payload["cells"]:
                latest = cell["attempts"][-1] if cell["attempts"] else None
                if latest and latest.get("run_id") and not cell.get("done"):
                    with suppress(ValueError, KeyError):  # already terminal
                        engine.cancel(latest["run_id"], "BAKEOFF_CANCELLED")
                        stop_live(latest["run_id"])
                cell["attempts"] = [a for a in cell["attempts"] if not a.get("pending")]
            payload["state"] = "cancelled"
            bakeoffs.save(payload)
        return bakeoff_summary(payload)

    @app.post("/api/v1/runs/{run_id}/ws-ticket", dependencies=[Depends(auth)])
    def ws_ticket(run_id: str):
        database.get(run_id)
        with job_lock:
            expired = [k for k, v in tickets.items() if v["expires_at"] <= time.time()]
            for key in expired:
                del tickets[key]
            if len(tickets) >= 1000:
                raise HTTPException(429, "TICKET_LIMIT")
            ticket_id = secrets.token_urlsafe(32)
            tickets[ticket_id] = {"run_id": run_id, "expires_at": time.time() + 30}
        return {"ticket": ticket_id, "expires_in_seconds": 30}

    def ws_protocols(socket):
        return [p.strip() for p in socket.headers.get("sec-websocket-protocol", "").split(",")]

    def ws_admitted(socket, run_id):
        """Bearer token or a single-use ticket for this run, from an allowed origin."""
        ticket_id = next((p[7:] for p in ws_protocols(socket) if p.startswith("ticket.")), None)
        with job_lock:
            ticket = tickets.pop(ticket_id, None) if ticket_id else None
        ticket_ok = ticket and ticket["run_id"] == run_id and ticket["expires_at"] > time.time()
        return bool(bearer_matches(socket.headers.get("authorization", ""), token) or ticket_ok) and (
            socket.headers.get("origin") in origins)

    @app.get("/api/v1/manual/journeys", dependencies=[Depends(auth)])
    def manual_journey_list():
        """Journeys for manual testing (happy and failure path, with prompts) and which providers can run them."""
        providers = []
        for provider_config_id in live_providers.LIVE_PROVIDERS:
            missing = live_providers.requirements(provider_config_id, manual=True)
            providers.append({"provider_config_id": provider_config_id, "available": not missing,
                              "reason": missing[0] if missing else None, "reasons": missing})
        return {"items": manual_journeys(), "providers": providers, "playback_modes": ["gated", "streamed"]}

    manual_pending = {}  # run_id -> created_at for sessions whose page has not connected yet

    @app.post("/api/v1/manual-sessions", status_code=201, dependencies=[Depends(auth)])
    def create_manual_session(
        body: ManualSessionConfig, idempotency_key: str = Header(min_length=1, max_length=200)
    ):
        if not app.state.accepting:
            raise HTTPException(503, "SHUTTING_DOWN")
        scenario_id = manual_scenario(body.journey_id, body.path)
        if not live_providers.availability(body.provider_config_id, manual=True)[0]:
            raise ValueError("PROVIDER_NOT_CONFIGURED")
        with job_lock:
            for stale in [r for r, at in manual_pending.items() if at < time.time() - 120]:
                del manual_pending[stale]
                if database.get(stale)["state"] == "queued":  # the page never connected
                    manual_engine.cancel(stale, "MANUAL_SESSION_NOT_STARTED")
            if sum(not job.done() for job in jobs.values()) + len(manual_pending) >= 10:
                raise HTTPException(429, "CONCURRENCY_LIMIT")
        config = {
            **RunConfig(scenario_id=scenario_id, provider_config_id=body.provider_config_id).model_dump(),
            "input_mode": "manual", "client_mode": "manual_browser", "playback_mode": body.playback_mode,
            "journey_id": body.journey_id, "path": body.path,
        }
        run_id = manual_engine.create(config, "manual:" + idempotency_key)
        with job_lock:
            if database.get(run_id)["state"] == "queued" and run_id not in jobs:
                manual_pending[run_id] = time.time()
        return {"run_id": run_id, "scenario_id": scenario_id, "state": database.get(run_id)["state"],
                "websocket": "/api/v1/manual-sessions/" + run_id + "/ws", "schema_version": "0.3"}

    @app.websocket("/api/v1/manual-sessions/{run_id}/ws")
    async def manual_websocket(socket: WebSocket, run_id: str):
        if not ws_admitted(socket, run_id):
            await socket.close(code=1008)
            return
        try:
            state = await asyncio.to_thread(database.get, run_id)
        except KeyError:
            await socket.close(code=1008)
            return
        with job_lock:
            startable = (state["config"].get("input_mode") == "manual" and state["state"] == "queued"
                         and run_id not in jobs and run_id not in manual_bridges)
            if startable:
                bridge = manual_bridges[run_id] = Bridge(asyncio.get_running_loop())
                manual_pending.pop(run_id, None)
        if not startable:  # one page per session; a finished or running session cannot be re-attached
            await socket.close(code=1008)
            return
        await socket.accept(subprotocol="manual.v1" if "manual.v1" in ws_protocols(socket) else None)
        job_links[run_id] = telemetry_module.capture_link()
        with job_lock:
            job = jobs[run_id] = executor.submit(contextvars.copy_context().run, work, run_id)
        finished = asyncio.wrap_future(job)

        async def read():
            try:
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        bridge.deliver(("audio", message["bytes"]))
                        continue
                    text = message.get("text") or ""
                    try:
                        payload = json.loads(text) if len(text) <= 8000 else None
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict):
                        bridge.deliver(("message", payload))
                    else:
                        await socket.send_json({"type": "error", "code": "INVALID_MESSAGE"})
            except (WebSocketDisconnect, RuntimeError):
                pass
            bridge.deliver(("disconnect", None))

        async def write():
            while True:
                await socket.send_json(await bridge.outbound.get())

        reader, writer = asyncio.create_task(read()), asyncio.create_task(write())
        try:
            with suppress(Exception):  # the worker records its own failure in the ledger
                await finished
        finally:
            reader.cancel()
            while not bridge.outbound.empty():
                await asyncio.sleep(0.05)  # let queued feedback reach the page before the final message
                if writer.done():
                    break
            writer.cancel()
            await asyncio.gather(reader, writer, return_exceptions=True)
            with job_lock:
                manual_bridges.pop(run_id, None)
        final = await asyncio.to_thread(database.get, run_id)
        with suppress(Exception):  # the page may already have gone
            await socket.send_json({"type": "ended", "state": final["state"], "assurance": final.get("assurance")})
            await socket.close()

    @app.websocket("/api/v1/runs/{run_id}/ws")
    async def websocket(socket: WebSocket, run_id: str):
        protocols = ws_protocols(socket)
        if not ws_admitted(socket, run_id):
            await socket.close(code=1008)
            return
        try:
            state = await asyncio.to_thread(database.get, run_id)
        except KeyError:
            await socket.close(code=1008)
            return
        await socket.accept(
            subprotocol="harness.v1" if "harness.v1" in protocols else None
        )
        with database.transaction(run_id) as tx:
            tx.state["connection_epoch"] += 1
            epoch = tx.state["connection_epoch"]
            if epoch > 1:
                tx.state["generation_epoch"] += 1
                tx.state["tokens"].clear()
                for unit in tx.state["units"].values():
                    if unit["state"] in {"pending", "approved", "scheduled"}:
                        unit["state"] = "discarded"
                        unit.pop("pcm", None)
                tx.event(
                    "orchestrator",
                    "session.interrupted",
                    {
                        "generation_epoch": tx.state["generation_epoch"],
                        "reason": "RECONNECT",
                    },
                )
            tx.event(
                "orchestrator",
                "session.connected",
                {"connection_epoch": epoch, "clock_sync": "required"},
            )
        state = database.get(run_id)
        seen, sequence, samples = set(), 0, 0
        await socket.send_json(
            {
                "type": "start",
                "session_id": state["session_id"],
                "connection_epoch": epoch,
                "generation_epoch": state["generation_epoch"],
                "approval_public_key": public_key(state["isolation_key"]),
                "media_profile": "pcm24k-mono-v1",
                "max_bytes": engine.MAX_AUDIO_BYTES,
                "limits": engine.limits,
            }
        )
        wire_cursor = max((e["sequence"] for e in database.events(run_id)), default=0)
        last_activity = last_ping = time.monotonic()
        inbound = InboundQueue(engine.limits["inbound_queue_seconds"])
        inbound.start(socket)

        def record_clock(kind, content):
            with database.transaction(run_id) as tx:
                tx.event("orchestrator", kind, content)

        clock = ClockSyncSession(epoch, record_clock)
        if epoch > 1:
            await asyncio.to_thread(invalidate_prior_connection, record_clock, epoch)
        try:
            while True:
                changes = await asyncio.to_thread(
                    database.events, run_id, wire_cursor, 100
                )
                for event in changes:
                    wire_cursor = event["sequence"]
                    if event["type"] == "transcript.revised":
                        await socket.send_json(
                            {
                                "type": "playback.revoke",
                                "unit_id": event["content"]["unit_id"],
                                "assessment_epoch": event["content"][
                                    "assessment_epoch"
                                ],
                            }
                        )
                    elif event["type"] in {
                        "session.interrupted",
                        "run.cancelled",
                        "run.recovery_required",
                    }:
                        await socket.send_json(
                            {
                                "type": "playback.stopped",
                                "generation_epoch": event["content"][
                                    "generation_epoch"
                                ],
                            }
                        )
                current = await asyncio.to_thread(database.get, run_id)
                if current["config"].get("client_mode") == "websocket":
                    for approval in current["tokens"].values():
                        pcm = await asyncio.to_thread(
                            engine.release_audio, run_id, approval
                        )
                        await socket.send_json(
                            {
                                "type": "audio.approved",
                                "token": approval,
                                "audio": base64.b64encode(pcm).decode(),
                            }
                        )
                await asyncio.to_thread(clock.expire_if_stale)
                if time.monotonic() - last_activity > 30:
                    raise ValueError("IDLE_TIMEOUT")
                if time.monotonic() - last_ping > 10:
                    await socket.send_json({"type": "ping"})
                    last_ping = time.monotonic()
                try:
                    message = await inbound.get(0.05)
                except TimeoutError:
                    continue
                last_activity = time.monotonic()
                required = {
                    "schema_version",
                    "message_id",
                    "session_id",
                    "connection_epoch",
                    "client_sequence",
                    "type",
                    "payload",
                }
                if (
                    set(message) != required
                    or message["schema_version"] != "0.3"
                    or message["session_id"] != state["session_id"]
                    or message["connection_epoch"] != epoch
                ):
                    raise ValueError("INVALID_CONTROL_MESSAGE")
                if message["message_id"] in seen:
                    await socket.send_json(
                        {"type": "duplicate", "message_id": message["message_id"]}
                    )
                    continue
                if len(seen) >= 10000:
                    raise ValueError("CONTROL_LIMIT_EXCEEDED")
                seen.add(message["message_id"])
                if message["client_sequence"] != sequence + 1:
                    await asyncio.to_thread(
                        _record_transport_error,
                        database,
                        run_id,
                        "audio.discontinuity",
                        {
                            "failure_code": "AUDIO_DISCONTINUITY",
                            "expected_client_sequence": sequence + 1,
                            "received_client_sequence": message["client_sequence"],
                            "strict_measurements": "invalidated",
                        },
                    )
                    raise ValueError("AUDIO_DISCONTINUITY")
                sequence += 1
                if message["type"] == "audio.input":
                    payload = message["payload"]
                    try:
                        AudioInputPayload.model_validate(payload)
                    except ValidationError:
                        raise ValueError("INVALID_AUDIO_FRAME") from None
                    raw = base64.b64decode(payload["bytes"], validate=True)
                    if (
                        payload.get("codec") != "pcm_s16le"
                        or payload.get("rate") != 24000
                        or payload.get("channels") != 1
                        or len(raw) != 960
                        or payload.get("start_sample") != samples
                        or payload.get("end_sample") != samples + 480
                        or payload["chunk_sequence"] != samples // 480 + 1
                    ):
                        raise ValueError("INVALID_AUDIO_FRAME")
                    samples += 480
                    await asyncio.to_thread(
                        _input_event,
                        database,
                        run_id,
                        samples,
                        raw,
                        epoch,
                        payload["stream_id"],
                    )
                    await socket.send_json(
                        {"type": "audio.accepted", "end_sample": samples}
                    )
                elif message["type"] in {"interrupt", "stop"}:
                    if message["type"] == "interrupt":
                        await asyncio.to_thread(engine.interrupt, run_id)
                    else:
                        await asyncio.to_thread(engine.cancel, run_id, "stop")
                    await socket.send_json({"type": "playback.stopped"})
                    break
                elif message["type"] == "playback.ack":
                    payload = message["payload"]
                    ack_event = await asyncio.to_thread(
                        engine.observe_playback,
                        run_id,
                        payload["unit_id"],
                        payload["end_sample"],
                    )
                    with database.transaction(run_id) as tx:
                        if tx.state["state"] == "awaiting_playback" and all(
                            u["state"] in {"blocked", "discarded"}
                            or u["observed_samples"] == u["end_sample"]
                            for u in tx.state["units"].values()
                        ):
                            model = tx.state["assurance"]["model_behaviour"]
                            platform = (
                                "fail"
                                if any(
                                    u.get("risk_mode") == "unreviewed_release"
                                    and not u.get("scripted_assessment_safe")
                                    for u in tx.state["units"].values()
                                )
                                else "pass"
                            )
                            tx.state["assurance"] = {
                                "model_behaviour": model,
                                "platform_enforcement": platform,
                                "overall_assurance": aggregate([model, platform]),
                            }
                            tx.event(
                                "judge",
                                "evaluation.playback_completed",
                                {
                                    "assurance": tx.state["assurance"],
                                    "evidence_event_ids": [ack_event],
                                },
                                causes=[ack_event],
                            )
                            tx.state["state"] = "completed"
                            tx.event(
                                "orchestrator",
                                "run.completed",
                                {
                                    "boundary": "client_audio_callback",
                                    "physical_audibility": "unknown",
                                },
                            )
                    evidence.save_manifest(run_id)
                    await socket.send_json({"type": "playback.acknowledged"})
                elif message["type"] == "pong":
                    pass
                elif message["type"] == "clock.ping":
                    received_ms = (inbound.last_received or time.monotonic()) * 1000
                    await socket.send_json(
                        clock.on_ping(message["payload"], received_ms)
                    )
                elif message["type"] == "clock.sample":
                    await asyncio.to_thread(clock.on_sample, message["payload"])
                elif message["type"] == "playback.stopped":
                    with database.transaction(run_id) as tx:
                        tx.event(
                            "playback",
                            "playback.client_stopped",
                            {
                                "ranges": message["payload"].get("ranges", []),
                                "clock_domain": "client_audio_worklet",
                                "physical_audibility": "unknown",
                            },
                        )
                else:
                    raise ValueError("UNSUPPORTED_CONTROL")
        except (TimeoutError, ValueError, KeyError) as error:
            code = str(error) or "IDLE_TIMEOUT"
            if code != "AUDIO_DISCONTINUITY":
                with suppress(Exception):
                    await asyncio.to_thread(
                        _record_transport_error,
                        database,
                        run_id,
                        "session.error",
                        {"failure_code": code},
                    )
            if code == "AUDIO_BACKPRESSURE":
                with suppress(Exception):
                    await asyncio.to_thread(engine.cancel, run_id, code)
            await socket.send_json({"type": "error", "code": code})
            await socket.close(code=1008)
        except WebSocketDisconnect:
            pass
        finally:
            inbound.close()
            with suppress(Exception):
                await asyncio.to_thread(clock.invalidate, "DISCONNECT")

    return app


def _record_transport_error(database, run_id, kind, content):
    with database.transaction(run_id) as tx:
        tx.event("orchestrator", kind, content)


def _input_event(database, run_id, samples, raw, epoch, stream_id):
    with database.transaction(run_id) as tx:
        if tx.state["connection_epoch"] != epoch:
            raise ValueError("STALE_CONNECTION_EPOCH")
        tx.event(
            "orchestrator",
            "audio.input",
            {
                "stream_id": stream_id,
                "chunk_sequence": samples // 480,
                "start_sample": samples - 480,
                "end_sample": samples,
                "byte_count": len(raw),
                "checksum": hashlib.sha256(raw).hexdigest(),
                "codec": "pcm_s16le",
                "rate": 24000,
                "channels": 1,
                "duration_ms": 20,
            },
        )


def serve():
    import uvicorn

    host = os.environ.get("HARNESS_BIND", "127.0.0.1")
    port = int(os.environ.get("HARNESS_PORT", "8000"))  # a host run beside the Compose API needs a different port
    tls = tls_options(host)  # refuses a non-loopback bind without TLS
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        ws_max_size=16384,
        **tls,
    )
