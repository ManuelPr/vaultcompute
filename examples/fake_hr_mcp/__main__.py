"""Fake HR API exposed as a stdio MCP server (test fixture)."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_SALARIES: dict[str, int] = {
    "John Smith": 62000,
    "James Brown": 71000,
    "Emily Johnson": 55000,
}

_EMPLOYEES: list[dict] = [
    {"name": "John Smith", "salary": 62000, "dept": "Engineering"},
    {"name": "James Brown", "salary": 71000, "dept": "Engineering"},
    {"name": "Emily Johnson", "salary": 55000, "dept": "Sales"},
    {"name": "Michael Wilson", "salary": 48000, "dept": "Sales"},
    {"name": "Sarah Miller", "salary": 83000, "dept": "Engineering"},
]


def _build_server() -> Server:
    server = Server("fake-hr-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_salary",
                description="Return the annual gross salary in EUR for a given full name.",
                inputSchema={
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            ),
            Tool(
                name="list_employees",
                description="Return every employee with salary and department.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        if name == "list_employees":
            # A list, so the demos have something to show a collective token on.
            return [
                TextContent(type="text", text=json.dumps({"employees": _EMPLOYEES}))
            ]
        if name != "get_salary":
            raise ValueError(f"unknown tool: {name}")
        person = arguments["name"]
        live_canary = os.environ.get("VAULTCOMPUTE_LIVE_CANARY")
        salary = (
            int(live_canary) if live_canary is not None else _SALARIES.get(person, 0)
        )
        return [
            TextContent(
                type="text", text=json.dumps({"name": person, "salary": salary})
            )
        ]

    return server


async def _amain() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
