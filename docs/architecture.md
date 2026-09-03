# Architecture

A developer's tour of how Blindfold actually works. Read this after the [README](../README.md) (which covers *why* the tool exists and what it promises) and before diving into the code.

- Reference spec: [`docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md`](superpowers/specs/2026-07-15-blindfold-mvp-design.md) — the formal MVP design.
- Known gaps: [`LIMITATIONS.md`](../LIMITATIONS.md) — what this document deliberately elides in favor of an idealized picture.

---

## 1. The problem, in one paragraph

When you connect an LLM to your private APIs via MCP tools, every tool response flows back into the model's context — and therefore to the LLM provider. Ask an agent *"What is Andrea's salary?"*, and the HR API's response (the actual number) is logged by Anthropic/OpenAI as part of the conversation. Traditional PII redaction proxies (Presidio, Philter) scrub the **user's prompt** before it reaches the model, but in agentic setups the sensitive payload is almost always in the tool response, not the prompt. That's the gap Blindfold covers.

## 2. Three ideas, in order

Blindfold's design sits on three ideas that stack.

### 2.1 Tokenize the tool response before it reaches the model

Blindfold's protected input contract is structured JSON. Sensitive fields —
declared per-tool in `blindfold.yaml` — are replaced with opaque **token
strings** (`⟦tok_…⟧`) before the supported result leaves the boundary. In
strict proxy/host paths, a configured result that is not inspectable JSON is
blocked rather than guessed at. The real values live in a local vault.

### 2.2 Controlled operations — let the model use hidden values

The model cannot compare two opaque tokens. The default `controlled` profile
therefore exposes `blindfold_table`, a fixed query language for a whole hidden
list. `blindfold_compute`, which runs arbitrary model-written Python, exists
only in the explicit `python_unsafe` profile and assumes a cooperative model.
Both paths return a new vault token rather than a value.

### 2.3 Rehydrate at the last hop, gated by policy

When the model's final answer contains tokens, the harness calls `rehydrate(text, session_id, store, policy)` — which regex-scans for `⟦tok_…⟧`, checks each one against the session-bound policy, and substitutes real values only for the caller's own tokens. Missing tokens surface as `[unknown token]` (protection against hallucination); policy-denied tokens surface as `[redacted]` (protection against cross-session leakage).

Note the subject of that sentence: **the harness calls it.** Rehydration is a function invoked on the final text by code that owns the final text. This is the one idea of the three that Blindfold cannot perform on your behalf, and §3 explains what that costs in each mode.

## 3. Four integration modes

Blindfold ships as one Python package with four deployment surfaces. Mode B is
the reference boundary; A is beta, while C and D are host-specific experimental
adapters over the same core.

### Mode A: CLI proxy — `blindfold -- <mcp-server>`

For apps that already speak MCP as a client — Claude Desktop, Cursor, Windsurf, Zed, custom agents built on the `mcp` Python client SDK, and other frameworks that have added MCP support. You wrap the existing MCP server command with `blindfold --`:

```jsonc
// Before (e.g. in Claude Desktop's claude_desktop_config.json):
{
  "command": "python",
  "args": ["-m", "your_org.hr_mcp"]
}
// After:
{
  "command": "blindfold",
  "args": ["--config", "blindfold.yaml", "--", "python", "-m", "your_org.hr_mcp"]
}
```

No application code changes are needed for the protection half. In the default
strict profile, the proxy tokenizes supported declared JSON results and rejects
batches, unstructured protected content, and declared path mismatches. It
injects `blindfold_table` for declared tables; `blindfold_compute` is injected
only when `compute.mode: python_unsafe` is selected explicitly.

**The rehydration half does not come for free, and with a third-party client it does not come at all.** The proxy answers a custom JSON-RPC method, `blindfold/rehydrate` — custom meaning *this project invented it*. It is not in the MCP specification, so Claude Desktop, Cursor and Zed have no reason to call it, and they don't. Wrapped under one of those, the assistant's answer reaches the user as:

> The higher earner is ⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧.

There is no fix inside the proxy. It sits on the tool channel, below the client; the assistant's final message never passes through it, and MCP gives a server no hook on what the model says. Exposing rehydration as a normal tool would work mechanically and defeat the design, since a tool result lands in the model's context — precisely where the values must not go.

So Mode A is the right shape when hiding values from the provider is the whole goal and placeholders in the output are acceptable, or when the MCP client is yours and can be taught the extra method. When a human has to read the values, use Mode B.

### Mode B: In-process library — `from blindfold import ...`

