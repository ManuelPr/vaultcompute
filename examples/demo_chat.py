"""Interactive demo — Claude answering an HR question through Blindfold.

Run with:
    uv run --extra demo python examples/demo_chat.py "Who earns more, Manuel Pernigotto or Andrea Tuscano?"

Requires ANTHROPIC_API_KEY in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from blindfold import BlindfoldSession
from blindfold.config import (
    BlindfoldConfig,
    ComputeConfig,
    SensitiveFieldConfig,
    ToolSchemaConfig,
)
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)

MODEL = "claude-opus-4-7"


async def _amain(question: str) -> None:
    sandbox = SubprocessSandbox()
    session_id = f"demo_{uuid.uuid4().hex[:8]}"
    config = BlindfoldConfig(
        schemas={
            "get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
                ]
            )
        },
        # This demo exercises arbitrary Python intentionally. Production code
        # should prefer a declared table plus TableQueryCapability.
        compute=ComputeConfig(mode="python_unsafe"),
    )
    blindfold = BlindfoldSession(config, session_id=session_id)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "examples.fake_hr_mcp"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()
            listed = await mcp_session.list_tools()
            tools = [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                }
                for tool in listed.tools
                if tool.name in config.schemas
            ]
            tool_def = build_tool_definition()
            tools.append({
                "name": BLINDFOLD_COMPUTE_TOOL_NAME,
                "description": tool_def["description"],
                "input_schema": tool_def["inputSchema"],
            })

            client = Anthropic()
            messages: list[dict] = [{"role": "user", "content": question}]

            while True:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=1024,
                    system=blindfold.model_instructions,
                    tools=tools,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    break

                tool_results: list[dict] = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    if block.name == BLINDFOLD_COMPUTE_TOOL_NAME:
                        try:
                            token = handle_blindfold_compute(
                                block.input,
                                store=blindfold.store,
                                policy=blindfold.policy,
                                sandbox=sandbox,
                                session_id=blindfold.session_id,
                                ttl_seconds=config.tokens.default_ttl,
                            )
                            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": token})
                        except Exception as exc:
                            tool_results.append({
                                "type": "tool_result", "tool_use_id": block.id,
                                "content": f"error: {exc}", "is_error": True,
                            })
                        continue

                    async def invoke_tool():
                        call = await mcp_session.call_tool(block.name, block.input)
                        text = call.content[0].text if call.content else "{}"
                        return json.loads(text)

                    protected = await blindfold.call_protected_tool_async(
                        block.name, invoke_tool
                    )
                    text = json.dumps(protected, ensure_ascii=False)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": text})

                messages.append({"role": "user", "content": tool_results})

            final_text = "".join(b.text for b in response.content if b.type == "text")
            print(blindfold.render_final_answer(final_text))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default="Who earns more, Manuel Pernigotto or Andrea Tuscano?")
    args = parser.parse_args()
    asyncio.run(_amain(args.question))


if __name__ == "__main__":
    main()
