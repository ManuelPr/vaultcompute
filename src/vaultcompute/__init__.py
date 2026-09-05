"""VaultCompute — privacy proxy for LLM tool calls."""

from vaultcompute.config import describe_config
from vaultcompute.core.capabilities import TableQueryCapability
from vaultcompute.core.rehydrator import PLACEHOLDER_PROMPT, rehydrate
from vaultcompute.core.tokenizer import describe_schema
from vaultcompute.errors import ProtectionError
from vaultcompute.session import VaultComputeSession

__all__ = [
    "PLACEHOLDER_PROMPT",
    "VaultComputeSession",
    "ProtectionError",
    "TableQueryCapability",
    "describe_config",
    "describe_schema",
    "rehydrate",
]
