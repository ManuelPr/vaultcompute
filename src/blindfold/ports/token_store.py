"""TokenStore port — abstract vault interface."""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from blindfold.core.lineage import VaultRecord


class TokenStore(ABC):
    @staticmethod
    def mint_token() -> str:
        """Mint a fresh token.

        On the port, not on an implementation: the delimiters and the hex width
        are a contract between whoever mints and the rehydrator's regex, not a
        detail of where records happen to be kept.

        16 bytes, not 4. Tokens become bearer capabilities in host mode, where
        possession is used to recover a session, so guessing matters as well
        as collision. `put` is an upsert in both stores, so two records sharing
        a token means the second silently replaces the first. 128 random bits
        gives the security margin expected of a bearer credential while
        remaining tiny in a JSON result.
        """
        return f"⟦tok_{secrets.token_hex(16)}⟧"

    @abstractmethod
    def put(self, record: VaultRecord) -> None: ...

    @abstractmethod
    def get(self, token: str) -> VaultRecord | None: ...

    @abstractmethod
    def resolve(self, token: str) -> Any | None: ...

    @abstractmethod
    def find_by_session(self, session_id: str) -> list[VaultRecord]: ...

    @abstractmethod
    def invalidate_cascade(self, token: str) -> int: ...

    @abstractmethod
    def purge_expired(self, now: datetime | None = None) -> int: ...
