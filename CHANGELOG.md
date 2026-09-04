# Changelog

All notable changes to Blindfold are documented here.

## 0.1.0 - 2026-09-04

First public release candidate.

### Added

- Fail-closed Mode B `BlindfoldSession` for synchronous and asynchronous tool
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
