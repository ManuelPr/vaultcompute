# Changelog

All notable changes to VaultCompute are documented here.

## Unreleased

### Security

- Strict Mode A and Claude Code reject protected MCP responses containing
  unsupported `structuredContent`, which previously remained in cleartext.
- Resource schema merging preserves both full coverage and required-path
  checks across overlapping globs, without nesting placeholders.
- Resource `tables` declarations are rejected at config load until supported;
  use `sensitive_fields` to hide a resource's array as an opaque value.
- Hook vault initialization failures now emit the host's blocking response.
- Schema overlap validation now includes intersecting wildcards and indices.

## 0.1.0 - 2026-09-04

First public release candidate.

The project was renamed from its unpublished working name to **VaultCompute**;
the distribution, Python package, CLI, configuration file and operation tools
now use the `vaultcompute` / `vault_*` naming consistently.

### Added

- Fail-closed Mode B `VaultComputeSession` for synchronous and asynchronous tool
  protection, exact capability-authorized table queries, model instructions and
  final rendering.
- Strict Mode A stdio MCP proxy for declared JSON tool results and resources.
- Experimental Claude Code and Codex host adapters.
- Memory and SQLite token stores, with optional AES-256-GCM encryption at rest.
- Controlled collective-table operations and explicit `python_unsafe` compute.
- Session-bound policy, lineage, compute-attempt quotas and transcript audit.

### Security

- Required protected paths and strict unsupported-shape handling fail closed.
- Exact table capabilities bind session, table, complete operations and expiry.
- Adversarial Mode B tests cover shape drift, adaptive threshold changes,
  forged placeholders and cross-session access.

Known structural and temporary limits are maintained in
[`LIMITATIONS.md`](LIMITATIONS.md).
