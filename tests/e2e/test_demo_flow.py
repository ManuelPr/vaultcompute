"""E2E: replay a canned transcript through the same wiring the demo uses."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

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
from blindfold.tools.blindfold_compute import handle_blindfold_compute

FIXTURE = Path(__file__).parent / "recorded_transcript.json"


async def test_demo_flow_end_to_end():
    transcript = json.loads(FIXTURE.read_text(encoding="utf-8"))
    sandbox = SubprocessSandbox()
    session_id = f"e2e_{uuid.uuid4().hex[:8]}"
    config = BlindfoldConfig(
        schemas={
            "get_salary": ToolSchemaConfig(
                sensitive_fields=[
                    SensitiveFieldConfig(path="$.salary", semantic_type="salary", unit="EUR/year")
                ]
            )
        },
        compute=ComputeConfig(mode="python_unsafe"),
    )
    blindfold = BlindfoldSession(config, session_id=session_id)

    # Everything a real model would receive or produce; probed for leaks below.
    llm_visible_stream = [blindfold.model_instructions, transcript["question"]]
    bindings: dict[str, Any] = {}

    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "examples.fake_hr_mcp"], env=None
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp_session:
            await mcp_session.initialize()
            for step in transcript["steps"]:
                if step["kind"] == "tool_call":
                    async def invoke_tool():
                        call = await mcp_session.call_tool(
                            step["name"], step["arguments"]
                        )
                        text = call.content[0].text if call.content else "{}"
                        return json.loads(text)

                    protected = await blindfold.call_protected_tool_async(
                        step["name"], invoke_tool
                    )
                    text = json.dumps(protected, ensure_ascii=False)
                    llm_visible_stream.append(text)
                    bindings[step["bind_result_as"]] = json.loads(text)

                elif step["kind"] == "compute":
                    inputs = [_deref(bindings, ref) for ref in step["inputs_from"]]
                    code = step["code_template"].format(*inputs)
                    llm_visible_stream.append(code)
                    token = handle_blindfold_compute(
                        {"code": code, "inputs": inputs},
                        store=blindfold.store,
                        policy=blindfold.policy,
                        sandbox=sandbox,
                        session_id=blindfold.session_id,
                        ttl_seconds=config.tokens.default_ttl,
                    )
                    llm_visible_stream.append(token)
                    bindings[step["bind_result_as"]] = token

                elif step["kind"] == "final_text":
                    text = step["template"].format(**bindings)
                    llm_visible_stream.append(text)
                    rehydrated = blindfold.render_final_answer(text)
                    assert rehydrated == transcript["expected_final_text"]

    joined = "\n".join(llm_visible_stream)
    for probe in transcript["leak_probes"]:
        assert str(probe) not in joined, f"real value leaked to LLM-visible stream: {probe!r}"


def _deref(bindings: dict, ref: str) -> Any:
    key, *path = ref.split(".")
    node = bindings[key]
    for p in path:
        node = node[p]
    return node
