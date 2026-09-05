"""Metadata-only records for `vault_compute` abuse controls.

Attempts live beside the vault records because the quota must be shared by
threads, processes and host adapters.  They contain token identifiers and a
code digest, never resolved values or exception messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


ATTEMPT_OUTCOMES = ("started", "succeeded", "failed", "timeout", "blocked")


@dataclass(frozen=True)
class ComputeAttempt:
    attempt_id: str
    session_id: str
    root_tokens: tuple[str, ...]
    input_tokens: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    code_digest: str
    outcome: str


class ComputeRateLimitError(ValueError):
    """An atomic quota reservation was refused and logged."""

    def __init__(self, *, recent_count: int, max_attempts: int, window_s: int) -> None:
        self.recent_count = recent_count
        self.max_attempts = max_attempts
        self.window_s = window_s
        super().__init__(
            f"compute rate limit: this token lineage has made {recent_count} "
            f"attempts in the last {window_s}s (limit {max_attempts})"
        )
