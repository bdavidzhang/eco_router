#!/usr/bin/env python3
"""
EcoRoute MCP Server

Exposes the FastAPI backend as MCP tools so Zed's AI agent can autonomously
query model rankings and get carbon-efficient recommendations.

Requires the FastAPI backend running at http://localhost:8000.
Transport: stdio (launched by Zed as a subprocess).
"""

import json
import re
from pathlib import Path

import anyio
import httpx
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

BACKEND_URL = "http://localhost:8000"
ZED_SETTINGS = Path.home() / ".config" / "zed" / "settings.json"

server = Server("ecoroute")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_zed_model() -> str:
    """Read the active model from ~/.config/zed/settings.json."""
    try:
        text = ZED_SETTINGS.read_text()
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r",\s*([}\]])", r"\1", text)
        cfg = json.loads(text)
        model = cfg.get("agent", {}).get("default_model", {}).get("model", "")
        if model:
            return model
    except Exception:
        pass
    return ""


def _call_score_task(task_type: str, context_size: int, current_model: str) -> dict:
    zed_model = _detect_zed_model()
    resolved = current_model or zed_model
    payload: dict = {"task_type": task_type, "context_size": context_size}
    if resolved:
        payload["current_model"] = resolved
    with httpx.Client(timeout=10) as client:
        resp = client.post(f"{BACKEND_URL}/tasks/score", json=payload)
        resp.raise_for_status()
        result = resp.json()
    result["zed_active_model"] = zed_model or None
    return result


def _call_rankings() -> list:
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{BACKEND_URL}/models/rankings")
        resp.raise_for_status()
        return resp.json()


def _call_health() -> dict:
    with httpx.Client(timeout=5) as client:
        resp = client.get(f"{BACKEND_URL}/health")
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="score_task",
            description=(
                "Recommends the most carbon-efficient model that meets quality "
                "requirements for the given coding task. Returns carbon savings "
                "vs the current model, quality confidence, and efficiency score."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_type": {
                        "type": "string",
                        "description": "One of: autocomplete, chat, debug, refactor",
                        "enum": ["autocomplete", "chat", "debug", "refactor"],
                    },
                    "context_size": {
                        "type": "integer",
                        "description": "Approximate context size in tokens (optional)",
                        "default": 0,
                    },
                    "current_model": {
                        "type": "string",
                        "description": (
                            "The model currently in use, e.g. 'claude-sonnet-4-6'. "
                            "Auto-detected from Zed settings if omitted."
                        ),
                        "default": "",
                    },
                },
                "required": ["task_type"],
            },
        ),
        types.Tool(
            name="get_model_rankings",
            description=(
                "Returns all Zed-configured models sorted by efficiency score (best first). "
                "Each entry includes: model name, sci_per_token, quality_tier, efficiency_score."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="health_check",
            description="Check if the EcoRoute backend is running and how many models are loaded.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "score_task":
        task_type = arguments.get("task_type", "chat")
        context_size = arguments.get("context_size", 0)
        current_model = arguments.get("current_model", "")
        result = await anyio.to_thread.run_sync(
            lambda: _call_score_task(task_type, context_size, current_model)
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "get_model_rankings":
        result = await anyio.to_thread.run_sync(_call_rankings)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "health_check":
        result = await anyio.to_thread.run_sync(_call_health)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    anyio.run(main)
