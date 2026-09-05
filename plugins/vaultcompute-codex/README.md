# VaultCompute for Codex

**Status: experimental guardrail.** This is not a complete boundary around all
tools Codex can expose.

This integration protects configured fields in results from local tools before
Codex continues its reasoning. It reuses VaultCompute's local SQLite vault and the
operation tools selected by `compute.mode`; arbitrary Python is absent by default.

Codex does not currently expose a display-only message hook. The model and the
user therefore both see placeholders such as `⟦tok_…⟧`; the plugin does not put
the real values back on screen. This is intentional and is not feature parity
with the Claude Code integration.

## Requirements

1. Install the repository so `vaultcompute` is on `PATH`.
2. Put `vaultcompute.yaml` in the directory from which Codex starts.
3. Use `storage.backend: sqlite`, because every hook and the MCP server runs in
   a separate process.
4. Configure only structured JSON tool results.

Example:

```yaml
storage:
  backend: sqlite
  path: ./vault.db

compute:
  mode: controlled

schemas:
  mcp__hr__get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

## Coverage boundary

Codex runs these hooks for shell commands, unified execution, file patches,
MCP tools, and most other local function tools. Hosted tools such as WebSearch
do not pass through the local hook path, and specialized tools can opt out.
VaultCompute therefore does not claim to protect those paths.

For a configured hooked tool, an unreadable or non-matching result is replaced
with a safe VaultCompute error instead of being passed through. For a tool path
that never fires a Codex hook, no plugin can make that guarantee.
