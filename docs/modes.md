# Choosing a mode

Blindfold has four ways to plug in. They share the same token vault and differ
in two questions that decide which one you want: **which tool results the host
lets Blindfold intercept**, and **who owns the model's final answer**, therefore
who can put the real values back.

Read this before installing anything. Picking the wrong mode gets you a working
system that never shows anyone a result.

| Mode | Maturity | Security role |
|---|---|---|
| B — library | reference | application-owned ingress, authorization and egress |
| A — MCP proxy | beta | strict declared-JSON boundary; no automatic rehydration |
| C — Claude Code | experimental | version-sensitive host adapter |
| D — Codex | experimental guardrail | supported local hooks; placeholders remain visible |

---

## Try the three host-independent flows first

```bash
uv run python examples/try_modes.py        # proxy, library, Claude hook flow
uv run python examples/try_modes.py a      # one of them
```

No API key, no Ollama, no Docker. The model is scripted, because the point is to
watch what happens to the data. Mode C is driven the way the host drives it —
each hook a separate process over a shared vault — so you can see the whole
sequence before installing anything. Mode D requires Codex itself because its
important behavior is replacement by the host, not tokenization in isolation.

## What Blindfold does, in one paragraph

When your agent calls a tool, the tool's answer normally goes straight into the
model's context and therefore to your LLM provider. Blindfold intercepts that
answer and replaces the fields you declared with placeholders — `⟦tok_a58cbaf08d2f45058ba8493ca72e94cb⟧`
— keeping the real values in a local vault. The model reasons over placeholders.
For a declared table it calls the controlled `blindfold_table` query tool and
gets another placeholder. Arbitrary Python exists only in the explicit
`python_unsafe` profile. At the end,
your code (or the host) swaps the placeholders for real values before a human
reads them.

Nothing is guessed. You declare, per tool, which JSON paths are sensitive. A
field you did not declare goes to the model in cleartext.

---

## Which mode

