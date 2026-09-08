"""Claude Code lifecycle adapter.

Only output shapes that can be reconstructed according to Claude Code's
current hook contract are admitted. MCP results are unvalidated by the host,
while Bash and PowerShell use the documented structured stdout/stderr shape.
Other configured built-in tools are denied by PreToolUse until an explicit
shape adapter exists for them.
"""

from __future__ import annotations

import copy
from typing import Any

from vaultcompute.config import VaultComputeConfig, describe_config
from vaultcompute.core.rehydrator import TOKEN_PATTERN, rehydrate
from vaultcompute.hosts.common import (
    ProtectionError,
    is_protected,
    protect_json_text,
    safe_reason,
    session_id,
)
from vaultcompute.ports.policy import DetokenizePolicy
from vaultcompute.ports.token_store import TokenStore

SUPPORTED_STRUCTURED_BUILTINS = frozenset({"Bash", "PowerShell"})


def handle_session_start(event: dict, *, config: VaultComputeConfig) -> dict | None:
    brief = describe_config(config)
    if brief is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": brief,
        }
    }


def can_rewrite_tool(tool_name: str) -> bool:
    return tool_name.startswith("mcp__") or tool_name in SUPPORTED_STRUCTURED_BUILTINS


def handle_pre_tool_use(event: dict, *, config: VaultComputeConfig) -> dict | None:
    """Deny configured built-ins whose result shape VaultCompute cannot replace."""
    tool_name = str(event.get("tool_name") or "")
    if not is_protected(config, tool_name) or can_rewrite_tool(tool_name):
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": safe_reason(
                tool_name,
                "this Claude Code output shape has no audited VaultCompute adapter",
            ),
        }
    }


def _protect_text(text: str, event: dict, config: VaultComputeConfig, store: TokenStore):
    return protect_json_text(
        text,
        tool_name=str(event.get("tool_name") or ""),
        session_id=session_id(event),
        config=config,
        store=store,
    )


def _mcp_replacement(
    response: Any, event: dict, config: VaultComputeConfig, store: TokenStore
) -> Any:
    if isinstance(response, str):
        return _protect_text(response, event, config, store).text

    if isinstance(response, list) and len(response) == 1:
        part = response[0]
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            replacement = copy.deepcopy(response)
            replacement[0]["text"] = _protect_text(
                part["text"], event, config, store
            ).text
            return replacement

    if isinstance(response, dict):
        if "structuredContent" in response:
            raise ProtectionError("the MCP result contains unsupported structuredContent")
        content = response.get("content")
        if isinstance(content, list) and len(content) == 1:
            part = content[0]
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                replacement = copy.deepcopy(response)
                replacement["content"][0]["text"] = _protect_text(
                    part["text"], event, config, store
                ).text
                return replacement

    raise ProtectionError("the MCP result is not one supported JSON text part")


def _shell_replacement(
    response: Any, event: dict, config: VaultComputeConfig, store: TokenStore
) -> dict:
    if not isinstance(response, dict) or not isinstance(response.get("stdout"), str):
        raise ProtectionError(
            "the shell result does not match the documented structured shape"
        )
    if response.get("stderr") not in (None, ""):
        raise ProtectionError(
            "the shell result also contains stderr, which is not declared separately"
        )
    replacement = copy.deepcopy(response)
    replacement["stdout"] = _protect_text(response["stdout"], event, config, store).text
    return replacement


def stop_response(tool_name: str, detail: str) -> dict:
    """Stop before another model request when a safe replacement is impossible."""
    return {"continue": False, "stopReason": safe_reason(tool_name, detail)}


def handle_post_tool_use(
    event: dict,
    *,
    config: VaultComputeConfig,
    store: TokenStore,
) -> dict | None:
    tool_name = str(event.get("tool_name") or "")
    if not is_protected(config, tool_name):
        return None

    try:
        # Compatibility with Claude Code versions observed before tool_response
        # became the documented structured field.
        legacy = event.get("tool_output")
        if isinstance(legacy, str):
            replacement: Any = _protect_text(legacy, event, config, store).text
        elif tool_name.startswith("mcp__"):
            replacement = _mcp_replacement(
                event.get("tool_response"), event, config, store
            )
        elif tool_name in SUPPORTED_STRUCTURED_BUILTINS:
            replacement = _shell_replacement(
                event.get("tool_response"), event, config, store
            )
        else:
            raise ProtectionError(
                "no audited output adapter exists for this built-in tool"
            )
    except ProtectionError as exc:
        return stop_response(tool_name, str(exc))

    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": replacement,
        }
    }


def handle_message_display(
    event: dict,
    *,
    store: TokenStore,
    policy: DetokenizePolicy,
) -> dict | None:
    text = event.get("delta")
    if not isinstance(text, str) or not TOKEN_PATTERN.search(text):
        return None
    try:
        sid = session_id(event)
    except ProtectionError:
        # A display failure reveals nothing: leave the placeholder visible.
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "MessageDisplay",
            "displayContent": rehydrate(text, sid, store, policy),
        }
    }