This is the reference integration for apps that call LLMs directly. A
`BlindfoldSession` owns tokenization, session identity, policy, TTL and final
rehydration. For
structured lists it can issue a trusted-side `TableQueryCapability` binding one
exact table/session/query/expiry to the user's authorized request; the model
cannot change a threshold or operation without being refused.

```python
from blindfold import BlindfoldSession
from blindfold.config import load_config

session = BlindfoldSession(
    load_config("blindfold.yaml"), session_id=f"user_{user_uuid}"
)
```

Four integration points in your loop:

1. **Brief the model.** Add `session.model_instructions` to the system prompt.
2. **Protect every real tool result.** Prefer `session.call_protected_tool(...)`, which returns only the protected copy, or call `protect_tool_result(...)` immediately after an async/framework-owned invocation.
3. **Authorize and route controlled queries.** Translate the user's authorized request into an exact, short-lived `TableQueryCapability`, then call `session.execute_authorized_query(..., capability=capability)`. A changed table, operation, literal or session is rejected.
4. **Rehydrate before display.** Call `session.render_final_answer(final_text)` only at the user-visible boundary.

The façade refuses unknown tools, absent required paths and declared tables of
the wrong shape before it writes anything to the vault. A path that is truly
optional must say `required: false` in configuration. Low-level primitives
remain available for advanced integrations.

Framework-agnostic, LLM-agnostic. Works with any provider that supports tool use / function calling. See [`examples/demo_chat.py`](../examples/demo_chat.py) for a deliberately unsafe compute example against the Anthropic SDK — swapping in OpenAI or Gemini is a matter of changing the SDK client and the tool_result payload shape.

That example explicitly selects `python_unsafe`; it is not the Mode B default
and should only be advertised when the application accepts the
cooperative-model assumption.

### Mode C: Claude Code plugin — four hooks, no proxy

The host offers its own seams, and they turn out to be a better fit than the
protocol's. [`src/blindfold/hosts/claude_code.py`](../src/blindfold/hosts/claude_code.py)
implements the host contract, wired by
[`plugin/hooks/hooks.json`](../plugin/hooks/hooks.json) to
`blindfold hook <event>`:

- **`PreToolUse`** checks configured tools before execution. MCP tools and the
  audited `Bash`/`PowerShell` adapters continue. A configured built-in whose
  result shape Blindfold does not know how to rebuild is denied before it can
  return sensitive data. `Read` and `WebFetch` currently fall in this group.

- **`PostToolUse`** returns `updatedToolOutput`, which replaces the tool result
  *the model receives*. The adapter preserves the original host shape rather
  than returning one generic string. MCP results may be a one-part text list or
  a `content` object containing that list; the text must be JSON. `Bash` and
  `PowerShell` use the documented structured result and Blindfold rewrites the
  JSON in `stdout` only when `stderr` is empty. A legacy flat `tool_output`
  string remains supported for older observed Claude Code events.
- **`MessageDisplay`** fires on every assistant message as it streams. The text
  to rewrite arrives under `delta` — "the newly completed lines" of the
  message, not the whole thing under `message_text` as the general hook docs
  would suggest for other events; that mismatch cost real debugging time (see
  [`LIMITATIONS.md`](../LIMITATIONS.md)) before the host's own `/hooks`
  inspector settled it. The handler returns `displayContent`, which replaces
  *what the screen shows* while the transcript keeps the original. That is
  rehydration, with a property the proxy cannot offer: the user reads real
  values and the conversation keeps the placeholders, so nothing re-enters the
  model's context on the next turn.
- **`SessionStart`** returns `additionalContext`, injected before the first
  prompt. It carries `describe_config(config)` — every protected path with its
  meaning, plus the instruction to reproduce placeholders verbatim.

Two things Mode A does by editing the `tools/list` response cannot be done here
at all: **no hook can add a tool, and none can edit a tool description.** So the
schema briefing moved to `SessionStart`, and `blindfold_compute` had to become a
real MCP server — [`src/blindfold/mcp_server.py`](../src/blindfold/mcp_server.py),
declared in [`plugin/.mcp.json`](../plugin/.mcp.json). Without it the model can
read placeholders and do nothing with them.

That server has one problem the proxy and the library do not: **it does not know
whose session it is serving.** MCP connections carry no session identity. So it
reads the session off the input tokens, refuses to mix two, and mints the result
into that same session — which is what lets `MessageDisplay`, bound to the
host's session, reveal the result instead of rendering `[redacted]`. The
trade-off is explicit: possession of a token is taken as proof of belonging to
its session.

