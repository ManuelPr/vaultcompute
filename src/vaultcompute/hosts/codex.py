"""Codex lifecycle adapter.

Codex can replace a completed tool result with hook feedback, so the protected
JSON is returned as that feedback. Codex currently has no display-only hook;
the transcript and the user therefore both keep placeholders.
"""

from __future__ import annotations

from typing import Any

from vaultcompute.config import VaultComputeConfig, describe_config
from vaultcompute.hosts.common import (
    ProtectionError,
    is_protected,
    protect_json_text,
    protect_payload,
    safe_reason,
    session_id,
)
from vaultcompute.ports.token_store import TokenStore


def handle_session_start(event: dict, *, config: VaultComputeConfig) -> dict | None:
    brief = describe_config(config)
    if brief is None:
        return None
    brief += (
        "\n\nCodex has no display-only rehydration hook. Keep every placeholder "
        "verbatim: the user will see placeholders in this integration."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": brief,
        }
    }


def handle_pre_tool_use(event: dict, *, config: VaultComputeConfig) -> dict | None:
    # Codex's PostToolUse feedback can safely replace any hooked local result.
    # Hosted tools do not fire this hook at all; that host limitation is
    # documented by the plugin rather than disguised as coverage.
    return None


def _protect_response(
    response: Any, event: dict, config: VaultComputeConfig, store: TokenStore
) -> str:
    tool_name = str(event.get("tool_name") or "")
    sid = session_id(event)

    if isinstance(response, str):
        return protect_json_text(
            response,
            tool_name=tool_name,
            session_id=sid,
            config=config,
            store=store,
        ).text

    if isinstance(response, list) and len(response) == 1:
        part = response[0]
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            return protect_json_text(
                part["text"],
                tool_name=tool_name,
                session_id=sid,
                config=config,
                store=store,
            ).text

    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list) and len(content) == 1:
            part = content[0]
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                return protect_json_text(
                    part["text"],
                    tool_name=tool_name,
                    session_id=sid,
                    config=config,
                    store=store,
                ).text

        if isinstance(response.get("stdout"), str):
            if response.get("stderr") not in (None, ""):
                raise ProtectionError(
                    "the shell result contains an unclassified stderr stream"
                )
            return protect_json_text(
                response["stdout"],
                tool_name=tool_name,
                session_id=sid,
                config=config,
                store=store,
            ).text

    if isinstance(response, (dict, list)):
        return protect_payload(
            response,
            tool_name=tool_name,
            session_id=sid,
            config=config,
            store=store,
        ).text

    raise ProtectionError("the tool result is not structured JSON")


def replacement_response(tool_name: str, feedback: str) -> dict:
    # In Codex PostToolUse, continue:false replaces the original with
    # stopReason and, unlike decision:block, does not reject a nested code-mode
    # tool promise.
    return {"continue": False, "stopReason": safe_reason(tool_name, feedback)}


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
        protected = _protect_response(event.get("tool_response"), event, config, store)
    except ProtectionError as exc:
        return replacement_response(tool_name, str(exc))
    return {"continue": False, "stopReason": protected}
