"""Lifecycle hook dispatcher for Claude Code and Codex.

The public functions keep the original Claude Code API for callers and tests,
while host-specific event contracts live under :mod:`blindfold.hosts`.
"""

from __future__ import annotations

import json
from typing import Any

from blindfold.config import BlindfoldConfig
from blindfold.hosts import CLAUDE_CODE, CODEX, HOSTS, claude_code, codex
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.token_store import TokenStore

PRE_TOOL_USE = "pre-tool-use"
POST_TOOL_USE = "post-tool-use"
MESSAGE_DISPLAY = "message-display"
SESSION_START = "session-start"

CLAUDE_EVENTS = (PRE_TOOL_USE, POST_TOOL_USE, MESSAGE_DISPLAY, SESSION_START)
CODEX_EVENTS = (PRE_TOOL_USE, POST_TOOL_USE, SESSION_START)
EVENTS = CLAUDE_EVENTS


def events_for(host: str) -> tuple[str, ...]:
    if host == CLAUDE_CODE:
        return CLAUDE_EVENTS
    if host == CODEX:
        return CODEX_EVENTS
    raise ValueError(f"unknown host {host!r}; expected one of {', '.join(HOSTS)}")


def handle_session_start(event: dict, *, config: BlindfoldConfig) -> dict | None:
    return claude_code.handle_session_start(event, config=config)


def handle_pre_tool_use(event: dict, *, config: BlindfoldConfig) -> dict | None:
    return claude_code.handle_pre_tool_use(event, config=config)


def handle_post_tool_use(
    event: dict, *, config: BlindfoldConfig, store: TokenStore
) -> dict | None:
    return claude_code.handle_post_tool_use(event, config=config, store=store)


def handle_message_display(
    event: dict, *, store: TokenStore, policy: DetokenizePolicy
) -> dict | None:
    return claude_code.handle_message_display(event, store=store, policy=policy)


def failure_response(host: str, event_name: str, message: str) -> dict | None:
    if event_name in (MESSAGE_DISPLAY, SESSION_START):
        return None
    if host == CODEX and event_name == POST_TOOL_USE:
        return codex.replacement_response("the configured tool", message)
    if host == CLAUDE_CODE and event_name == PRE_TOOL_USE:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Blindfold: {message}",
            }
        }
    # Claude PostToolUse cannot safely invent an output shape here. Stopping
    # the turn prevents another model request from receiving the original.
    return {"continue": False, "stopReason": f"Blindfold: {message}"}


def dispatch(
    event_name: str,
    event: dict,
    *,
    config: BlindfoldConfig,
    store: TokenStore,
    policy: DetokenizePolicy,
    host: str = CLAUDE_CODE,
) -> dict | None:
    if event_name not in events_for(host):
        raise ValueError(
            f"unknown {host} hook event {event_name!r}; "
            f"expected one of {', '.join(events_for(host))}"
        )

    adapter = claude_code if host == CLAUDE_CODE else codex
    if event_name == PRE_TOOL_USE:
        return adapter.handle_pre_tool_use(event, config=config)
    if event_name == POST_TOOL_USE:
        return adapter.handle_post_tool_use(event, config=config, store=store)
    if event_name == MESSAGE_DISPLAY:
        return claude_code.handle_message_display(event, store=store, policy=policy)
    return adapter.handle_session_start(event, config=config)


def read_event(raw: str) -> dict[str, Any]:
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook input must be a JSON object")
    return event
