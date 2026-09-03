"""MemoryTokenStore — in-memory implementation of TokenStore.

Single-process, but not single-threaded: the proxy runs blind compute in a
worker thread while the other pump keeps tokenizing tool results on the event
loop, so two threads write this store at once whenever the model sends a tool
call and a compute in the same turn. Every method takes a lock, the same way
`SQLiteTokenStore` does — without it the sweep's scan raises
`dictionary changed size during iteration` and takes a pump down with it.
Reentrant, because `put` sweeps and `resolve` goes through `get`.

Expiry is enforced twice, for two different reasons. `get` hides expired
records so a stale token never resolves. `put` sweeps them, on an interval, so
an expired value stops *existing*: a TTL that only governs resolvability would
leave yesterday's salary sitting in this process's memory in cleartext, and
short TTLs are the mitigation this project recommends. The sweep lives here
rather than in the proxy because the in-process library builds its own store
and never goes near the proxy.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from blindfold.core.compute_attempts import (
    ATTEMPT_OUTCOMES,
    ComputeAttempt,
    ComputeRateLimitError,
)
from blindfold.core.lineage import VaultRecord
from blindfold.ports.token_store import TokenStore


class MemoryTokenStore(TokenStore):
    #: Seconds between sweeps. Set it lower on an instance if a deployment
    #: wants expired values gone sooner than this.
    purge_interval_s: float = 60.0

    def __init__(self) -> None:
        self._records: dict[str, VaultRecord] = {}
        self._compute_attempts: dict[str, ComputeAttempt] = {}
        self._lock = threading.RLock()
        self._last_purge = self._now()

    def put(self, record: VaultRecord) -> None:
        with self._lock:
            self._sweep_if_due()
            self._records[record.token] = record

    def _sweep_if_due(self) -> None:
        # ponytail: linear scan over every record, amortized by the interval.
        # An expiry-ordered index is the upgrade if a vault ever gets big
        # enough for the scan to show up.
        now = self._now()
        if (now - self._last_purge).total_seconds() < self.purge_interval_s:
            return
        self._last_purge = now
        self.purge_expired(now)

    def get(self, token: str) -> VaultRecord | None:
        with self._lock:
            rec = self._records.get(token)
        if rec is None:
            return None
        if rec.ttl <= self._now():
            return None
        return rec

    def resolve(self, token: str) -> Any | None:
        rec = self.get(token)
        return rec.value if rec is not None else None

    def find_by_session(self, session_id: str) -> list[VaultRecord]:
        now = self._now()
        with self._lock:
            return [r for r in self._records.values() if r.session_id == session_id and r.ttl > now]

    def invalidate_cascade(self, token: str) -> int:
        with self._lock:
            return self._invalidate_cascade(token)

    def _invalidate_cascade(self, token: str) -> int:
        if token not in self._records:
            return 0
        to_remove = {token}
        changed = True
        while changed:
            changed = False
            for t, r in self._records.items():
                if t in to_remove:
                    continue
                if any(inp in to_remove for inp in r.lineage.inputs):
                    to_remove.add(t)
                    changed = True
        for t in to_remove:
            del self._records[t]
        return len(to_remove)

    def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = now if now is not None else self._now()
        with self._lock:
            expired = [t for t, r in self._records.items() if r.ttl <= cutoff]
            for t in expired:
                del self._records[t]
            expired_attempts = [
                attempt_id
                for attempt_id, attempt in self._compute_attempts.items()
                if attempt.expires_at <= cutoff
            ]
            for attempt_id in expired_attempts:
                del self._compute_attempts[attempt_id]
        return len(expired)

    def reserve_compute_attempt(
        self,
        *,
        session_id: str,
        root_tokens: tuple[str, ...],
        input_tokens: tuple[str, ...],
        code_digest: str,
        expires_at: datetime,
        max_attempts: int,
        window_s: int,
    ) -> str:
        attempt_id = f"attempt_{secrets.token_hex(16)}"
        now = self._now()
        cutoff = now.timestamp() - window_s
        roots = tuple(sorted(set(root_tokens)))
        blocked_count = 0
        with self._lock:
            recent = [
                attempt
                for attempt in self._compute_attempts.values()
                if attempt.session_id == session_id
                and attempt.created_at.timestamp() >= cutoff
                and attempt.outcome != "blocked"
            ]
            for root in roots:
                blocked_count = max(
                    blocked_count,
                    sum(1 for attempt in recent if root in attempt.root_tokens),
                )
            blocked = max_attempts > 0 and blocked_count >= max_attempts
            self._compute_attempts[attempt_id] = ComputeAttempt(
                attempt_id=attempt_id,
                session_id=session_id,
                root_tokens=roots,
                input_tokens=tuple(input_tokens),
                created_at=now,
                expires_at=expires_at,
                code_digest=code_digest,
                outcome="blocked" if blocked else "started",
            )
        if blocked:
            raise ComputeRateLimitError(
                recent_count=blocked_count,
                max_attempts=max_attempts,
                window_s=window_s,
            )
        return attempt_id

    def finish_compute_attempt(self, attempt_id: str, outcome: str) -> None:
        if outcome not in ATTEMPT_OUTCOMES or outcome in ("started", "blocked"):
            raise ValueError(f"invalid terminal compute outcome: {outcome!r}")
        with self._lock:
            attempt = self._compute_attempts.get(attempt_id)
            if attempt is None:
                raise KeyError(f"unknown compute attempt: {attempt_id}")
            self._compute_attempts[attempt_id] = replace(attempt, outcome=outcome)

    def find_compute_attempts(self, session_id: str) -> list[ComputeAttempt]:
        now = self._now()
        with self._lock:
            return sorted(
                (
                    attempt
                    for attempt in self._compute_attempts.values()
                    if attempt.session_id == session_id and attempt.expires_at > now
                ),
                key=lambda attempt: attempt.created_at,
            )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=timezone.utc)
