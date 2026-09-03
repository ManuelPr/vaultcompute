"""Blindfold stdio MCP proxy.

Wraps a downstream stdio MCP server, tokenizes outbound tool results,
injects `blindfold_compute` into tools/list, and answers the custom
`blindfold/rehydrate` control-plane method.

stdin/stdout of THIS process are the harness-facing side.
The downstream child's stdin/stdout are asyncio pipes wired by
create_subprocess_exec. stdin on Windows cannot be attached to the
asyncio loop, so we read it via asyncio.to_thread and write to stdout
synchronously (messages are small and JSON-RPC framing is line-based).
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from blindfold.config import (
    BlindfoldConfig,
    build_token_store,
    load_config,
    required_table_paths_for,
    schema_fields_for,
    schema_fields_for_resource,
    table_schemas_for,
)
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.protection import protect_result, protect_results
from blindfold.core.rehydrator import rehydrate
from blindfold.core.tokenizer import describe_schema, describe_tables
from blindfold.errors import ProtectionError
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.sandbox import ComputeSandbox, SandboxError
from blindfold.ports.token_store import TokenStore
from blindfold.sandbox.subprocess_ import SubprocessSandbox
from blindfold.tools.blindfold_compute import (
    BLINDFOLD_COMPUTE_TOOL_NAME,
    build_tool_definition,
    handle_blindfold_compute,
)
from blindfold.tools.blindfold_table import (
    BLINDFOLD_TABLE_TOOL_NAME,
    build_tool_definition as build_table_tool_definition,
    handle_blindfold_table,
)


@dataclass
class ProxyState:
    store: TokenStore
    policy: DetokenizePolicy
    sandbox: ComputeSandbox
    config: BlindfoldConfig
    session_id: str
    pending_calls: dict[Any, str] = field(default_factory=dict)  # jsonrpc id -> tool name
    pending_reads: dict[Any, str] = field(default_factory=dict)  # jsonrpc id -> resource uri

    @property
    def ttl_seconds(self) -> int:
        return self.config.tokens.default_ttl


class ProxyProtectionError(ValueError):
    """A protected protocol result cannot be inspected safely."""


def _error_message(request_id: Any, detail: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32001, "message": f"Blindfold blocked a response: {detail}"},
    }


def _block_response(msg: dict, detail: str) -> None:
    request_id = msg.get("id")
    msg.clear()
    msg.update(_error_message(request_id, detail))


def build_proxy_state(config: BlindfoldConfig) -> ProxyState:
    return ProxyState(
        store=build_token_store(config),
        policy=SessionBoundPolicy(),
        sandbox=SubprocessSandbox(),
        config=config,
        session_id=f"sess_{uuid.uuid4().hex[:12]}",
    )


async def run_proxy(downstream_cmd: list[str], config_path: Path | None = None) -> None:
    cfg = load_config(config_path) if config_path is not None else BlindfoldConfig()
    state = build_proxy_state(cfg)

    child = await asyncio.create_subprocess_exec(
        *downstream_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=sys.stderr,
    )

    stdin_buf: BinaryIO = sys.stdin.buffer
    stdout_buf: BinaryIO = sys.stdout.buffer

    async def read_client_line() -> bytes:
        # Blocking readline on stdin runs in a worker thread so the event loop
        # keeps servicing the child's stdout on Windows too.
        return await asyncio.to_thread(stdin_buf.readline)

    def write_client_line(data: bytes) -> None:
        stdout_buf.write(data)
        stdout_buf.flush()

    tasks = [
        asyncio.create_task(_pump_client_to_child(read_client_line, child, write_client_line, state)),
        asyncio.create_task(_pump_child_to_client(child, write_client_line, state)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        # Half a second to reap a child that has already gone: returncode is not
        # populated the instant its stdout hits EOF, so checking it before this
        # reports nothing. If the wait times out the child is alive and we are
        # the ones shutting it down, which is the normal path and silent.
        try:
            await asyncio.wait_for(child.wait(), timeout=0.5)
            died_on_its_own = True
        except asyncio.TimeoutError:
            died_on_its_own = False
            child.terminate()
            try:
                await asyncio.wait_for(child.wait(), timeout=5)
            except asyncio.TimeoutError:
                child.kill()

        if died_on_its_own and child.returncode != 0:
            # A downstream that cannot start otherwise leaves the proxy exiting
            # without a word and the client waiting for a server already gone.
            # The usual cause is the wrong interpreter: `uv run` does not put
            # its virtualenv on a child process's PATH.
            print(
                f"[blindfold] downstream {downstream_cmd[0]!r} exited with code "
                f"{child.returncode} — its own error, if any, is above",
                file=sys.stderr,
            )


async def _pump_client_to_child(read_line, child, write_client, state: ProxyState) -> None:
    assert child.stdin is not None
    while True:
        line = await read_line()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[blindfold] bad JSON from client: {exc!r}", file=sys.stderr)
            continue
        if not isinstance(msg, dict):
            if state.config.proxy.strict:
                print("[blindfold] blocked a JSON-RPC batch in strict mode", file=sys.stderr)
                if isinstance(msg, list):
                    errors = [
                        _error_message(item.get("id"), "JSON-RPC batches are not supported in strict mode")
                        for item in msg
                        if isinstance(item, dict) and "id" in item
                    ]
                    if errors:
                        write_client((json.dumps(errors) + "\n").encode("utf-8"))
                continue
            print("[blindfold] forwarding a JSON-RPC batch in permissive mode", file=sys.stderr)
            child.stdin.write(line)
            await child.stdin.drain()
            continue

        method = msg.get("method")
        if method == "blindfold/rehydrate":
            _handle_rehydrate(msg, write_client, state)
            continue
        if method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") == BLINDFOLD_COMPUTE_TOOL_NAME:
                if state.config.compute.mode != "python_unsafe":
                    write_client(
                        (json.dumps(_error_message(msg.get("id"), "arbitrary Python compute is disabled")) + "\n").encode("utf-8")
                    )
                    continue
                # In a worker thread: the sandbox blocks for up to its timeout,
                # and doing that inline stopped the proxy forwarding anything
                # in either direction for those seconds.
                await asyncio.to_thread(_handle_blindfold_compute, msg, write_client, state)
                continue
            if params.get("name") == BLINDFOLD_TABLE_TOOL_NAME:
                if state.config.compute.mode == "disabled":
                    write_client(
                        (json.dumps(_error_message(msg.get("id"), "controlled compute is disabled")) + "\n").encode("utf-8")
                    )
                    continue
                # No sandbox and no subprocess: a table query is a fixed set of
                # operations run here, so it does not need a worker thread.
                _handle_blindfold_table(msg, write_client, state)
                continue
            state.pending_calls[msg.get("id")] = params.get("name")
        elif method == "resources/read":
            # Resources carry data just like tool results do, and used to pass
            # through untouched — a server exposing salaries as a resource got
            # no protection at all.
            state.pending_reads[msg.get("id")] = ((msg.get("params") or {}).get("uri")) or ""

        child.stdin.write(line)
        await child.stdin.drain()


async def _pump_child_to_client(child, write_client, state: ProxyState) -> None:
    assert child.stdout is not None
    while True:
        line = await child.stdout.readline()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[blindfold] bad JSON from child: {exc!r}", file=sys.stderr)
            if state.config.proxy.strict:
                write_client(
                    (json.dumps(_error_message(None, "downstream returned invalid JSON")) + "\n").encode("utf-8")
                )
            else:
                write_client(line)
            continue
        if not isinstance(msg, dict):
            if state.config.proxy.strict:
                print("[blindfold] blocked a downstream JSON-RPC batch in strict mode", file=sys.stderr)
                write_client(
                    (json.dumps(_error_message(None, "downstream JSON-RPC batches are not supported in strict mode")) + "\n").encode("utf-8")
                )
            else:
                print("[blindfold] forwarding a JSON-RPC batch in permissive mode", file=sys.stderr)
                write_client(line)
            continue

        if "result" in msg and isinstance(msg["result"], dict):
            tools = msg["result"].get("tools")
            if isinstance(tools, list):
                _annotate_protected_tools(tools, state)
                if state.config.compute.mode == "python_unsafe":
                    tools.append(build_tool_definition())
                if (
                    state.config.compute.mode != "disabled"
                    and any(t.tables for t in state.config.schemas.values())
                ):
                    tools.append(build_table_tool_definition())

        msg_id = msg.get("id")
        try:
            if msg_id in state.pending_calls:
                tool_name = state.pending_calls.pop(msg_id)
                if "result" not in msg or not isinstance(msg["result"], dict):
                    if schema_fields_for(state.config, tool_name) or table_schemas_for(state.config, tool_name):
                        raise ProxyProtectionError("protected tool returned no inspectable result")
                else:
                    _tokenize_tool_call_result(msg, tool_name, state)
            elif msg_id in state.pending_reads:
                requested_uri = state.pending_reads.pop(msg_id)
                if "result" not in msg or not isinstance(msg["result"], dict):
                    if schema_fields_for_resource(state.config, requested_uri):
                        raise ProxyProtectionError("protected resource returned no inspectable result")
                else:
                    _tokenize_resource_read(msg, requested_uri, state)
        except ProxyProtectionError as exc:
            if state.config.proxy.strict:
                _block_response(msg, str(exc))
            else:
                print(f"[blindfold] permissive passthrough: {exc}", file=sys.stderr)

        write_client((json.dumps(msg) + "\n").encode("utf-8"))


def _annotate_protected_tools(tools: list, state: ProxyState) -> None:
    """Tell the model what each tool's tokens mean, once, on the tool itself.

    Cheaper than repeating it on every result: the same text would otherwise be
    re-sent per call and linger in the transcript for the rest of the session.
    """
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name", "")
        notes = [
            describe_schema(
                schema_fields_for(state.config, name),
                allow_python_compute=state.config.compute.mode == "python_unsafe",
            ),
            describe_tables(table_schemas_for(state.config, name)),
        ]
        note = "\n\n".join(n for n in notes if n)
        if not note:
            continue
        tool["description"] = f"{tool.get('description', '').rstrip()}\n\n{note}".lstrip()


def _tokenize_tool_call_result(msg: dict, tool_name: str, state: ProxyState) -> None:
    fields = schema_fields_for(state.config, tool_name)
    tables = table_schemas_for(state.config, tool_name)
    if not fields and not tables:
        return
    content = (msg.get("result") or {}).get("content") or []
    if not isinstance(content, list) or not content:
        raise ProxyProtectionError("protected tool returned no content")
    now = datetime.now(tz=timezone.utc)
    ttl = now + timedelta(seconds=state.ttl_seconds)
    payloads = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "text":
            raise ProxyProtectionError("protected tool returned a non-text content part")
        try:
            payloads.append(json.loads(part.get("text", "")))
        except json.JSONDecodeError:
            raise ProxyProtectionError("protected tool returned text that is not JSON") from None

    try:
        protected = protect_results(
            payloads,
            source_name=tool_name,
            fields=fields,
            tables=tables,
            required_table_paths=required_table_paths_for(state.config, tool_name),
            store=state.store,
            session_id=state.session_id,
            ttl=ttl,
        )
    except ProtectionError as exc:
        raise ProxyProtectionError(str(exc)) from None

    for i, (part, tokenized) in enumerate(zip(content, protected, strict=True)):
        # ensure_ascii=False, and it is not cosmetic: this text is what the
        # model reads. Escaped, it sees the characters ⟦tok_…⟧ and
        # may copy that form into its answer, which the rehydrator's regex
        # does not match — the user would get an unreplaced escape sequence.
        replacement = dict(part)
        replacement["text"] = json.dumps(tokenized, ensure_ascii=False)
        content[i] = replacement


def _tokenize_resource_read(msg: dict, requested_uri: str, state: ProxyState) -> None:
    contents = (msg.get("result") or {}).get("contents") or []
    if not isinstance(contents, list) or not contents:
        if schema_fields_for_resource(state.config, requested_uri):
            raise ProxyProtectionError("protected resource returned no contents")
        return
    now = datetime.now(tz=timezone.utc)
    ttl = now + timedelta(seconds=state.ttl_seconds)
    matched = False
    for part in contents:
        if not isinstance(part, dict):
            raise ProxyProtectionError("protected resource returned an invalid content part")
        # Each returned part names its own URI; the request URI is the fallback
        # for servers that leave it out.
        uri = part.get("uri") or requested_uri
        fields = schema_fields_for_resource(state.config, uri)
        if not fields:
            continue
        text = part.get("text")
        if not isinstance(text, str):
            # A blob, or no text at all. Declared and unprotectable is worth
            # saying out loud rather than passing along.
            print(
                f"[blindfold] {uri} declares protected paths but returned no text part",
                file=sys.stderr,
            )
            raise ProxyProtectionError("protected resource returned a blob or no text")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            print(
                f"[blindfold] {uri} declares protected paths but did not return JSON",
                file=sys.stderr,
            )
            raise ProxyProtectionError("protected resource returned text that is not JSON") from None
        try:
            tokenized = protect_result(
                payload,
                source_name=uri,
                fields=fields,
                tables=[],
                required_table_paths=set(),
                store=state.store,
                session_id=state.session_id,
                ttl=ttl,
            )
        except ProtectionError as exc:
            raise ProxyProtectionError(str(exc)) from None
        matched = True
        part["text"] = json.dumps(tokenized, ensure_ascii=False)
    if schema_fields_for_resource(state.config, requested_uri) and not matched:
        raise ProxyProtectionError("no returned resource part matched the protected declaration")


def _handle_blindfold_table(msg: dict, write_client, state: ProxyState) -> None:
    args = ((msg.get("params") or {}).get("arguments")) or {}
    try:
        new_token = handle_blindfold_table(
            args,
            store=state.store,
            policy=state.policy,
            session_id=state.session_id,
            ttl_seconds=state.ttl_seconds,
        )
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": new_token}], "isError": False},
        }
    except ValueError as exc:
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": f"blindfold_table error: {exc}"}], "isError": True},
        }
    write_client((json.dumps(payload) + "\n").encode("utf-8"))


def _handle_rehydrate(msg: dict, write_client, state: ProxyState) -> None:
    params = msg.get("params") or {}
    text = params.get("text", "")
    result_text = rehydrate(text, state.session_id, state.store, state.policy)
    write_client(
        (json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {"text": result_text}}) + "\n").encode("utf-8"),
    )


def _handle_blindfold_compute(msg: dict, write_client, state: ProxyState) -> None:
    args = ((msg.get("params") or {}).get("arguments")) or {}
    try:
        new_token = handle_blindfold_compute(
            args,
            store=state.store,
            policy=state.policy,
            sandbox=state.sandbox,
            session_id=state.session_id,
            ttl_seconds=state.ttl_seconds,
            max_calls_per_token=state.config.compute.max_calls_per_token,
            rate_window_s=state.config.compute.rate_window_s,
        )
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": new_token}], "isError": False},
        }
    except (ValueError, SandboxError) as exc:
        payload = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {"content": [{"type": "text", "text": f"blindfold_compute error: {exc}"}], "isError": True},
        }
    write_client((json.dumps(payload) + "\n").encode("utf-8"))
