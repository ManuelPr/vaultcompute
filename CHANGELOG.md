# Changelog

All notable changes to VaultCompute are documented here.

## 0.1.0a2 - 2026-09-15

- Replace personal names in examples, synthetic fixtures and documentation with
  generic English names, keeping the same salaries and expected behavior.

## 0.1.0a1 - 2026-09-14

First alpha of the Mode B library, with memory and SQLite storage, optional
encryption and the CLI. Mode A remains a beta proxy; Modes C/D are experimental
host adapters.

### Distribution

- MIT SPDX metadata and explicit source-archive contents.
- Clean wheel and source installs checked across the supported CI matrix.
- Manual, tag-bound Trusted Publishing through TestPyPI before PyPI.
- Runnable controlled-query demo, pilot protocol and private security reporting.
- Python selection in CI now explicitly follows the declared matrix.

### Security

- Strict Mode A and Claude Code reject protected MCP responses containing
  unsupported `structuredContent`, which previously remained in cleartext.
- Resource schema merging preserves both full coverage and required-path
  checks across overlapping globs, without nesting placeholders.
- Resource `tables` declarations are rejected at config load until supported;
  use `sensitive_fields` to hide a resource's array as an opaque value.
- Hook vault initialization failures now emit the host's blocking response.
- Schema overlap validation now includes intersecting wildcards and indices.

## Development snapshot - 2026-09-04 (unpublished)

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