Two properties this mode forced into the design:

**A shared vault is not optional.** Each hook invocation is a separate process.
A token minted by `PostToolUse` must still resolve when `MessageDisplay` runs
seconds later from somewhere else, so the CLI refuses to run the hooks unless
`storage.backend` is `sqlite`. This is what made the persistent store a
prerequisite rather than a convenience.

**Failure stops rather than passes.** Printing nothing tells the host to keep
what it had — for `PostToolUse` that is the untokenized result on its way to
the model. A configured tool whose result cannot be rebuilt returns
`continue: false`, stopping before another model request. This matters because
Claude Code's post-tool `decision: block` feedback does not remove the original
result. `MessageDisplay` is the opposite: nothing leaks if it does nothing, so
it stays quiet on failure and the user sees a placeholder. The asymmetry is
deliberate.

### Mode D: Codex plugin — result protection without display-only reveal

[`src/blindfold/hosts/codex.py`](../src/blindfold/hosts/codex.py) consumes the
Codex event shape. For a configured tool, `PostToolUse` tokenizes the structured
`tool_response` and returns it with `continue: false` as the stop text. Codex
replaces the original result with that feedback before the model continues. The
adapter can read direct JSON objects/lists, a one-part MCP text result, and JSON in a shell
result's `stdout` when `stderr` is empty.

Codex's local hook path covers shell execution, file patches, MCP calls, and
most local function tools. Hosted tools such as web search do not enter that
path, and specialized tools may opt out. This is therefore a useful guardrail,
not a complete boundary around every tool Codex may ever expose.

Unlike Claude Code, Codex has no display-only message hook. It would be easy to
return the clear value as ordinary feedback, but that feedback would also enter
the model's context. The adapter deliberately does not do that: both model and
user keep the placeholder. A custom client can close this presentation gap only
if it owns the final response; the decision criteria are in
[`host-adapters.md`](host-adapters.md).

### Which mode do you need?

| Your setup | Mode |
|---|---|
| Claude Code | C |
| Codex, with placeholders acceptable in the visible answer | D |
| Claude Desktop / Cursor / Windsurf / Zed + stdio MCP server | A — with placeholders in the user-visible answer (see above) |
| Custom agent that already speaks MCP via `mcp` Python SDK | A if you add the `blindfold/rehydrate` call, otherwise B |
| Enterprise agent using Anthropic / OpenAI / Gemini SDK directly | B |
| LangChain / LlamaIndex / Haystack | B |
| Self-hosted LLM (Ollama, vLLM, LiteLLM) | B |
| Multi-provider gateway (Portkey, LangSmith proxy, etc.) | B, wrapped once at your gateway layer |

**Same core, host-specific seams.** The `Rehydrator`, `Tokenizer`,
`MemoryTokenStore`/`SQLiteTokenStore`, `SessionBoundPolicy`, `SubprocessSandbox`,
and compute handlers are shared. Mode A translates MCP traffic, Mode B calls
the objects directly, and Modes C and D dispatch through separate adapters
under `src/blindfold/hosts/`. Keeping those adapters separate is intentional:
the two hosts use different result shapes and different failure semantics.

## 4. The system, one component per file

Every component lives in exactly one file. Ports are ABCs so alternative implementations are additive.

### Data model — [`src/blindfold/core/lineage.py`](../src/blindfold/core/lineage.py)

Three frozen dataclasses:

- **`VaultRecord`** — the full record for one token: value, dtype, semantic_type, unit, session_id, created_at, ttl, lineage, policy.
- **`Lineage`** — where a record came from: `op` (`tool_result` / `blind_compute` / `literal`), `inputs` (parent token IDs), `code_digest` (sha256 of the compute code), `tool` and `path` for tool-result origins.
- **`Policy`** — three independent booleans: `reveal_to_frontend`, `can_be_input_to_compute` for arbitrary Python, and `can_be_input_to_query` for the constrained table language.

Plus two pure composition helpers:
- `compose_policy(inputs) -> Policy` — AND-composition: any restrictive input wins.
- `compose_ttl(inputs) -> datetime` — shortest surviving TTL wins.

These are called by the blind-compute handler when it mints a derived record, so a computation cannot launder a restrictive policy or extend a short TTL.

### Ports (interfaces)

Small ABCs, one per orthogonal concern. Implementations are additive behind the ports.

