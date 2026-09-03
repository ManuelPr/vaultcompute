"""Trusted-side capabilities for exact table operations.

Blindfold deliberately does not ask another model whether an agent's query is
"close enough" to the user's request. A Mode B application may instead issue
one of these objects after it has interpreted and, where appropriate, confirmed
the request. The object never enters model-controlled JSON: it is authority
held by the application and matched against the proposed operation exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class TableQueryCapability:
    """Authority for one exact query on one table in one session."""

    session_id: str
    table_token: str
    canonical_ops: str
    expires_at: datetime

    @classmethod
    def issue(
        cls,
        *,
        session_id: str,
        table_token: str,
        ops: list[dict[str, Any]],
        expires_at: datetime,
    ) -> TableQueryCapability:
        if expires_at.tzinfo is None:
            raise ValueError("capability expiry must be timezone-aware")
        return cls(
            session_id=session_id,
            table_token=table_token,
            canonical_ops=_canonical(ops),
            expires_at=expires_at,
        )

    def authorize(self, *, session_id: str, table_token: str, ops: list[dict]) -> None:
        if datetime.now(tz=timezone.utc) >= self.expires_at:
            raise ValueError("table query capability has expired")
        if session_id != self.session_id:
            raise ValueError("table query capability belongs to another session")
        if table_token != self.table_token:
            raise ValueError("table query capability names another table")
        if self.canonical_ops != _canonical(ops):
            raise ValueError("table query was not authorized by the trusted application")


def _canonical(ops: list[dict[str, Any]]) -> str:
    try:
        return json.dumps(ops, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("capability operations must be JSON-serializable") from exc
