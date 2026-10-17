"""Pinned SDK stdio MCP server bound to one trusted orchestration context."""

import asyncio
import json
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from .contracts import TOOLS
from .engine import Engine, identifier
from .storage import Database


async def main():
    engine = Engine(
        Database(os.environ["HARNESS_DATABASE_URL"]),
        faults=json.loads(os.environ.get("HARNESS_FAULT_PROFILE", "{}")),
    )
    # Context is injected at process launch; model arguments cannot select a run.
    context = json.loads(os.environ["HARNESS_RUN_CONTEXT"])
    run_id = context["run_id"]
    engine._context(engine.database.get(run_id), run_id, context)
    server = Server("synthetic-bank", version="0.3")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=name,
                description="Synthetic fixture tool: " + name,
                inputSchema=model.model_json_schema(),
            )
            for name, model in TOOLS.items()
        ]

    @server.call_tool(validate_input=True)
    async def call_tool(name, arguments):
        meta = server.request_context.meta
        call_id = meta.model_dump().get("harness_call_id") if meta else None
        call_id = call_id or identifier("call")
        result = await asyncio.to_thread(
            engine.tool, run_id, context, call_id, name, arguments or {}
        )
        # Native MCP envelope carries only the safe catalogue error; the canonical
        # ToolResult travels separately in structuredContent.
        native = (
            result["error"]
            if result["status"] != "success" and result.get("error")
            else result
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(native))],
            structuredContent=result,
            isError=result["status"] != "success",
        )

    from opentelemetry.context import attach, detach
    from opentelemetry.propagate import extract

    from .telemetry import provider

    traces = provider()
    token = attach(extract(json.loads(os.environ.get("HARNESS_TRACE_CONTEXT", "{}"))))
    try:
        with traces.get_tracer("voice-harness.mcp").start_as_current_span(
            "mcp.session"
        ):
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
    finally:
        detach(token)
        traces.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