| Your situation | Mode |
|---|---|
| You work inside **Claude Code** | **C** |
| You work inside **Codex** and placeholders in the visible answer are acceptable | **D** |
| You write the agent loop yourself, in Python, with any LLM SDK | **B** |
| You use an MCP client someone else wrote (Claude Desktop, Cursor, Zed) **and can live without seeing the values** | **A** |
| You use an MCP client someone else wrote and a human must read the values | **none of them** — see [the catch](#the-catch-who-puts-the-values-back) |

---

## The catch: who puts the values back

Putting placeholders *into* the model's context is easy from anywhere. Taking
them *out* of its answer needs someone who holds that answer.

- **Mode B**: your `BlindfoldSession` holds it and renders the final answer. Works.
- **Mode C**: Claude Code holds it, and offers a hook that rewrites what the
  screen shows. Works — and better than Mode B, see below.
- **Mode D**: Codex can replace a supported local tool result before the model
  continues, but has no display-only hook. The protection works; the final
  placeholders stay visible.
- **Mode A**: the proxy sits *below* the client, on the tool channel. The
  model's final message never passes through it, and MCP gives a server no hook
  on what the model says. **It cannot rehydrate.**

So under Claude Desktop with Mode A, your assistant answers:

> The higher earner is ⟦tok_9c1bf051f23546eeb0e5d29276da9217⟧.

and stops there. The protection is real — your provider never saw a salary — but
nobody can read the answer. This is structural, not a missing feature.

---

## Mode A — CLI proxy

**What it is.** A process that sits between your MCP client and your MCP server
and edits the traffic. No application code changes.

**Setup.** Change the command your client already runs:

```jsonc
// before
{ "command": "python", "args": ["-m", "your_org.hr_mcp"] }
// after
{ "command": "blindfold",
  "args": ["--config", "blindfold.yaml", "--", "python", "-m", "your_org.hr_mcp"] }
```

**What you get**

- Tool results (`tools/call`) tokenized against your `schemas:` section.
- MCP resources (`resources/read`) tokenized against your `resources:` section,
  matched by URI glob.
- Each protected tool's description gains a note saying which paths come back as
  placeholders and what they mean, so the model knows what it is holding.
- `blindfold_table` is added when a table is declared and the controlled profile
  is active. `blindfold_compute` is added only for `compute.mode: python_unsafe`.

**What you do not get**

- **Rehydration**, unless the client is yours and you teach it to call the
  custom `blindfold/rehydrate` JSON-RPC method. No third-party client does.
- Protection for `prompts/*`.
- Element-by-element JSON-RPC batch support. Strict mode blocks batches rather
  than forwarding an uninspected response.
- Free-form text, images and blobs. For a configured protected tool, strict mode
  blocks these shapes rather than passing them through.
- Anything outside that one MCP server. The proxy wraps one command.

**Pick it when** hiding values from the LLM provider is the whole goal and
placeholders in the output are fine — audit trails, pipelines whose output is
read by code, or a client you plan to extend.

---

## Mode B — in-process library

**What it is.** You create a `BlindfoldSession` around the agent loop you
already have. No MCP required; works with Anthropic, OpenAI, Gemini, Ollama,
LangChain, or a loop you wrote yourself.

**Setup.**

```python
from blindfold import BlindfoldSession
from blindfold.config import load_config

config = load_config("blindfold.yaml")
session = BlindfoldSession(config, session_id=f"user_{user_uuid}")
```

The safe path:

1. **Put `session.model_instructions` in your system prompt.** It includes the
   placeholder rules and every configured protected path.
2. **Protect every tool result.** Prefer
   `session.call_protected_tool(tool_name, function, *args)` for synchronous
   tools, so the caller receives only the protected copy. When a framework has
   already invoked the tool, use `session.protect_tool_result(tool_name,
   result)` immediately.
3. **Advertise only the selected operation tools.** In the default controlled
   profile add `blindfold_table` for declared tables. Add
   `blindfold_compute.build_tool_definition()` only after an explicit
   cooperative-model decision.
4. **Render only the final answer** with
   `session.render_final_answer(model_answer)` immediately before display.

For user-intent authorization, the trusted application can issue a
`TableQueryCapability` and call `session.execute_authorized_query(...,
capability=capability)`. This method always requires the capability. It matches the
session, table, full operation list and expiry exactly; a model-proposed change
is refused without asking another model to judge semantic similarity.

Configured paths are required by default. If a field is legitimately absent
from some successful responses, declare `required: false`. Unknown tools,
missing required paths and malformed declared tables stop with
`ProtectionError`; the façade never returns the raw result as a fallback.

See [`examples/demo_chat.py`](../examples/demo_chat.py) for the whole thing
against the Anthropic SDK.

**What you get**

- Everything, including rehydration. This is the only mode where you own every
  seam.
- Freedom over session identity: use your real user id, and `SessionBoundPolicy`
  will refuse another user's placeholders.

**What you do not get**

- Control over an LLM SDK you call outside the façade: the application must
  still send the protected result, not a raw result it obtained elsewhere.
- Automatic async tool invocation. Protect an awaited result immediately with
  `protect_tool_result()`.

The primitive tokenizer, rehydrator and handlers remain available as an
advanced API for integrations that need custom orchestration.

**Pick it by default** when you write the loop or need the strongest available
boundary. This is the reference mode because your application owns every seam.

---

## Mode C — Claude Code plugin

**What it is.** Four hooks plus a small MCP server, instead of a proxy. Claude
Code calls Blindfold at the right moments; nothing sits in the middle.

**Setup.**

```bash
uv tool install .            # puts `blindfold` on PATH
claude --plugin-dir ./plugin
```

with a `blindfold.yaml` in the directory you start from:

```yaml
storage:
  backend: sqlite            # required, see below
  path: ./vault.db

schemas:
  mcp__hr__get_salary:       # Claude Code names MCP tools mcp__<server>__<tool>
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

**The five pieces**

| Piece | Does |
|---|---|
| `SessionStart` hook | Before your first prompt, tells the model which paths come back as placeholders, what they mean, how to compute on them, and to copy them verbatim |
| `PreToolUse` hook | Denies a configured built-in before execution when Blindfold has no tested adapter for that result shape |
| `PostToolUse` hook | Replaces declared fields while preserving Claude Code's result shape; audited for one-part JSON MCP results and JSON in `Bash`/`PowerShell` standard output |
| `blindfold` MCP server | Offers only the operations selected by `compute.mode`; Python is absent by default |
| `MessageDisplay` hook | Puts the real values back **on screen only** |

**What you get**

- Rehydration, with a property no other mode has: because `MessageDisplay`
  changes only what is displayed, **the transcript keeps the placeholders**. You
  read real values; the model, on the next turn, still sees placeholders. The
  values never re-enter its context.
- Coverage across MCP tools and the structured shell adapters, without wrapping
  each MCP server separately.

**What you do not get**

- **MCP resources are not protected.** The hooks are tool-scoped. If your server
  exposes sensitive data as a resource rather than a tool, use Mode A for it.
- **A memory vault will not work.** Every hook run is a separate process and the
  MCP server is a third one, so the vault must be a shared file. Blindfold
  refuses to run the hooks with `backend: memory` rather than minting
  placeholders nobody can resolve.
- **`blindfold` must be on `PATH`** for every hook process and for the server.
- **Configured `Read`, `WebFetch`, and other built-ins without an audited result
  adapter are denied before execution.** Treating all tools as if they returned
  the same shape is unsafe: Claude Code can reject the replacement and retain
  the original.

**One behaviour to know before you rely on it:** if a tool with declared fields
returns something that is not JSON, the call is **blocked**, not passed through.
Blindfold cannot tell which part of a free-text answer is sensitive, and letting
it through would send the model exactly the values you asked to hide.

A valid JSON result whose declared paths no longer match also stops the turn.
That usually means the upstream tool changed its response shape; continuing
would make the configuration look active while protecting nothing.

**Pick it when** easy Claude Code adoption matters and you accept an
experimental, version-sensitive host boundary. Verify the installed host
version before relying on it with real data.

---

## Mode D — Codex plugin

**What it is.** A host adapter packaged under
[`plugins/blindfold-codex/`](../plugins/blindfold-codex/). Codex invokes
`PostToolUse` after a supported local tool completes. Blindfold tokenizes the
configured fields and asks Codex to replace the original tool result with that
protected JSON before the model continues.

**What you get**

- Protection for hooked local tool paths: shell commands, unified execution,
  file patches, MCP calls, and most other local function tools.
- The same profile-gated operation server as Mode C.
- Fail-closed handling for a configured hooked result that is not structured
  JSON or whose declared paths no longer match.

**What you do not get**

- **No display-only reveal.** Codex has no equivalent of Claude Code's
  `MessageDisplay`, so both the model and the user see placeholders. Returning
  the real value in a normal hook message would put it back in model-visible
  context and defeat the design.
- **No coverage for hosted tools such as web search.** Those calls do not enter
  Codex's local hook path. Some specialized tool paths may opt out as well.
- **No undo of side effects.** The post-tool hook changes what continues toward
  the model; it runs after the tool has already acted.

Blindfold uses Codex's `continue: false` post-tool response rather than a
blocking decision. Both replace the model-visible result, but this form also
lets a nested JavaScript code-mode tool promise resolve with the protected
feedback instead of turning the privacy rewrite into an exception.

**Pick it when** hiding configured local tool outputs matters more than seeing
their real values in Codex's final answer.

---

## Side by side

| | A — proxy | B — library | C — Claude | D — Codex |
|---|:---:|:---:|:---:|:---:|
| Tool results tokenized | one MCP server | what you wire | audited MCP + shell shapes | hooked local tools |
| MCP resources tokenized | yes | yes, if you call it | **no** | **no** |
| Hosted tools covered | no | if your loop owns them | host-dependent | **no** |
| Model told what placeholders mean | automatic | you wire it | automatic | automatic |
| Blind compute on single values | automatic | you wire it | automatic | automatic |
| Table queries on long lists | automatic | you wire it | automatic | automatic |
| **Values reach the user** | **no** | yes | yes | **no** |
| Values kept out of the next turn | n/a | no | **yes** | yes, as placeholders |
| Persistent vault required | no | no | **yes** | **yes** |
| Application code changes | none | one session façade | none | none |

---

## How do you know it is working?

A fair question with an uncomfortable answer: in Modes B and C, **from the
screen alone you cannot tell.** They put the real values back before you read
them, so a working install and no install can look identical. Mode D is visibly
different because it leaves placeholders, but that alone does not prove the
original value was absent from every persisted host record.

The truth is in the transcript — what the model actually received — and reading
it by eye is not enough, because knowing whether something leaked means knowing
what was supposed to be hidden. That lives in the vault. So:

```bash
blindfold audit ~/.claude/projects/<your-project>/<session>.jsonl
```

It cross-references the two and answers in one line:

```
vault records            : 1
placeholders in transcript: 1

No hidden value appears in the transcript.
```

and when something did get through:

```
LEAKED — 1 hidden value(s) found in the transcript:
  ⟦tok_8909bc5650239a83⟧ (salary) -> 71000
```

Exit code 1 on a leak, so it can gate a pipeline. Note what it does **not**
tell you: a field you never declared was never tokenized, so there is no record
of it and nothing to look for. The audit measures whether Blindfold kept the
promises your config made — not whether your config named everything it should
have.

For Mode A and Mode B there is a simpler check, because you can see the
placeholders directly: Mode A leaves them in the answer, and
`examples/try_modes.py` prints what the tool returned next to what the model
was given.

---

## What no mode does

These are properties of the approach, not gaps to be filled. The full list, with
reasoning, is in [`LIMITATIONS.md`](../LIMITATIONS.md).

- **Your prompt is not protected.** If you ask *"what is Andrea's salary?"*, the
  name and the intent go to the provider. Only the answer is hidden.
- **The model cannot judge a hidden value.** *"Who earns more"* works. *"Is this
  a competitive salary for Milan?"* cannot — that needs seeing the number.
- **Undeclared fields pass through in cleartext.** Protection is exactly as good
  as your config. Declare defensively, including error paths.
- **`blindfold_compute` runs arbitrary Python**, so a model actively trying to
  extract a value can learn one bit per call by writing code that fails on
  purpose. `blindfold_table` cannot be used that way — where your data is a
  list, prefer a table. For `blindfold_compute`, `compute.max_calls_per_token`/
  `rate_window_s` (default: 8 attempts per 60s, per original secret lineage)
  bound how *fast* that channel can be probed. The quota is reserved before
  execution, counts failures and timeouts, cannot be reset with a derived
  token, and is atomic across SQLite-backed host processes. Blocks are logged
  and reported by `blindfold audit`, but the limit still only slows the channel.
- **Access control is your API's job.** Blindfold forwards requests untouched.
  If your API answers anyone, Blindfold faithfully hides data the caller should
  never have received.

---

## Storage, in one line

`backend: memory` is the default and loses everything when the process ends.
Use `backend: sqlite` when placeholders must outlive a restart, or when two
processes need the same vault — which Modes C and D always do. The file holds
cleartext unless you set `encrypt_at_rest: true` and supply
`BLINDFOLD_VAULT_KEY`; there is deliberately no way to put the key in the config
file it protects.