- **[`src/blindfold/ports/token_store.py`](../src/blindfold/ports/token_store.py)** — `TokenStore`: `mint_token`, `put`, `get`, `resolve`, `find_by_session`, `invalidate_cascade`, `purge_expired`. `mint_token` lives on the port because the delimiters and hex width are a contract with the rehydrator's regex, not a property of where records are kept.
- **[`src/blindfold/ports/policy.py`](../src/blindfold/ports/policy.py)** — `DetokenizePolicy` with independent `can_reveal`, `can_compute`, and `can_query` checks, plus `DetokenizeContext`.
- **[`src/blindfold/ports/sandbox.py`](../src/blindfold/ports/sandbox.py)** — `ComputeSandbox.run(code, inputs, timeout_s)` and the `SandboxError` exception.

### Vault — [`src/blindfold/core/vault.py`](../src/blindfold/core/vault.py)

`MemoryTokenStore` — the default in-process store. Backed by a locked `dict[str, VaultRecord]`. Lazy TTL expiry on `get`/`resolve`; eager on `purge_expired`. `invalidate_cascade` runs a fixpoint sweep over `lineage.inputs` to remove all descendants of an invalidated token.

**`put` sweeps expired records on an interval** (`purge_interval_s`, 60 seconds by default), so expiry frees memory instead of only hiding records from `get`. It sits in the store rather than in the proxy so Mode B, which constructs its own store, gets it too. Amortized, so a record can outlive its TTL by up to one interval — set `purge_interval_s` lower if that matters.

### Persistent vault — [`src/blindfold/core/sqlite_store.py`](../src/blindfold/core/sqlite_store.py)

`SQLiteTokenStore` — the same port, backed by a file instead of a dict. Selected with `storage.backend: sqlite`; `build_token_store(config)` returns one or the other, and nothing else in the system knows which it got. Standard-library `sqlite3`, WAL journaling so two processes can share one file, an index on the TTL column so expiry sweeps are not full scans.

It exists for two reasons, and the second is the one that shaped it. Longevity is the obvious one: a vault that dies with the process leaves yesterday's placeholders pointing at nothing. **Cross-process sharing** is the one that unlocks new integrations — tokenizing and rehydrating need not happen in the same program, and a host that runs each callback in a fresh process cannot work at all with a memory vault, whatever the TTL.

Both stores are held to one behavioural suite, [`tests/unit/test_token_store_conformance.py`](../tests/unit/test_token_store_conformance.py), which touches the public interface only.

