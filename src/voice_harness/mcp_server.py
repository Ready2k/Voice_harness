"""Pinned SDK stdio MCP server bound to one trusted orchestration context."""

import asyncio
import json
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

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
    async def list_tools(ctx, params):
        return ListToolsResult(
            tools=[
                Tool(
                    name=name,
                    description="Synthetic fixture tool: " + name,
                    input_schema=model.model_json_schema(),
                )
                for name, model in TOOLS.items()
            ]
        )

    async def call_tool(ctx, params):
        # Arguments are validated by engine.tool against the same TOOLS models, so an
        # invalid call still yields an evidenced INVALID_ARGUMENT result.
        call_id = (ctx.meta or {}).get("harness_call_id") or identifier("call")
        result = await asyncio.to_thread(
            engine.tool, run_id, context, call_id, params.name, params.arguments or {}
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
            structured_content=result,
            is_error=result["status"] != "success",
        )

    server = Server(
        "synthetic-bank", version="0.3", on_list_tools=list_tools, on_call_tool=call_tool
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
