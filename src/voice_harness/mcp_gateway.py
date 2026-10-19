"""Gateway-owned stdio context and logical call identity."""

import json
import os
import sys
from contextlib import asynccontextmanager
from importlib.metadata import version

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from .contracts import TOOLS, Result
from .ratelimit import RateLimiter


@asynccontextmanager
async def gateway(engine, run_id, limiter=None, faults=None):
    """``faults`` overrides ``engine.faults`` for this run's MCP worker only."""
    from opentelemetry.propagate import inject

    carrier = {}
    inject(carrier)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "voice_harness.mcp_server"],
        env={
            **os.environ,
            "HARNESS_DATABASE_URL": engine.database.url,
            "HARNESS_RUN_CONTEXT": json.dumps(engine.context(run_id)),
            "HARNESS_FAULT_PROFILE": json.dumps(engine.faults if faults is None else faults),
            "HARNESS_TRACE_CONTEXT": json.dumps(carrier),
        },
    )
    async with stdio_client(parameters) as (read, write):  # noqa: SIM117 -- streams precede session creation
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            with engine.database.transaction(run_id) as tx:
                tx.event(
                    "mcp",
                    "mcp.initialized",
                    {
                        "protocol_version": initialized.protocol_version,
                        "sdk_version": version("mcp"),
                        "transport": "stdio",
                        "bridge": "gateway_bridge",
                    },
                )
            yield Gateway(session, engine, run_id, limiter)


class Gateway:
    def __init__(self, session, engine, run_id, limiter=None):
        self.session, self.engine, self.run_id = session, engine, run_id
        self.limiter = limiter or RateLimiter(
            int(os.environ.get("HARNESS_TOOL_RATE_LIMIT", "60")),
            float(os.environ.get("HARNESS_TOOL_RATE_WINDOW", "10")),
        )

    async def tool(self, call_id, name, args):
        self.engine.prepare_tool(
            self.run_id, self.engine.context(self.run_id), call_id, name, args
        )
        if not self.limiter.allow(self.run_id):
            return self._rate_limited(call_id)
        try:
            TOOLS[name].model_validate(args)
        except (KeyError, ValidationError):
            return self.engine.tool(
                self.run_id, self.engine.context(self.run_id), call_id, name, args
            )
        try:
            native = await self.session.call_tool(
                name,
                args,
                read_timeout_seconds=3,
                meta={"harness_call_id": call_id},
            )
        except Exception:  # noqa: BLE001 -- all delivery failures require outcome reconciliation
            with self.engine.database.transaction(self.run_id) as tx:
                known = tx.state["calls"][call_id]["result"]
                event = tx.event(
                    "mcp",
                    "tool.result_delivery_failed",
                    {"call_id": call_id, "known_outcome": known is not None},
                )
                if known is not None:
                    tx.event(
                        "mcp",
                        "tool.reconciled",
                        {"call_id": call_id, "status": known["status"]},
                        causes=[event],
                    )
                    return known
                unknown_event = tx.event(
                    "mcp",
                    "tool.outcome_unknown",
                    {"call_id": call_id, "reason": "DELIVERY_LOST"},
                    causes=[event],
                )
                result = Result(
                    call_id=call_id,
                    status="outcome_unknown",
                    structured_content=None,
                    error_code="OUTCOME_UNKNOWN",
                    evidence_event_ids=[unknown_event],
                ).model_dump()
                tx.state["calls"][call_id]["state"] = "outcome_unknown"
                tx.state["calls"][call_id]["result"] = result
                tx.state["state"] = "recovery_required"
                return result
        result = Result.model_validate(native.structured_content).model_dump()
        if result["call_id"] != call_id:
            raise ValueError("TOOL_RESULT_CORRELATION_FAILED")
        with self.engine.database.transaction(self.run_id) as tx:
            tx.event(
                "mcp",
                "mcp.result_received",
                {
                    "call_id": call_id,
                    "native_envelope": native.model_dump(mode="json", by_alias=True),
                },
            )
        return result

    def _rate_limited(self, call_id):
        """Reject before dispatch: nothing executes, the refusal is evidence."""
        with self.engine.database.transaction(self.run_id) as tx:
            call = tx.state["calls"][call_id]
            if call["result"] is not None:
                return call["result"]
            requested = call["request_event_id"]
            event = tx.event(
                "mcp",
                "tool.failed",
                {"call_id": call_id, "status": "error", "error_code": "RATE_LIMITED"},
                causes=[requested],
            )
            result = Result(
                call_id=call_id,
                status="error",
                structured_content=None,
                error_code="RATE_LIMITED",
                evidence_event_ids=[event],
            ).model_dump()
            call["state"], call["result"] = "failed", result
            return result
