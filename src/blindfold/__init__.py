"""Blindfold — privacy proxy for LLM tool calls."""

from blindfold.config import describe_config
from blindfold.core.capabilities import TableQueryCapability
from blindfold.core.rehydrator import PLACEHOLDER_PROMPT, rehydrate
from blindfold.core.tokenizer import describe_schema
from blindfold.errors import ProtectionError
from blindfold.session import BlindfoldSession

__all__ = [
    "PLACEHOLDER_PROMPT",
    "BlindfoldSession",
    "ProtectionError",
    "TableQueryCapability",
    "describe_config",
    "describe_schema",
    "rehydrate",
]
