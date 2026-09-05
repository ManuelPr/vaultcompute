"""DetokenizePolicy port — authorization for reveal and data operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from vaultcompute.core.lineage import VaultRecord


@dataclass(frozen=True)
class DetokenizeContext:
    session_id: str


class DetokenizePolicy(ABC):
    @abstractmethod
    def can_reveal(self, context: DetokenizeContext, record: VaultRecord) -> bool: ...

    @abstractmethod
    def can_compute(self, context: DetokenizeContext, record: VaultRecord) -> bool: ...

    @abstractmethod
    def can_query(self, context: DetokenizeContext, record: VaultRecord) -> bool: ...
