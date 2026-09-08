# Host adapters: exact guarantees and limits

VaultCompute's privacy core is host-independent. A host adapter is the small layer
that answers four concrete questions for one client:

1. When can VaultCompute inspect or stop a tool call?
2. In what data shape does that host return the result?
3. How can the original result be replaced before the next model request?
4. Can the final answer be changed only on screen, without changing the
   transcript the model will read later?

The fourth question is why this is not merely another personal-information
filter. A filter can replace a salary with a token. A useful interactive client
must also let the human read the salary without putting it back into the model's
next input.

Both shipped host adapters are experimental. Claude Code currently has the
stronger UX because it exposes display-only replacement; Codex is a local-path
guardrail. Neither adapter defines the security model of the core, and neither
claims coverage for host paths that do not emit the required events.

## Capability matrix

| Capability | Claude Code | Codex | Generic MCP client | Client you build |
|---|---|---|---|---|
| Inspect supported local tool result | yes | yes | only inside the wrapped MCP server | yes |
| Replace result before next model request | yes | yes | yes for wrapped MCP traffic | yes |
| Stop unsupported configured call before execution | yes | not needed for admitted local JSON; unavailable for hosted paths | only by refusing it in the server/proxy | yes |
| Reveal only on screen | **yes** | **no** | **no** | yes, if the client owns rendering |
| Hosted tools covered | host-dependent | no | no | only if your loop owns them |
| User sees real values | yes | no | no | yes |

“Yes” does not mean “every future tool shape.” It means the host supplies the
required lifecycle event and VaultCompute has an explicit adapter for the result
being admitted.

## Claude Code contract

VaultCompute currently admits:

- MCP results containing exactly one text part whose text is JSON. Both the
  observed list form and the object-with-`content` form are accepted. Objects
  containing `structuredContent` are refused: that parallel representation is
  not covered by this text adapter. The strict Mode A proxy also refuses it.
- `Bash` and `PowerShell` structured results when `stdout` is JSON and `stderr`
  is empty. The full object is copied and only `stdout` is replaced.
- The older flat `tool_output` string observed in previous Claude Code events,
  for compatibility.

Claude Code requires a replacement for a built-in tool to match that tool's
output shape. Returning a generic string for every built-in is unsafe because
the host can ignore a shape-invalid replacement and retain the original. That
is why configured built-ins such as `Read` and `WebFetch` are currently denied
by `PreToolUse`: the project has not audited and implemented their output
adapters yet.

After an admitted tool runs, three cases exist:

1. Declared paths match: VaultCompute stores the values and returns the same result
   shape with placeholders.
2. The result is not an accepted JSON shape: VaultCompute stops before another
   model request.
3. The JSON is valid but no declared path matches: VaultCompute also stops. This
   is treated as response-shape drift, not as a successful empty rewrite.

A missing or empty `session_id` is also a refusal. Host processes share a
SQLite vault, so silently mapping malformed events to one common identity would
collapse session isolation.

`MessageDisplay` then replaces placeholders only in the rendered text. The
transcript and Claude's context keep the placeholders. This is the capability
that gives Mode C its complete user experience.

The guarantee begins at the hook boundary. Claude Code may record local
telemetry containing the original output before the post-tool hook runs; that
storage is outside VaultCompute's control.

## Codex contract

Codex sends a structured `tool_response` to `PostToolUse`. VaultCompute accepts a
direct JSON object/list, a JSON string, a one-part MCP text result, or JSON in a
shell result's `stdout` when `stderr` is empty. It returns the protected JSON as
the post-tool stop text with `continue: false`. Codex replaces the original
result with that feedback before the model continues.

The local hook path covers shell commands, unified execution, file patches,
MCP tools, and most local function tools. Hosted tools such as web search do
not use that path, and specialized paths may opt out. A tool path that never
fires the hook cannot be protected by this plugin.

Codex has no display-only lifecycle event. VaultCompute therefore never resolves
a placeholder in ordinary Codex feedback: doing so would show the value to the
model as well as the user. The user-visible token is a product limitation, not
a tokenization bug.

The choice of `continue: false` is also deliberate for Codex's JavaScript code
mode: it replaces the model-visible result without rejecting the nested tool
promise. Codex's alternative blocking decision protects the result too, but
turns that promise into an error.

## Compatibility verification

The ordinary suite tests VaultCompute's adapters with fixed event fixtures and
checks that both plugin files stay aligned with the command-line dispatcher:

```bash
uv run pytest tests/unit/test_hooks.py tests/unit/test_codex_hooks.py \
  tests/integration/test_host_plugin_contracts.py
```

Fixtures detect regressions in this repository. Only a real host run detects a
host release that changed when a hook fires or which result shape it sends.
Claude Code therefore also has a paid, opt-in verification:

```bash
uv tool install . --force
uv run python scripts/verify_claude_code.py \
  --expect-version "<exact output of claude --version>"
```

The version check happens before the paid turn. The script calls a fake HR MCP
tool with a unique canary, finds the persisted transcript, and requires both a
vault record and a placeholder while rejecting any appearance of the canary.
Record the exact successful version in a release note; do not replace it with
“latest,” because compatibility is a statement about a concrete host build.

There is not yet an equivalent paid Codex script. The local Codex contract is
covered by fixtures and by the plugin validator; a release should not claim
end-to-end Codex verification until a real hosted turn and its persisted event
record can be audited in the same way.

## When a custom Codex client becomes worth building

Do not build one merely to make the integration look symmetric. Build it only
when all of these are true:

1. **Users need readable final values inside a Codex-based experience.** If
   placeholders are acceptable, the plugin already provides the useful part.
2. **That need is repeated, not hypothetical.** A practical threshold is at
   least three real workflows or design partners blocked specifically by the
   missing display-only reveal.
3. **The client can own the final rendering boundary.** It must receive the
   placeholder answer, rehydrate it locally after authorization, and ensure the
   revealed text is not appended to the model's next turn.
4. **The tool surface can be enumerated and tested.** A client that mixes local
   and hosted tools without knowing which results it controls cannot honestly
   promise complete protection.
5. **The extra product is maintainable.** Authentication, streaming, approvals,
   conversation persistence, updates, and compatibility testing become your
   responsibility, not OpenAI's.

Until those conditions are met, the rational sequence is: keep one privacy
core, maintain small host adapters, collect evidence that visible placeholders
are the actual adoption blocker, and only then invest in a client that owns the
last display hop.

Current host behavior should always be checked against the
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks) and the
[official Codex hooks documentation](https://developers.openai.com/codex/hooks).
