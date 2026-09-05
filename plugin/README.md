# VaultCompute — Claude Code plugin

**Status: experimental host adapter.** Its boundary depends on the event and
replacement contracts of the installed Claude Code version.

Protects declared fields in the supported result shapes, offers the operation
profile selected by the operator, and shows authorized values on screen.

## The five pieces

| Piece | Effect |
|---|---|
| `SessionStart` hook | Before the first prompt, tells the model which paths come back as `⟦tok_…⟧`, what they mean, which operation profile is active, and to reproduce placeholders verbatim. |
| `PreToolUse` hook | Refuses a configured built-in tool before it runs when VaultCompute has no tested way to rebuild that tool's result safely. |
| `PostToolUse` hook | Replaces declared fields before the model sees them, while preserving the result shape Claude Code expects. The audited adapters currently cover MCP tools with one JSON text part and JSON written to standard output by `Bash` or `PowerShell`. |
| `vaultcompute` MCP server | Offers `vault_table` for declared tables by default. `vault_compute` appears only with `compute.mode: python_unsafe`. |
| `MessageDisplay` hook | Puts the real values back **on screen only**. The transcript keeps the placeholders, so nothing re-enters the model's context on the next turn. |

The first and third exist because a host's hook system **cannot add a tool or
rewrite a tool description**. The CLI proxy does both by editing the
`tools/list` response as it goes past; here the schema briefing has to arrive as
session context, and `vault_compute` has to arrive as a real MCP server.

Which fields are sensitive is declared per tool in `vaultcompute.yaml`. Nothing is
guessed: a field you did not declare passes through in cleartext.

## Requirements

1. **The `vaultcompute` command on your `PATH`.** Both the hooks and the MCP server
   are invoked by name. From a clone:

   ```bash
   uv tool install .        # or: pipx install .
   ```

2. **`storage.backend: sqlite`.** Every hook run is a separate process, and the
   MCP server is a third one. With a memory vault they would each get their own
   empty dictionary, so they refuse to run instead of minting tokens nobody can
   resolve.

3. **A `vaultcompute.yaml` in the directory you start Claude Code from**, or
   `--config` added to the commands in `hooks/hooks.json` and `.mcp.json`.

## Setup

```yaml
# vaultcompute.yaml
storage:
  backend: sqlite
  path: ./vault.db
  # encrypt_at_rest: true   # needs VAULTCOMPUTE_VAULT_KEY in every hook's env

tokens:
  default_ttl: 3600

compute:
  mode: controlled          # use python_unsafe only for a cooperative model

schemas:
  mcp__hr__get_salary:      # Claude Code names MCP tools mcp__<server>__<tool>
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

Then:

```bash
claude --plugin-dir ./plugin
```

Run `/plugin` and check the **Errors** tab if the hooks or the MCP server do not
start.

## What you will see

Ask something that reaches a protected tool. The model works with placeholders
throughout and, for declared tables, uses controlled operations. Your screen
shows authorized real values in the final answer.

To confirm it is actually working rather than quietly doing nothing, look at the
transcript: the tool results and the assistant message there should still
contain `⟦tok_…⟧`. `vaultcompute audit <transcript>` automates exactly that check —
point it at the session's `.jsonl` file under `~/.claude/projects/<project>/`
and it cross-references every vault record against the transcript text and
reports any hidden value that made it through.

## Behaviour worth knowing before you rely on it

- **A tool with declared fields whose result is not JSON gets blocked**, not
  passed through. VaultCompute cannot tell which part of a free-text answer is
  sensitive, and letting it through would send the values it was asked to
  protect straight to the model.
- **A configured built-in without an audited result adapter is refused before
  it runs.** Today that includes tools such as `Read` and `WebFetch`. They are
  not silently treated like `Bash`: each host tool has its own result shape,
  and returning the wrong shape can make Claude Code ignore the replacement.
- **A configured result whose declared paths no longer match stops the turn.**
  This catches an upstream response change at the privacy boundary instead of
  letting an apparently protected tool continue with a cleartext result.
- **Undeclared tools are untouched.** That is intended, and it means the
  protection is exactly as good as your `schemas` section.
- **The MCP server infers the session from the tokens it is given.** An MCP
  connection carries no session identity, so the server reads the session off
  the input tokens and refuses to mix two. Possession of a token is therefore
  treated as proof of belonging to its session.
- **The vault file holds cleartext unless you turn encryption on.** Set
  `encrypt_at_rest: true` and export `VAULTCOMPUTE_VAULT_KEY` (32 bytes, base64;
  `pip install vaultcompute[encryption]`). Every hook process and the MCP server
  need that variable in their environment. Without it, protect the file with
  filesystem permissions and keep TTLs short.
- **`MessageDisplay` fires on every assistant message** with a 10-second budget;
  the hook returns immediately when the text contains no placeholders. The text
  to check arrives under `delta` (the newly-completed-lines chunk), not the
  whole message.
- **If `python_unsafe` is enabled, secret compute attempts are lineage-rate-limited** —
  `compute.max_calls_per_token` (default 8) within `compute.rate_window_s`
  (default 60) — to bound how fast a model probing for a hidden value one bit
  at a time (see [`../LIMITATIONS.md`](../LIMITATIONS.md)) can extract it.
  Attempts reserve quota before execution, including failures and timeouts.
  Derived tokens share the original secrets' budget, and SQLite reservations
  are atomic across hook/MCP processes. Blocks are logged and shown by
  `vaultcompute audit`. Ordinary reuse spread across a session is unaffected.
- **Host telemetry is outside this guarantee.** VaultCompute rewrites the result
  before the next model request. A host may have recorded the original tool
  result locally or in its own telemetry before the hook ran.

## Compatibility check against a real Claude Code version

Unit and integration tests lock down the event shapes VaultCompute accepts. They
cannot prove that a newly installed Claude Code release still invokes those
hooks in the same way. The opt-in check below starts one real model turn, so it
uses your Claude allowance:

```bash
uv tool install . --force
uv run python scripts/verify_claude_code.py --expect-version "<exact output of claude --version>"
```

It calls the fake HR tool with a unique canary, locates Claude Code's persisted
transcript, and checks that the canary is absent while a VaultCompute placeholder
is present. Authentication is checked before the paid turn; a missing login or
version mismatch exits without starting it.

See [`../LIMITATIONS.md`](../LIMITATIONS.md) for the full inventory.
