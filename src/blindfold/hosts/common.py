"""Host-independent protection helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from blindfold.config import BlindfoldConfig, schema_fields_for, table_schemas_for
from blindfold.core.tokenizer import tokenize_result
from blindfold.ports.token_store import TokenStore


class ProtectionError(ValueError):
    """A configured result cannot be transformed without risking disclosure."""


@dataclass(frozen=True)
class ProtectedPayload:
    value: Any
    text: str


def is_protected(config: BlindfoldConfig, tool_name: str) -> bool:
    return bool(
        schema_fields_for(config, tool_name) or table_schemas_for(config, tool_name)
    )


def protect_payload(
    payload: Any,
    *,
    tool_name: str,
    session_id: str,
    config: BlindfoldConfig,
    store: TokenStore,
) -> ProtectedPayload:
    """Tokenize a decoded JSON value and require the declaration to match.

    A configured path matching nothing is dangerous at a host boundary: it can
    mean the upstream tool changed shape and moved the value somewhere that is
    now passing through in cleartext. The lower-level tokenizer intentionally
    permits optional fields; host adapters use this stricter contract because
    they are the last stop before the model.
    """
    fields = schema_fields_for(config, tool_name)
    tables = table_schemas_for(config, tool_name)
    ttl = datetime.now(tz=UTC) + timedelta(seconds=config.tokens.default_ttl)
    tokenized = tokenize_result(
        payload,
        tool_name,
        fields,
        store,
        session_id,
        ttl,
        tables=tables,
    )
    if tokenized == payload:
        raise ProtectionError("none of the declared protected paths matched the result")
    return ProtectedPayload(
        value=tokenized, text=json.dumps(tokenized, ensure_ascii=False)
    )


def protect_json_text(
    text: str,
    *,
    tool_name: str,
    session_id: str,
    config: BlindfoldConfig,
    store: TokenStore,
) -> ProtectedPayload:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtectionError("the protected result is not JSON") from exc
    return protect_payload(
        payload,
        tool_name=tool_name,
        session_id=session_id,
        config=config,
        store=store,
    )


def session_id(event: dict[str, Any]) -> str:
    value = event.get("session_id")
    if not isinstance(value, str) or not value.strip():
        raise ProtectionError("the host event has no usable session_id")
    return value


def safe_reason(tool_name: str, detail: str) -> str:
    """Build feedback from metadata only; never echo an exception or result."""
    return f"Blindfold stopped {tool_name or 'the configured tool'}: {detail}"
