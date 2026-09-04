"""Fail-closed, in-process façade for Mode B integrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from blindfold.config import (
    BlindfoldConfig,
    build_token_store,
    describe_config,
    required_table_paths_for,
    schema_fields_for,
    table_schemas_for,
)
from blindfold.core.capabilities import TableQueryCapability
from blindfold.core.policy import SessionBoundPolicy
from blindfold.core.protection import protect_result
from blindfold.core.rehydrator import rehydrate
from blindfold.errors import ProtectionError
from blindfold.ports.policy import DetokenizePolicy
from blindfold.ports.token_store import TokenStore
from blindfold.tools.blindfold_table import handle_blindfold_table

__all__ = ["BlindfoldSession"]


class BlindfoldSession:
    """Own the safe Mode B path from tool result to user-visible answer."""

    def __init__(
        self,
        config: BlindfoldConfig,
        *,
        session_id: str,
        store: TokenStore | None = None,
        policy: DetokenizePolicy | None = None,
    ) -> None:
        if not session_id:
            raise ValueError("session_id must not be empty")
        self.config = config
        self.session_id = session_id
        self.store = store if store is not None else build_token_store(config)
        self.policy = policy if policy is not None else SessionBoundPolicy()

    @property
    def model_instructions(self) -> str:
        """The placeholder rules and schema description for the model."""
        instructions = describe_config(self.config)
        if instructions is None:
            raise ProtectionError("the configuration has no protected tool schemas")
        return instructions

    def protect_tool_result(self, tool_name: str, result: Any) -> Any:
        """Validate and tokenize a result, or raise without returning raw data."""
        schema = self.config.schemas.get(tool_name)
        if schema is None or not (schema.sensitive_fields or schema.tables):
            raise ProtectionError(f"tool {tool_name!r} has no protected schema")

        ttl = datetime.now(tz=timezone.utc) + timedelta(
            seconds=self.config.tokens.default_ttl
        )
        return protect_result(
            result,
            source_name=tool_name,
            fields=schema_fields_for(self.config, tool_name),
            tables=table_schemas_for(self.config, tool_name),
            required_table_paths=required_table_paths_for(self.config, tool_name),
            store=self.store,
            session_id=self.session_id,
            ttl=ttl,
        )

    def call_protected_tool(
        self,
        tool_name: str,
        invoke: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Invoke a synchronous tool and expose only its protected result."""
        return self.protect_tool_result(tool_name, invoke(*args, **kwargs))

    async def call_protected_tool_async(
        self,
        tool_name: str,
        invoke: Callable[..., Awaitable[Any]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Invoke an asynchronous tool and expose only its protected result."""
        return self.protect_tool_result(tool_name, await invoke(*args, **kwargs))

    def execute_authorized_query(
        self,
        proposed_query: dict[str, Any],
        *,
        capability: TableQueryCapability,
    ) -> str:
        """Execute one table query that exactly matches trusted authority."""
        if capability is None:
            raise ValueError("execute_authorized_query requires a capability")
        return handle_blindfold_table(
            proposed_query,
            store=self.store,
            policy=self.policy,
            session_id=self.session_id,
            ttl_seconds=self.config.tokens.default_ttl,
            capability=capability,
            require_capability=True,
        )

    def render_final_answer(self, model_answer: str) -> str:
        """Resolve placeholders only at the final user-visible boundary."""
        if not isinstance(model_answer, str):
            raise TypeError("model_answer must be a string")
        return rehydrate(model_answer, self.session_id, self.store, self.policy)
