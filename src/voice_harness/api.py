"""Authenticated loopback control plane and bounded reference WebSocket framing."""

import asyncio
import base64
import contextvars
import hashlib
import hmac
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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .approvals import public_key
from .contracts import AudioInputPayload, CancelRun, HoldRequest, RenewHold, RunConfig
from .coverage import default_registry
from .engine import Engine
from .evidence import Evidence
from .scenarios import run_scenario
from .storage import Database
from .verdicts import aggregate


def create_app(database=None, root=None, token=None, recover=True):
    root = Path(root or os.environ.get("HARNESS_DATA", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    token_file = root / "operator-token"
    if token is None:
        if not token_file.exists():
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(32))
        token = token_file.read_text().strip()
    database = database or Database(
        os.environ.get("HARNESS_DATABASE_URL", str(root / "development.sqlite"))
    )
    engine, evidence = Engine(database), Evidence(database, root / "objects")
    from .telemetry import provider

    traces = provider()
    tracer = traces.get_tracer("voice-harness.control")
    jobs, job_lock = {}, threading.Lock()
    tickets = {}
    executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="mock-run")

    def work(run_id):
        try:
            with database.transaction(run_id) as tx:
                if tx.state["state"] != "queued":
                    return
                tx.state["state"] = "running"
                tx.event("orchestrator", "run.running", {})
            run_scenario(engine, run_id, evidence)
        except Exception:  # noqa: BLE001 -- protected worker boundary fails closed
            with database.transaction(run_id) as tx:
                if tx.state["state"] not in {"cancelled", "recovery_required"}:
                    tx.state["state"] = "failed"
                    tx.event("orchestrator", "run.failed", {"reason": "WORKER_FAILED"})
        finally:
            evidence.save_manifest(run_id)

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
                cursor = ids[-1] if ids else ""

        maintenance = asyncio.create_task(retention())
        yield
        maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance
        executor.shutdown(wait=True, cancel_futures=False)
        traces.shutdown()

    app = FastAPI(
        title="Voice Harness Phase 1",
        version="0.3",
        openapi_version="3.1.0",
        lifespan=lifespan,
    )
    app.state.engine, app.state.evidence = engine, evidence
    app.state.jobs = jobs
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

    def auth(authorization: str | None = Header(default=None)):
        if not authorization or not hmac.compare_digest(
            authorization, "Bearer " + token
        ):
            raise HTTPException(401, "AUTH_REQUIRED")

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
        return {"items": [c.to_dict() for c in default_registry()], "verified_count": 0}

    @app.get("/api/v1/scenarios", dependencies=[Depends(auth)])
    def scenarios():
        return {
            "items": [
                {"scenario_id": s, "version": 1}
                for s in [
                    "identity-bypass-001",
                    "balance-verified-001",
                    "unsafe-disclosure-001",
                ]
            ]
        }

    @app.post("/api/v1/runs", status_code=202, dependencies=[Depends(auth)])
    def launch(
        config: RunConfig, idempotency_key: str = Header(min_length=1, max_length=200)
    ):
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
                run_id = engine.create(
                    config.model_dump(), idempotency_key + ":" + str(repetition)
                )
                if database.get(run_id)["state"] == "queued" and run_id not in jobs:
                    jobs[run_id] = executor.submit(
                        contextvars.copy_context().run, work, run_id
                    )
            run_ids.append(run_id)
        return {"run_ids": run_ids, "state": "queued", "schema_version": "0.3"}

    @app.get("/api/v1/runs/{run_id}", dependencies=[Depends(auth)])
    def get_run(run_id: str):
        state = database.get(run_id)
        return {
            "run_id": run_id,
            "state": state["state"],
            "session_id": state["session_id"],
            "assurance": state["assurance"],
            "config": state["config"],
            "capability_status": "mock_only",
        }

    @app.post("/api/v1/runs/{run_id}/cancel", dependencies=[Depends(auth)])
    def cancel(run_id: str, body: CancelRun):
        state = engine.cancel(run_id, body.reason)
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

    @app.get("/api/v1/runs/{run_id}/evidence", dependencies=[Depends(auth)])
    def bundle(run_id: str):
        return evidence.bundle(run_id)

    @app.post(
        "/api/v1/runs/{run_id}/preservation-holds",
        status_code=201,
        dependencies=[Depends(auth)],
    )
    def hold(run_id: str, body: HoldRequest):
        return evidence.hold(
            run_id, body.reason, body.owner_id, body.expires_at, body.idempotency_key
        )

    @app.post(
        "/api/v1/runs/{run_id}/preservation-holds/{hold_id}/release",
        dependencies=[Depends(auth)],
    )
    def release_hold(run_id: str, hold_id: str, body: CancelRun):
        evidence.release(run_id, hold_id, body.reason)
        return {"state": "released", "hold_id": hold_id}

    @app.post(
        "/api/v1/runs/{run_id}/preservation-holds/{hold_id}/renew",
        dependencies=[Depends(auth)],
    )
    def renew_hold(run_id: str, hold_id: str, body: RenewHold):
        return evidence.renew(run_id, hold_id, body.expires_at, body.reason)

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

    @app.websocket("/api/v1/runs/{run_id}/ws")
    async def websocket(socket: WebSocket, run_id: str):
        authorization = socket.headers.get("authorization", "")
        protocols = [
            p.strip()
            for p in socket.headers.get("sec-websocket-protocol", "").split(",")
        ]
        ticket_id = next((p[7:] for p in protocols if p.startswith("ticket.")), None)
        with job_lock:
            ticket = tickets.pop(ticket_id, None) if ticket_id else None
        ticket_ok = (
            ticket and ticket["run_id"] == run_id and ticket["expires_at"] > time.time()
        )
        if (
            not hmac.compare_digest(authorization, "Bearer " + token) and not ticket_ok
        ) or socket.headers.get("origin") not in origins:
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
            }
        )
        wire_cursor = max((e["sequence"] for e in database.events(run_id)), default=0)
        last_activity = last_ping = time.monotonic()
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
                if time.monotonic() - last_activity > 30:
                    raise ValueError("IDLE_TIMEOUT")
                if time.monotonic() - last_ping > 10:
                    await socket.send_json({"type": "ping"})
                    last_ping = time.monotonic()
                try:
                    message = await asyncio.wait_for(
                        socket.receive_json(), timeout=0.05
                    )
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
            await socket.send_json(
                {"type": "error", "code": str(error) or "IDLE_TIMEOUT"}
            )
            await socket.close(code=1008)
        except WebSocketDisconnect:
            pass

    return app


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

    uvicorn.run(
        create_app(),
        host=os.environ.get("HARNESS_BIND", "127.0.0.1"),
        port=8000,
        ws_max_size=16384,
    )
