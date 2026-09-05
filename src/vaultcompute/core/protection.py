"""One fail-closed protection path shared by every integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from vaultcompute.core.lineage import TableSchema
from vaultcompute.core.tokenizer import SchemaField, _resolve_paths, tokenize_result
from vaultcompute.errors import ProtectionError
from vaultcompute.ports.token_store import TokenStore


def protect_results(
    results: list[Any],
    *,
    source_name: str,
    fields: list[SchemaField],
    tables: list[tuple[str, TableSchema]],
    required_table_paths: set[str],
    store: TokenStore,
    session_id: str,
    ttl: datetime,
) -> list[Any]:
    """Protect decoded results after validating every required declaration."""
    required = {field.path for field in fields if field.required} | required_table_paths
    matched: set[str] = set()
    invalid_tables: set[str] = set()

    for result in results:
        for field in fields:
            if _resolve_paths(result, field.path):
                matched.add(field.path)
        for path, _schema in tables:
            matches = _resolve_paths(result, path)
            if matches:
                matched.add(path)
            if any(not isinstance(value, list) for _, value in matches):
                invalid_tables.add(path)

    missing = required - matched
    if missing:
        raise ProtectionError(
            f"{source_name!r} is missing required protected path(s): "
            + ", ".join(sorted(missing))
        )
    if invalid_tables:
        raise ProtectionError(
            "protected table path(s) did not contain lists: "
            + ", ".join(sorted(invalid_tables))
        )

    return [
        tokenize_result(
            result,
            source_name,
            fields,
            store,
            session_id,
            ttl,
            tables=tables,
        )
        for result in results
    ]


def protect_result(result: Any, **kwargs: Any) -> Any:
    """Single-result convenience wrapper around :func:`protect_results`."""
    return protect_results([result], **kwargs)[0]