**The file holds cleartext by default.** With `encrypt_at_rest: true`, values are sealed using AES-256-GCM and a key supplied through `BLINDFOLD_VAULT_KEY`; metadata remains readable. Clear/encrypted mode mismatches and wrong keys are refused. See [`LIMITATIONS.md`](../LIMITATIONS.md#storage).

**`invalidate_cascade` is still not called by the runtime** — it is API surface for your code, not automatic behavior.

### Policy — [`src/blindfold/core/policy.py`](../src/blindfold/core/policy.py)

`SessionBoundPolicy` — the strictest MVP default. A record minted in session `S` can only be revealed or operated on from session `S`. Even a guessed token ID is unresolvable across sessions. Reveal, arbitrary compute and constrained query permissions are separate flags.

### Tokenizer — [`src/blindfold/core/tokenizer.py`](../src/blindfold/core/tokenizer.py)

The heart of Blindfold's schema-driven approach:

1. Deep-copies the payload (never mutates the original).
2. For each `SchemaField(path, semantic_type, unit)` from the config, walks the JSONPath against the copy.
3. For each match: mints a token, builds a `VaultRecord` with `_infer_dtype(value)` (`bool`/`int`/`float`/`str` → `boolean`/`number`/`string`; anything else → `object`), calls `store.put`, then replaces the value in the tree with the token string.

[`core/protection.py`](../src/blindfold/core/protection.py) is the single
fail-closed entry above that low-level tokenizer. It validates every required
field and table across the decoded result parts before writing to the vault.
Mode B, the MCP proxy, and the Claude/Codex adapters all call this same entry;
their remaining code only translates their transport-specific result shapes.
4. Returns the mutated copy.

The JSONPath dialect is intentionally minimal at MVP, though not as minimal as this document previously claimed: static keys (`$.a.b`), list wildcards at any depth including nested ones (`$.a[*].b[*].c` descends correctly — `_walk` recurses), and explicit numeric indices (`$.items[0].name`). No filters, no recursive descent (`$..salary`), no slicing — see [`LIMITATIONS.md`](../LIMITATIONS.md).

**Paths that don't match are no-ops.** Declare paths defensively — the cost is zero and it protects against unexpected response shapes.

**Paths that could never match are refused.** `validate_path` runs wherever a `SchemaField` is created: from `blindfold.yaml` through a Pydantic validator on `SensitiveFieldConfig`, and inside the dataclass itself so Mode B is covered without the YAML. It rejects recursive descent, filters, slices, quoted keys and unbalanced brackets — all of which the walker would otherwise reinterpret rather than honor, `$..salary` quietly becoming `$.salary`. The low-level tokenizer keeps non-matching paths as no-ops for compatibility; `BlindfoldSession` instead refuses a missing path unless it is explicitly declared `required: false`.

### Rehydrator — [`src/blindfold/core/rehydrator.py`](../src/blindfold/core/rehydrator.py)

Regex `⟦tok_[0-9a-f]{8}⟧` over the text. For each match:

- `store.get(token)` returns `None` → substitute `[unknown token]` (model hallucinated or record expired).
- Policy denies `can_reveal` → substitute `[redacted]`.
- Otherwise → substitute `str(record.value)`.

Publicly re-exported as `from blindfold import rehydrate` for harness use.

### Sandbox — [`src/blindfold/sandbox/subprocess_.py`](../src/blindfold/sandbox/subprocess_.py)

`SubprocessSandbox` — the compute isolation layer. Given `(code, inputs, timeout_s)`:

1. Spawns `python -I -c <wrapper>` — a fresh, isolated interpreter (no `site-packages`, no `PYTHONPATH`).
2. Clean env: only `PATH` and `PYTHONIOENCODING=utf-8` — nothing inherited.
3. Hard timeout — child is killed via `subprocess.run(..., timeout=timeout_s)`.
4. Feeds `{"code", "inputs"}` as a single JSON line on stdin.
5. The child wrapper `exec`s the user code inside a scope that exposes `resolve(token)` — a closure over the resolved `inputs` dict that raises `KeyError` if the code references an unlisted token.
6. The user code MUST assign to a variable named `result`; the wrapper serializes `result` to JSON on stdout.
7. Parent reads stdout, parses the envelope, returns the value.

Every failure path — timeout, malformed output, syntax error, unassigned `result`, non-serializable `result`, unlisted-token lookup — surfaces as a single `SandboxError`.

**That message is a second way out of the sandbox, so it is deliberately uninformative.** It names the exception's *type* and nothing else: `raise ValueError(resolve("⟦tok_…⟧"))` returns `ValueError`, not the value it used to return. Child stdout and stderr never reach the caller either — they are printed to the operator's own stderr, where the model cannot see them. The envelope is also checked for shape rather than just parsed, since a bare `print(62000)` from user code leaves a last line that is valid JSON.

Step 5's exec scope carries an **allow-list instead of the full builtins**: aggregation functions, value types and a few exception types. `open`, `__import__`, `eval`, `exec`, `getattr`, `type` and `globals` are simply not names in that namespace, so the direct routes to the filesystem and the network are gone — `os.listdir(".")` returns `ImportError`, `open(...)` returns `NameError`. Step 2's environment cleanup was never doing that job: it removes inherited variables and `site-packages`, not file access.

This raises the cost of an escape rather than preventing one. Reaching `object.__subclasses__()` through the object graph needs no builtins at all. Real isolation is an operating-system question, which is what the Docker adapter is for.

[`LIMITATIONS.md`](../LIMITATIONS.md#sandboxing) has the full list, each entry marked with whether it can be closed and at what cost.

### Collective tokens — [`src/blindfold/core/table.py`](../src/blindfold/core/table.py) and [`src/blindfold/tools/blindfold_table.py`](../src/blindfold/tools/blindfold_table.py)

A list declared under `tables:` is replaced by **one** token whatever its length, and the record carries a `TableSchema` — column names, semantic types, units — so the token describes itself rather than depending on a config that may have changed since it was minted. The model is told those columns in the tool description and queries them with `blindfold_table`.

`run_query` applies a fixed set of operations: `filter`, `sort_by`, `limit`, `select`, then optionally one of `sum`, `mean`, `min`, `max`, `count` last. A row result comes back as another table token carrying the columns that survived a `select`, so a query can be built in steps.

**The invariant that makes this worth its cost:** no query may fail because of the data. Comparisons across types do not match rather than raising; sorting mixed types uses a total order; an aggregate over a column with no numbers returns nothing rather than erroring. `ValueError` is reserved for a malformed *query* — an unknown column, an operation out of place. This is what removes the one-bit oracle that arbitrary Python opens, and the reason this path executes no model-written code and runs no sandbox.

Table tokens and their descendants cannot be inputs to `blindfold_compute`.
This separation matters: otherwise a model could mint a new count token for
each threshold and make Python fail according to whether the count is zero,
bypassing a rate limit attached to the original token.

### `blindfold_compute` tool (unsafe opt-in) — [`src/blindfold/tools/blindfold_compute.py`](../src/blindfold/tools/blindfold_compute.py)

The MCP tool the LLM actually calls. Two exports:

- `build_tool_definition()` — returns the tool spec (name, description, JSON schema for `code` + `inputs`). The description teaches the model the protocol: "every token you resolve must be listed in `inputs`; assign to `result`; result must be JSON-serializable; you get back a new token, not a value".
- `handle_blindfold_compute(args, *, store, policy, sandbox, session_id, ttl_seconds, max_calls_per_token, rate_window_s)` — the cooperative-model orchestrator:
  1. Validate `args` shape.
  2. For each input token: fetch the record, run `policy.can_compute` — reject on failure.
  3. Walk each input's lineage to its original secrets and atomically reserve one attempt in the vault. Each root shares its budget with every derived token; failed and timed-out executions still consume it.
  4. Build `{token: value}` dict.
  5. Call the sandbox.
  6. Mint a new record with `lineage.op="blind_compute"`, `lineage.inputs=tuple(inputs)`, `lineage.code_digest=sha256(code)`, `policy=compose_policy([...])`, `ttl=compose_ttl([...])`.
  7. Mark the attempt succeeded (or failed/timed out on the exception paths) and return the new token.

The composed policy and TTL are why the model cannot launder sensitive data through a computation: derived tokens are at least as restricted as their most restrictive input.

**Why the rate check is a window, not a lifetime count:** the same secret legitimately shows up many times across a real session. A flat cap would eventually break ordinary use. A window catches a burst while permitting later reuse. Reservations live in a metadata-only attempt ledger, happen before the sandbox, and are atomic in both stores (SQLite uses a write transaction across processes). `blindfold audit` summarizes outcomes and flags blocked bursts. This does not close the channel — a patient attacker can continue across windows — so the prompt and tool description also state the cooperative-model rule explicitly.

### Config — [`src/blindfold/config.py`](../src/blindfold/config.py)

Pydantic v2 models the shipped sections, including `compute.mode`
(`disabled|controlled|python_unsafe`) and `proxy.strict`. Unknown top-level keys
remain tolerated for forward compatibility, while unknown values inside a
modeled section are refused.

### Proxy — [`src/blindfold/proxy.py`](../src/blindfold/proxy.py)

The wire orchestrator. `run_proxy(downstream_cmd, config_path)`:

1. Loads the config.
2. Builds a `ProxyState`: fresh `MemoryTokenStore`, `SessionBoundPolicy`, `SubprocessSandbox`, and a unique `session_id` (UUID4).
3. Spawns the downstream MCP server as a subprocess with piped stdin/stdout.
4. Runs two concurrent asyncio tasks:
   - **Client → child**: reads a line from our stdin, parses JSON-RPC, then dispatches:
     - `blindfold/rehydrate` → answer locally using the rehydrator.
     - configured operation tool → answer locally only when its compute profile permits it.
     - Anything else → record the tool name (indexed by JSON-RPC `id`) and forward verbatim to the child.
   - **Child → client**: reads a line from the child's stdout, and:
     - If it's a `tools/list` response → append only the tools permitted by `compute.mode`.
     - If it's a recorded protected response → tokenize supported JSON or replace the entire response with a safe error in strict mode.
     - If it's a `resources/read` response with a recorded ID → same, matching the returned part's URI against the `resources:` globs. Resources carry data exactly as tool results do, and used to pass through untouched.
     - Forward the (possibly-mutated) message to our stdout.
5. Shuts down cleanly when either side closes.

**Windows note:** `asyncio.connect_read_pipe(sys.stdin)` doesn't work on Windows' ProactorEventLoop. The proxy uses `asyncio.to_thread(stdin.buffer.readline)` and synchronous `stdout.buffer.write + flush` instead. Portable, small enough to reason about, no platform detection needed.

### Audit — [`src/blindfold/audit.py`](../src/blindfold/audit.py)

Answers the question a screenshot cannot: did any hidden value actually reach the model, in a *real* conversation. `audit(transcript, store, session_id)`:

1. Finds every `⟦tok_…⟧` placeholder in the transcript text (`TOKEN_PATTERN.findall`), and every vault record for the session (`store.find_by_session`).
2. For each record, searches the transcript for its value as text — every scalar inside it if the value is a table row or nested structure (`_searchable`). A hit is a leak, reported with the token and its `semantic_type`.
3. Values under `MIN_INTERESTING` (5) characters are skipped and counted separately — `"Eng"` or `"1"` occurs in any transcript for reasons unrelated to a leak.
4. **`blind_compute`-lineage records get one more check before being called a leak**: `_literals_the_model_already_wrote` scans the transcript for string literals inside any `blindfold_compute` call's own `code` argument (its JSON-escaped text, unescaped and searched for quoted substrings). A match there means the model typed that text itself — choosing between two already-known names based on a hidden comparison, say — not that it came out of the vault. Reported separately as `Report.explained`, not silently dropped, so a wrong exclusion is still visible. A value actually produced by `resolve(...)` arithmetic, never typed as a literal anywhere, is unaffected.

`read_transcript(path)` re-serializes each JSONL line (`json.dumps(json.loads(line), ensure_ascii=False)`) so a placeholder written escaped by one process and literally by another is found either way; a non-`.jsonl` file is read as plain text, so a log captured by hand works too. `session_ids_in(path)` reads the `sessionId` field Claude Code's own transcripts carry, so the caller does not have to know one. Exposed as `blindfold audit <transcript> [--session ID]`.

### CLI — [`src/blindfold/cli.py`](../src/blindfold/cli.py)

A thin argparse layer. `blindfold [--config PATH] -- <cmd> [args...]`: parses the pre-`--` options, treats everything after `--` as the downstream command, calls `run_proxy`. `blindfold hook <event> --host claude-code|codex` reads a JSON hook event from stdin and dispatches through [`hooks.py`](../src/blindfold/hooks.py) to the chosen adapter (Modes C and D). `blindfold mcp-server` starts the shared `blindfold_compute`/`blindfold_table` MCP server. `blindfold audit <transcript>` runs the check above. Also exposed as `python -m blindfold` via [`__main__.py`](../src/blindfold/__main__.py). `main()` reconfigures `stdin`/`stdout`/`stderr` to UTF-8 before anything else runs — Windows' default console codepage cannot represent the token delimiters, and a binary a host invokes by bare command name never gets `PYTHONIOENCODING` set for it.

## 5. End-to-end example: "Who earns more, Manuel or Andrea?"

Setup: `fake_hr_mcp` (in [`examples/fake_hr_mcp/`](../examples/fake_hr_mcp/)) hard-codes `Manuel Pernigotto → 62000`, `Andrea Tuscano → 71000`. Config declares `$.salary` as sensitive on `get_salary`. This trace deliberately enables `compute.mode: python_unsafe`; it illustrates the legacy free-form compute path, not the default controlled profile.

The harness in this trace is one you wrote — [`examples/demo_chat.py`](../examples/demo_chat.py) is the running version of it. Frames 1–6 play out identically under a third-party MCP client; frame 7 is the one that requires your own code, and the reason the trace is written this way.

### Frame 1: user asks, harness lists tools
```
user       → harness: "Who earns more, Manuel Pernigotto or Andrea Tuscano?"
harness    → Anthropic: messages + tools=[get_salary, blindfold_compute]
```
`blindfold_compute` is in the tool list because the proxy injected it into the `tools/list` response.

### Frame 2: model asks for Manuel's salary
```
Claude     → harness: tool_use(get_salary, {"name": "Manuel Pernigotto"})
harness    → proxy:   tools/call get_salary("Manuel Pernigotto")
proxy      → child:   (forwarded)
child      → proxy:   {"content":[{"type":"text","text":'{"name":"Manuel Pernigotto","salary":62000}'}]}
```

The proxy sees the `tools/call` response, finds `get_salary` in the pending-calls map, and calls the tokenizer:
```
tokenizer:
  match $.salary → 62000
  mint token: ⟦tok_7f3a1b2c6e9645d4b17f425a58992275⟧
  vault.put({token, value: 62000, dtype: number, semantic_type: salary, unit: EUR/year, session: sess_abc, ...})
  return {"name": "Manuel Pernigotto", "salary": "⟦tok_7f3a1b2c6e9645d4b17f425a58992275⟧"}
```

```
proxy      → harness: response with tokenized salary
harness    → Anthropic: tool_result = '{"name":"Manuel Pernigotto","salary":"⟦tok_7f3a1b2c6e9645d4b17f425a58992275⟧"}'
```

**Anthropic has never seen 62000.**

### Frame 3: model asks for Andrea's salary
Same round-trip. Now the vault has two records; Anthropic has seen two token strings.

### Frame 4: model needs to compare, calls blindfold_compute
```
Claude     → harness: tool_use(blindfold_compute, {
  "code": "result = 'Manuel Pernigotto' if resolve('⟦tok_7f3a1b2c6e9645d4b17f425a58992275⟧') > resolve('⟦tok_2d81e9f43a1244709732a7aa30cc2e39⟧') else 'Andrea Tuscano'",
  "inputs": ["⟦tok_7f3a1b2c6e9645d4b17f425a58992275⟧", "⟦tok_2d81e9f43a1244709732a7aa30cc2e39⟧"]
})
```

### Frame 5: handler executes
The proxy sees `params.name == "blindfold_compute"` in the incoming `tools/call` and routes to `handle_blindfold_compute` (never forwards to the child). It:
1. Verifies both tokens exist, matching session, `can_compute` passes.
2. Builds the private input map for the two 128-bit tokens (`62000` and `71000`).
3. Calls sandbox → subprocess `python -I` → `exec` the code → `62000 > 71000` is `False` → `result = 'Andrea Tuscano'` → stdout `{"ok": true, "value": "Andrea Tuscano"}`.
4. Mints `⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧` with `lineage.op="blind_compute"`, both input tokens in `lineage.inputs`, and `lineage.code_digest=sha256(code)`.
5. Returns the new token.

```
proxy      → harness: {"content":[{"type":"text","text":"⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧"}]}
harness    → Anthropic: tool_result = "⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧"
```

**Anthropic saw: a tool call with source code + two token literals, and a token literal in the response. No numbers, no names.**

### Frame 6: model writes its answer
```
Claude     → harness: "The higher earner is ⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧."
```

### Frame 7: rehydration
The harness calls `rehydrate("The higher earner is ⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧.", session_id=sess_abc, store, policy)`:
- Regex finds `⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧`.
- `store.get` returns the record.
- `policy.can_reveal(ctx=sess_abc, record)` → `True`.
- Substitute → `"The higher earner is Andrea Tuscano."`

Printed to the user: **"The higher earner is Andrea Tuscano."**

Under Claude Desktop or Cursor, with nothing making that call, the last line reads **"The higher earner is ⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧."** instead. Same protection, no delivery.

## 6. Package layout at a glance

```
src/blindfold/
├── __init__.py                # re-exports PLACEHOLDER_PROMPT, describe_config, describe_schema, rehydrate
├── __main__.py                # enables `python -m blindfold`
├── cli.py                     # argparse; proxy / hook / mcp-server / audit subcommands
├── proxy.py                   # asyncio stdio MCP proxy (Mode A)
├── hooks.py                   # common lifecycle dispatcher for host adapters
├── hosts/
│   ├── common.py              # shared fail-closed tokenization helpers
│   ├── claude_code.py         # Claude Code event/result shapes (Mode C)
│   └── codex.py               # Codex event/result shapes (Mode D)
├── mcp_server.py              # blindfold_compute / blindfold_table for Modes C and D
├── audit.py                   # cross-reference a transcript against the vault
├── config.py                  # pydantic BlindfoldConfig
├── core/
│   ├── lineage.py             # data model + composition helpers
│   ├── vault.py               # MemoryTokenStore
│   ├── sqlite_store.py        # SQLiteTokenStore
│   ├── tokenizer.py           # SchemaField + tokenize_result + JSONPath
│   ├── table.py               # collective tokens: the query operations
│   ├── rehydrator.py          # rehydrate + TOKEN_PATTERN + PLACEHOLDER_PROMPT
│   └── policy.py              # SessionBoundPolicy
├── ports/
│   ├── token_store.py         # TokenStore ABC
│   ├── policy.py              # DetokenizePolicy ABC + DetokenizeContext
│   └── sandbox.py             # ComputeSandbox ABC + SandboxError
├── sandbox/
│   └── subprocess_.py         # SubprocessSandbox
└── tools/
    ├── blindfold_compute.py   # tool spec + handler + compute rate limit
    └── blindfold_table.py     # query a collective token, no sandbox
```

Tests mirror the layout under `tests/unit/`, plus `tests/integration/test_proxy_forwarding.py` (spawns the real proxy) and `tests/e2e/test_demo_flow.py` (replays a canned transcript and asserts no leakage).
