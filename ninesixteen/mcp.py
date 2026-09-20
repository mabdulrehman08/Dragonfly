"""A minimal MCP server (Streamable HTTP, JSON-RPC 2.0) that exposes the read-only tools to headless Claude Code runs.

Why: XO Space observes Claude Code sessions, not raw API calls. With `NINESIXTEEN_LLM=claude-code` every agent run is
a `claude -p` subprocess whose only tools are the ones served here. The URL names the run, and the run names the agent,
so `tools/list` returns exactly that agent's allow-list and `tools/call` refuses anything else (I1 stays structural).
No new dependency: FastAPI already speaks JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from ninesixteen import tools
from ninesixteen.sim.world import World

PROTOCOL = "2025-03-26"


@dataclass
class Registration:
    """One live agent run: which world it may read, which tools it may call, and what it called."""

    world: World
    tool_names: tuple[str, ...]
    calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = field(default_factory=list)
    tool_failed: bool = False


RUNS: dict[str, Registration] = {}

mcp_app = FastAPI(title="ninesixteen tools (MCP)")


def _ok(req_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _err(req_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


@mcp_app.post("/{run_id}")
async def rpc(run_id: str, body: dict[str, Any]) -> Response:
    reg = RUNS.get(run_id)
    method, req_id, params = body.get("method"), body.get("id"), body.get("params") or {}
    if reg is None:
        return _err(req_id, -32001, "unknown run")
    if method == "initialize":
        return _ok(
            req_id, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "ninesixteen", "version": "1.0"}}
        )
    if method in ("notifications/initialized", "notifications/cancelled"):
        return Response(status_code=202)
    if method == "ping":
        return _ok(req_id, {})
    if method == "tools/list":
        return _ok(
            req_id, {"tools": [{"name": n, "description": tools.TOOLS[n][1], "inputSchema": tools.TOOLS[n][2]} for n in reg.tool_names]}
        )
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in reg.tool_names:
            return _ok(req_id, {"content": [{"type": "text", "text": "tool not allowed for this agent"}], "isError": True})
        try:
            result = tools.call_tool(reg.world, name, dict(args))
            reg.calls.append((name, dict(args), result))
            return _ok(req_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
        except tools.ToolError as e:
            reg.calls.append((name, dict(args), None))
            reg.tool_failed = True
            return _ok(req_id, {"content": [{"type": "text", "text": f"ToolError: {e}"}], "isError": True})
        except TypeError as e:
            return _ok(req_id, {"content": [{"type": "text", "text": f"bad arguments: {e}"}], "isError": True})
    return _err(req_id, -32601, f"method not found: {method}")


@mcp_app.get("/{run_id}")
async def no_stream(run_id: str) -> Response:
    """We don't push server events; tell the client so it uses plain request/response."""
    return Response(status_code=405)
