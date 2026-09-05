"""Adapters for agent hosts that expose lifecycle hooks.

The privacy core does not know whether a result came from Claude Code, Codex,
or an application-owned loop. Host adapters own the unstable part: event
field names, result shapes, and the exact response that prevents the original
result from reaching the model.
"""

from vaultcompute.hosts import claude_code, codex

CLAUDE_CODE = "claude-code"
CODEX = "codex"
HOSTS = (CLAUDE_CODE, CODEX)

__all__ = ["CLAUDE_CODE", "CODEX", "HOSTS", "claude_code", "codex"]
