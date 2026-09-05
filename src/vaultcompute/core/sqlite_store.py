"""SQLiteTokenStore — a vault that outlives the process that filled it.

Two things need this, and they need it for different reasons.

A multi-process deployment needs it because the placeholders already sent to a
model do not die with a restart: they sit in conversation history, in logs, in
the application's database, and a memory vault leaves them pointing at values
that no longer exist anywhere.

Host integrations need it because tokenizing and rehydrating can happen in
*different processes*. A Claude Code hook, for instance, is a fresh process per
invocation — the token minted while a tool result is rewritten has to still
resolve when the answer is displayed, seconds later, from somewhere else.

**Values are cleartext unless you ask for encryption and supply a key.** With
``encrypt=True`` each value is sealed with AES-256-GCM before it is written,
and the key must come from outside the file — ``VAULTCOMPUTE_VAULT_KEY`` in the
environment, or passed in. There is deliberately no way to keep the key beside
the database, because that is decoration rather than encryption. Without
encryption the store narrows the file's permissions where the platform
supports it, and that is the whole of its protection.

Only the value is sealed. The token, session and lineage stay readable: they
are what the store queries on, and none of them is the secret. That does leak
shape — how many records exist, when, and in which session — to anyone holding
the file.

Uses `sqlite3` from the standard library. Encryption needs `cryptography`,
which is an optional extra: `pip install vaultcompute[encryption]`.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vaultcompute.core.compute_attempts import (
    ATTEMPT_OUTCOMES,
    ComputeAttempt,
    ComputeRateLimitError,
)
from vaultcompute.core.lineage import Column, Lineage, Policy, TableSchema, VaultRecord
from vaultcompute.ports.token_store import TokenStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    token         TEXT PRIMARY KEY,
    value         TEXT NOT NULL,   -- JSON
    dtype         TEXT NOT NULL,
    semantic_type TEXT,
    unit          TEXT,
    session_id    TEXT NOT NULL,
    created_at    TEXT NOT NULL,   -- ISO 8601, for exact reconstruction
    ttl           TEXT NOT NULL,   -- ISO 8601, for exact reconstruction
    ttl_epoch     REAL NOT NULL,   -- seconds, for range queries
    lineage       TEXT NOT NULL,   -- JSON
    policy        TEXT NOT NULL,   -- JSON
    table_schema  TEXT             -- JSON, collective tokens only
);
CREATE INDEX IF NOT EXISTS idx_records_session ON records(session_id);
CREATE INDEX IF NOT EXISTS idx_records_ttl ON records(ttl_epoch);
CREATE TABLE IF NOT EXISTS compute_attempts (
    attempt_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    root_tokens  TEXT NOT NULL,   -- JSON
    input_tokens TEXT NOT NULL,   -- JSON
    created_at   TEXT NOT NULL,
    created_epoch REAL NOT NULL,
    expires_at   TEXT NOT NULL,
    expires_epoch REAL NOT NULL,
    code_digest  TEXT NOT NULL,
    outcome      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_compute_attempts_session_time
    ON compute_attempts(session_id, created_epoch);
CREATE INDEX IF NOT EXISTS idx_compute_attempts_expiry
    ON compute_attempts(expires_epoch);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_KEY_ENV = "VAULTCOMPUTE_VAULT_KEY"


class VaultKeyError(RuntimeError):
    """Encryption was asked for and the key could not be used."""


def _load_cipher(key: bytes | None):
    """An AES-256-GCM cipher, or a refusal that says what to do about it."""
    if key is None:
        raw = os.environ.get(_KEY_ENV)
        if not raw:
            raise VaultKeyError(
                f"encrypt_at_rest needs a key in ${_KEY_ENV}: 32 bytes, base64. "
                f"Generate one with:  python -c \"import base64,os; "
                f"print(base64.b64encode(os.urandom(32)).decode())\"  — and keep it "
                f"somewhere other than next to the vault file, or it protects nothing."
            )
        try:
            key = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise VaultKeyError(f"${_KEY_ENV} is not valid base64") from exc
    if len(key) != 32:
        raise VaultKeyError(f"vault key must be 32 bytes, got {len(key)}")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise VaultKeyError(
            "encryption needs the `cryptography` package: pip install vaultcompute[encryption]"
        ) from exc
    return AESGCM(key)


class SQLiteTokenStore(TokenStore):
    #: Seconds between expiry sweeps, as in MemoryTokenStore.
    purge_interval_s: float = 60.0

    def __init__(
        self, path: str | Path, *, encrypt: bool = False, key: bytes | None = None
    ) -> None:
        self._path = Path(path)
        self._cipher = _load_cipher(key) if encrypt else None
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: autocommit. Every method here is a single
        # statement or an explicit transaction, and a vault that loses the last
        # write on a crash is worse than one that does not.
        # check_same_thread=False plus an explicit lock: the proxy runs blind
        # compute in a worker thread, so the connection outlives the thread
        # that made it. Every statement below goes through the lock.
        # Reentrant: put() sweeps, resolve() goes through get().
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        # WAL is what makes two processes on one file safe, which is the whole
        # reason this class exists. busy_timeout turns a concurrent write from
        # an immediate "database is locked" into a short wait.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._restrict_permissions()
        self._check_encryption_matches_file()
        self._last_purge = self._now()

    # --- TokenStore -------------------------------------------------------

    def put(self, record: VaultRecord) -> None:
        with self._lock:
            self._sweep_if_due()
            self._put(record)

    def _put(self, record: VaultRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO records "
            "(token, value, dtype, semantic_type, unit, session_id, created_at, "
            " ttl, ttl_epoch, lineage, policy, table_schema) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.token,
                self._seal(record.token, record.value),
                record.dtype,
                record.semantic_type,
                record.unit,
                record.session_id,
                record.created_at.isoformat(),
                record.ttl.isoformat(),
                record.ttl.timestamp(),
                json.dumps(
                    {
                        "op": record.lineage.op,
                        "inputs": list(record.lineage.inputs),
                        "code_digest": record.lineage.code_digest,
                        "tool": record.lineage.tool,
                        "path": record.lineage.path,
                    }
                ),
                json.dumps(
                    {
                        "reveal_to_frontend": record.policy.reveal_to_frontend,
                        "can_be_input_to_compute": record.policy.can_be_input_to_compute,
                        "can_be_input_to_query": record.policy.can_be_input_to_query,
                    }
                ),
                None
                if record.table is None
                else json.dumps(
                    [
                        {"name": c.name, "semantic_type": c.semantic_type, "unit": c.unit}
                        for c in record.table.columns
                    ]
                ),
            ),
        )

    def get(self, token: str) -> VaultRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM records WHERE token = ? AND ttl_epoch > ?",
                (token, self._now().timestamp()),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def resolve(self, token: str) -> Any | None:
        record = self.get(token)
        return record.value if record is not None else None

    def find_by_session(self, session_id: str) -> list[VaultRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM records WHERE session_id = ? AND ttl_epoch > ?",
                (session_id, self._now().timestamp()),
            ).fetchall()
        return [self._from_row(r) for r in rows]

    def invalidate_cascade(self, token: str) -> int:
        with self._lock:
            return self._invalidate_cascade(token)

    def _invalidate_cascade(self, token: str) -> int:
        exists = self._conn.execute(
            "SELECT 1 FROM records WHERE token = ?", (token,)
        ).fetchone()
        if exists is None:
            return 0

        # ponytail: loads every (token, inputs) pair and closes the descendant
        # set in Python, same shape as MemoryTokenStore. An edges table would
        # let SQLite do it with a recursive CTE; worth it only if lineage DAGs
        # get big enough to notice.
        edges = [
            (r["token"], tuple(json.loads(r["lineage"])["inputs"]))
            for r in self._conn.execute("SELECT token, lineage FROM records")
        ]
        to_remove = {token}
        changed = True
        while changed:
            changed = False
            for tok, inputs in edges:
                if tok in to_remove:
                    continue
                if any(i in to_remove for i in inputs):
                    to_remove.add(tok)
                    changed = True

        self._conn.executemany(
            "DELETE FROM records WHERE token = ?", [(t,) for t in to_remove]
        )
        return len(to_remove)

    def purge_expired(self, now: datetime | None = None) -> int:
        cutoff = (now if now is not None else self._now()).timestamp()
        with self._lock:
            cur = self._conn.execute("DELETE FROM records WHERE ttl_epoch <= ?", (cutoff,))
            self._conn.execute(
                "DELETE FROM compute_attempts WHERE expires_epoch <= ?", (cutoff,)
            )
            return cur.rowcount

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
        roots = tuple(sorted(set(root_tokens)))
        blocked_count = 0
        blocked = False
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    "SELECT root_tokens FROM compute_attempts "
                    "WHERE session_id = ? AND created_epoch >= ? AND outcome != 'blocked'",
                    (session_id, now.timestamp() - window_s),
                ).fetchall()
                recent_roots = [tuple(json.loads(row["root_tokens"])) for row in rows]
                for root in roots:
                    blocked_count = max(
                        blocked_count,
                        sum(1 for attempt_roots in recent_roots if root in attempt_roots),
                    )
                blocked = max_attempts > 0 and blocked_count >= max_attempts
                self._conn.execute(
                    "INSERT INTO compute_attempts "
                    "(attempt_id, session_id, root_tokens, input_tokens, created_at, "
                    " created_epoch, expires_at, expires_epoch, code_digest, outcome) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        attempt_id,
                        session_id,
                        json.dumps(roots),
                        json.dumps(tuple(input_tokens)),
                        now.isoformat(),
                        now.timestamp(),
                        expires_at.isoformat(),
                        expires_at.timestamp(),
                        code_digest,
                        "blocked" if blocked else "started",
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise
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
            cur = self._conn.execute(
                "UPDATE compute_attempts SET outcome = ? WHERE attempt_id = ?",
                (outcome, attempt_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"unknown compute attempt: {attempt_id}")

    def find_compute_attempts(self, session_id: str) -> list[ComputeAttempt]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM compute_attempts "
                "WHERE session_id = ? AND expires_epoch > ? ORDER BY created_epoch",
                (session_id, self._now().timestamp()),
            ).fetchall()
        return [
            ComputeAttempt(
                attempt_id=row["attempt_id"],
                session_id=row["session_id"],
                root_tokens=tuple(json.loads(row["root_tokens"])),
                input_tokens=tuple(json.loads(row["input_tokens"])),
                created_at=datetime.fromisoformat(row["created_at"]),
                expires_at=datetime.fromisoformat(row["expires_at"]),
                code_digest=row["code_digest"],
                outcome=row["outcome"],
            )
            for row in rows
        ]

    # --- housekeeping -----------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _sweep_if_due(self) -> None:
        now = self._now()
        if (now - self._last_purge).total_seconds() < self.purge_interval_s:
            return
        self._last_purge = now
        self.purge_expired(now)

    def _seal(self, token: str, value: Any) -> str:
        plain = json.dumps(value).encode("utf-8")
        if self._cipher is None:
            return plain.decode("utf-8")
        nonce = os.urandom(12)
        # The token is the associated data, so a ciphertext cannot be moved to
        # another row and still open.
        sealed = self._cipher.encrypt(nonce, plain, token.encode("utf-8"))
        return base64.b64encode(nonce + sealed).decode("ascii")

    def _open(self, token: str, stored: str) -> Any:
        if self._cipher is None:
            return json.loads(stored)
        blob = base64.b64decode(stored)
        try:
            plain = self._cipher.decrypt(blob[:12], blob[12:], token.encode("utf-8"))
        except Exception as exc:
            raise VaultKeyError(
                "a vault record would not open with this key — wrong key, or the "
                "file was tampered with"
            ) from exc
        return json.loads(plain)

    def _check_encryption_matches_file(self) -> None:
        """Refuse a key/file mismatch instead of failing at the first read."""
        want = "1" if self._cipher is not None else "0"
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'encrypted'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('encrypted', ?)", (want,)
            )
            return
        if row["value"] != want:
            was, now = ("encrypted", "cleartext") if row["value"] == "1" else ("cleartext", "encrypted")
            raise VaultKeyError(
                f"{self._path} was written {was} and is being opened {now}. "
                f"VaultCompute will not mix the two in one file."
            )

    def _restrict_permissions(self) -> None:
        # Owner-only on POSIX. Windows ignores the mode bits, which is why this
        # is documented as "narrows where supported" rather than as protection.
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def _from_row(self, row: sqlite3.Row) -> VaultRecord:
        return _from_row_with(self._open, row)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=timezone.utc)


def _from_row_with(open_value, row: sqlite3.Row) -> VaultRecord:
    lineage = json.loads(row["lineage"])
    policy = json.loads(row["policy"])
    return VaultRecord(
        token=row["token"],
        value=open_value(row["token"], row["value"]),
        dtype=row["dtype"],
        semantic_type=row["semantic_type"],
        unit=row["unit"],
        session_id=row["session_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        ttl=datetime.fromisoformat(row["ttl"]),
        lineage=Lineage(
            op=lineage["op"],
            inputs=tuple(lineage["inputs"]),
            code_digest=lineage["code_digest"],
            tool=lineage["tool"],
            path=lineage["path"],
        ),
        policy=Policy(
            reveal_to_frontend=policy["reveal_to_frontend"],
            can_be_input_to_compute=policy["can_be_input_to_compute"],
            # Existing vaults predate the split between constrained queries
            # and arbitrary compute, so preserve their former permissive
            # behaviour unless a newer record says otherwise.
            can_be_input_to_query=policy.get("can_be_input_to_query", True),
        ),
        table=None
        if row["table_schema"] is None
        else TableSchema(
            columns=tuple(Column(**c) for c in json.loads(row["table_schema"]))
        ),
    )
