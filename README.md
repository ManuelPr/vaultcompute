# Blindfold

> A privacy layer for structured LLM tool results. On supported, declared paths Blindfold replaces private values with opaque tokens before the model receives them, and restores authorized values only at a caller-controlled last hop.

*(Working name — subject to change.)*

**Status:** pre-alpha. The MVP is built and covered by tests; everything beyond it is design, not code.

| Surface | Status | Intended guarantee |
|---|---|---|
| Core + Mode B library | reference | the application owns both ingress and final egress |
| Mode A MCP proxy | beta | strict protection of declared JSON results; no automatic final rehydration |
| Mode C Claude Code | experimental | fail-closed adapter for explicitly supported host result shapes |
| Mode D Codex | experimental guardrail | supported local hook paths only; placeholders remain visible |

This README describes both, so it marks which is which:

- Unmarked prose describes **what ships today**.
- **[planned]** marks something that is designed but not implemented. It is not in the package; do not rely on it.

[`LIMITATIONS.md`](LIMITATIONS.md) is the authoritative inventory of the distance between the two. It separates *temporary implementation gaps* (someone can close them) from *structural limits* (no amount of work removes them). Read it before pointing this at real data.

Feedback and contributions welcome — see [Contributing](#contributing).

---

## The problem

When you connect an LLM (Claude, GPT, Gemini…) to your internal APIs via tool calling or MCP, every tool result flows back into the model's context. Ask an agent *"What is Andrea's salary?"* and the HR API's response — the actual salary — is sent to the LLM provider as part of the conversation.

Existing PII-redaction proxies (Presidio, Philter, LLM Guard, …) solve a different problem: they scrub the **user's prompt** before it reaches the model. But in agentic setups the sensitive data usually isn't in the prompt — it's in the **tool results** coming back from your private APIs. That gap is what Blindfold covers.

## What it does

Blindfold sits between your agent harness and your APIs (or MCP servers). It:

1. **Tokenizes tool results.** Sensitive fields — declared per-tool in a schema — are replaced with typed anonymous tokens before the result reaches the LLM. The real values stay in a local vault — in memory by default, or in a SQLite file when the vault has to survive a restart or be shared between processes. That file holds cleartext unless you set `encrypt_at_rest` and supply a key from outside it.
2. **Tells the LLM what the tokens mean, not what they are.** Each protected tool's description gains one line per declared path — `$.salary — salary, EUR/year` — so the model knows what it is manipulating even when the upstream API names its fields `f_42`. Sent once, with the tool definitions; never per token, never the value.
3. **Enables controlled operations.** The default `controlled` profile exposes `blindfold_table`, a fixed operation set over a hidden list. Arbitrary model-written Python is available only through the explicit `python_unsafe` cooperative-model profile. Its system instruction forbids probing, while an atomic lineage-wide rate limit counts successful, failed and timed-out attempts; those controls slow abuse but do not make arbitrary Python safe against a malicious or prompt-injected model.
4. **Rehydrates at the last hop.** Tokens in the model's answer are replaced with real values only when the response is delivered to the end user — after a pluggable authorization check. **This step is yours to call.** It happens in your application code, on the answer the model produced; a third-party MCP client will not do it for you (see [Quick start](#quick-start) and [`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control)).
5. **Provides a diagnostic audit.** `blindfold audit <transcript>` cross-references exact vault values against a conversation and reports secret-compute outcomes or rate-limit blocks. It can confirm expected placeholders and catch direct cleartext matches; it is not proof of non-disclosure because undeclared, transformed, inferred and very short values can evade it.

```
┌────────────┐   answer with real values    ┌─────────────────────┐
│  Frontend  │◄─────────────────────────────│  Blindfold           │
└────────────┘                              │  ┌───────────────┐  │
      │                                     │  │ token vault   │  │
      ▼ prompt                              │  │ (memory or    │  │
┌────────────┐   tool call                  │  │  ephemeral,   │  │
│  Harness   │────────────────────────────► │  │  lineage DAG) │  │
│  + LLM     │◄─────────────────────────────│  └───────────────┘  │
│  provider  │   result with ⟦tokens⟧       └──────────┬──────────┘
└────────────┘                                         │ real values
   sees tokens only                                    ▼
                                              ┌─────────────────┐
                                              │  Your private   │
                                              │  APIs / MCP     │
                                              └─────────────────┘
```

## Example

**User:** *"Who earns more, Manuel Pernigotto or Andrea Tuscano?"*

1. The LLM calls `hr_api.get_salary` twice. Blindfold intercepts both results and returns `{"name": "Manuel Pernigotto", "salary": "⟦tok_7f3a⟧"}` and the same shape for Andrea. The model already knows from the tool's description that `$.salary` is a salary in EUR/year and that it cannot read it.
2. The LLM cannot compare what it cannot see — so it submits a blind-compute request:
   ```python
   result = "Manuel Pernigotto" if resolve("⟦tok_2d81⟧") > resolve("⟦tok_7f3a⟧") else "Andrea Tuscano"
   ```
3. Blindfold executes it in a sandbox on the real values and returns a **new token** — `⟦tok_9c1b⟧`, and nothing else. The vault keeps what the model doesn't get: the value, its dtype, and the lineage back to `tok_7f3a` and `tok_2d81` through this exact code.
4. The LLM answers: *"The higher earner is ⟦tok_9c1b⟧."*
5. Your application calls `rehydrate()` before showing the answer: *"The higher earner is Manuel Pernigotto."* (Step 5 is the one an MCP client you did not write will skip — it would show the placeholder.)

The LLM provider saw two opaque tokens, a snippet of comparison code, and a third opaque token. It never learned a salary — and not even who earns more.

## How it works

### Typed tokens with lineage

Every vault entry is a full record, not a bare key-value pair:

```json
{
  "token": "tok_9c1b",
  "value": "<held in the vault, never serialized toward the LLM>",
  "dtype": "string",
  "semantic_type": null,
  "lineage": { "op": "blind_compute", "inputs": ["tok_7f3a", "tok_2d81"], "code_digest": "sha256:…" },
  "session_id": "sess_01",
  "ttl": "2026-07-15T11:32:00Z",
  "policy": { "reveal_to_frontend": true, "can_be_input_to_compute": true }
}
```

The lineage DAG buys three things most redaction tools don't have:

- **Audit** — every derived value can show which inputs and code produced it. `blindfold audit <transcript>` is a diagnostic for placeholders and exact cleartext matches; it is not a proof of non-disclosure.
- **Cascading invalidation** — expire or delete a token and all its descendants go with it. Implemented as `invalidate_cascade`, but nothing in the runtime calls it yet: today it is an API for your code, not an automatic behavior.
- **Policy inheritance** — a derived token inherits the *most restrictive* policy of its inputs, so sensitive data can't be laundered through a computation. This one is wired: `compose_policy` and `compose_ttl` run on every blind compute.

### Collective tokens for structured data

A list declared under `tables:` comes back as **one** token, whatever its
length, and the model is told the column names and what they mean. On 500
employees x 5 sensitive fields:

```
individual tokens : 2500
collective token  : 1
the model sees    : {"employees": "⟦tok_a58cbaf08d2f45058ba8493ca72e94cb⟧"}
```

The cost of not having this was never context size — current tokens are 38 characters
and *replace* the values they hide, measured at +3% on that same response back
when they were 14. It
was that the model could not operate on the result at all: a few hundred
unordered opaque strings carry no structure, so it cannot sort them or even
tell they are comparable quantities.

It queries the token with `blindfold_table`, using a fixed set of operations
rather than code — `filter`, `sort_by`, `limit`, `select`, `sum`, `mean`,
`min`, `max`, `count` — and gets another token back:

```
model  -> filter dept == Eng, sort by salary desc, limit 3, select name, salary
result -> ⟦tok_dc708f103c704e9ba8af451b542c1762⟧
user   -> [{"name": "p499", "salary": 48463}, …]
```

That restriction is the feature, not a compromise. Arbitrary Python lets a
model write something whose *success* depends on a hidden value and read one
bit per call; a fixed operation set cannot express it. So this path executes no
model-written code and **needs no sandbox at all**.

Table tokens and every value derived from them are policy-marked as ineligible
for `blindfold_compute`. This prevents a model from creating a fresh count token
for each threshold and feeding those tokens to Python as a success/failure
oracle. A Mode B application can additionally require a trusted-side
`TableQueryCapability` that binds one exact table, session, operation list and
expiry to the user's authorized request.

### Schema-driven tokenization

Blindfold does **not** guess what's sensitive with NER or regexes over tool results. You declare it, per tool, per field:

```yaml
schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

Deterministic, and zero false negatives on declared fields — which is also the catch: a sensitive field you did not declare passes through in cleartext. Declare defensively, including error and debug paths; a path that never matches costs nothing. (An optional NER pass over free-text fields is on the roadmap — see below.)

Paths are checked when the config loads. A path that this dialect cannot honor — recursive descent, a filter, a slice — is refused at startup rather than reinterpreted into something else, because the failure mode that matters here is a config that looks like it protects a field and does not. A path that is well-formed but simply absent from a given response stays a silent no-op, so defensive declaration remains free.

The declared `semantic_type` and `unit` are what the model is told about each path. The runtime data type is recorded in the vault but not surfaced, since the tool description is built from config before any response exists.

### Robust rehydration

Tokens use distinctive delimiters and 128 random bits (`⟦tok_…⟧`). `rehydrate()` validates every token in the model's answer against the vault: a token that doesn't resolve renders as `[unknown token]` (hallucinated or expired), one the policy refuses renders as `[redacted]`. Neither is silently dropped.

Rehydration is a function your application calls on the final answer, not something that happens on the wire. Two consequences worth knowing before you design around it:

- The model has to preserve the placeholders verbatim for this to work. `from blindfold import PLACEHOLDER_PROMPT` and put it in your system prompt — both demos do exactly that, while the Claude Code and Codex plugins carry the same text inside their `SessionStart` briefing.
- If the model's answer never passes through your code, nothing rehydrates it. That is the situation with any MCP client you did not write — see [`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control).

## Quick start

Blindfold is a Python package, not yet published to PyPI. Install it from source:

```bash
git clone https://github.com/ManuelPr/blindfold && cd blindfold
uv sync            # or:  pip install -e .
```

There are four ways to use it. **Inside Claude Code, pick Mode C. Inside Codex, Mode D protects supported local tool results but leaves placeholders visible. Everywhere else, pick Mode B unless you know why you want Mode A.**

**Mode A — a CLI wrapping another stdio MCP server:**

```bash
# Wrap any stdio MCP server; blindfold reads ./blindfold.yaml if present:
blindfold --config blindfold.yaml -- python -m your_org.some_mcp_server
```

In its default strict profile this protects declared fields in supported JSON
tool/resource results and blocks protocol shapes it cannot inspect: batches,
non-JSON text, blobs, images, and declared paths that no longer match. Setting
`proxy.strict: false` restores compatibility passthrough and explicitly gives
up the complete-boundary claim. Mode A still cannot own the model's final
answer: with a client you did not write, the user sees placeholders. See
[`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control).

**Mode C — a Claude Code plugin:**

```bash
claude --plugin-dir ./plugin        # from a clone; see plugin/README.md
```

Four hooks and one small MCP server, because a host gives Blindfold different
seams than a protocol does:

| Piece | Does what |
|---|---|
| `SessionStart` hook → `additionalContext` | tells the model, once before the first prompt, which paths come back as placeholders and what they mean — and to reproduce them verbatim |
| `PreToolUse` hook | refuses a configured built-in before execution when Blindfold has no tested adapter for its result shape |
| `PostToolUse` hook → `updatedToolOutput` | rewrites the result **the model receives**, preserving the shape expected by Claude Code; audited today for one-part JSON MCP results and JSON in `Bash`/`PowerShell` standard output |
| `mcp-server` (`blindfold mcp-server`) | offers the operations selected by `compute.mode`; arbitrary Python is absent by default |
| `MessageDisplay` hook → `displayContent` | rewrites **what the screen shows**, leaving the transcript untouched — rehydration |

The last one solves the problem Mode A cannot. Because `MessageDisplay` is
display-only, the user reads real values while the conversation keeps the
placeholders — so the values never re-enter the model's context on the next
turn. The proxy has no way to make that distinction.

The first and third exist because a host's hooks **cannot add a tool or edit a
tool description**. Mode A does both by rewriting the `tools/list` response as
it passes the proxy; here the same information has to arrive as session context,
and blind compute has to arrive the way every other tool does.

This mode **requires `storage.backend: sqlite`**: every hook invocation is a
separate process, so the vault has to be shared. The CLI refuses to run the
hooks with a memory vault rather than minting tokens nobody will be able to
resolve.

Configured tools are fail-closed at the host boundary. A built-in such as
`Read` or `WebFetch`, whose output has no audited adapter yet, is denied before
it runs. If an admitted result is not structured JSON or its declared paths no
longer match, the turn stops before another model request can receive the
original. Undeclared tools remain untouched.

**Mode D — a Codex plugin:**

```bash
# plugin source: ./plugins/blindfold-codex
```

Codex can run a hook after supported local tools and replace their result with
Blindfold's tokenized JSON. This covers shell commands, local execution, file
patches, MCP tools, and most local function tools. It does not cover hosted
tools such as web search, and some specialized paths may opt out.

There is one deliberate difference from Claude Code: Codex currently exposes
no display-only hook that can put real values on screen without also returning
them to the conversation. Therefore the model and the user both see
`⟦tok_…⟧`. The protection is useful when opaque output is acceptable; it is not
feature parity with Mode C. See [`plugins/blindfold-codex/`](plugins/blindfold-codex/)
and the exact host matrix in [`docs/host-adapters.md`](docs/host-adapters.md).

**Mode B — an in-process library (used by a harness you write):**

```python
from blindfold import BlindfoldSession
from blindfold.config import load_config

session = BlindfoldSession(load_config("blindfold.yaml"), session_id=session_id)
system_prompt += "\n\n" + session.model_instructions
protected = session.call_protected_tool("get_salary", get_salary, employee_id)
# Send only `protected` to the model.
visible_answer = session.render_final_answer(llm_answer)
```

Mode B is the reference integration: your code owns the tool result, the
authorization decision and the final answer, so the loop actually closes. It
works with any LLM SDK and needs no MCP. `BlindfoldSession` fails closed for an
unknown tool, a missing required path, or a declared table with the wrong
shape. Mark a genuinely optional path with `required: false`.

For user-intent authorization, issue a capability on the trusted side and
require an exact match when executing the model's proposed query:

```python
from datetime import datetime, timedelta, timezone
from blindfold import TableQueryCapability

ops = [{"op": "filter", "column": "salary", "cmp": ">", "value": 70000}]
capability = TableQueryCapability.issue(
    session_id=session_id,
    table_token=table_token,
    ops=ops,
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
)
result_token = session.execute_authorized_query(
    {"table": table_token, "ops": agent_ops}, capability=capability
)
```

The application—not a PII classifier or an LLM judge—decides when to issue the
capability. Changing `70000`, the table, the operation or the session is refused.

Out of the box the security-relevant defaults are a memory vault,
session-bound authorization, controlled table operations, and strict proxy
handling. The subprocess Python sandbox is constructed only by integrations
that explicitly enable or call the `python_unsafe` surface. See
[`examples/demo_chat.py`](examples/demo_chat.py) for a deliberately unsafe
Anthropic SDK compute example.

`BlindfoldSession.model_instructions` supplies the placeholder rules and schema
briefing. Integrations that need per-tool descriptions can still use
`describe_schema()` and `describe_tables()`. The lower-level tokenizer,
rehydrator and handlers remain available for advanced integrations.

## Configuration

Everything deployment-specific lives in one file. **This is the whole of what the current release reads:**

```yaml
tokens:
  default_ttl: 3600         # seconds a token stays resolvable

storage:
  backend: memory           # memory (default) | sqlite
  path: ./vault.db          # sqlite only
  encrypt_at_rest: false    # sqlite only; needs BLINDFOLD_VAULT_KEY

compute:
  mode: controlled           # disabled | controlled | python_unsafe
  max_calls_per_token: 8    # blindfold_compute calls on one token per window; 0 disables
  rate_window_s: 60         # window length in seconds

proxy:
  strict: true              # false permits uninspected compatibility passthrough

schemas:
  hr_api.get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year

  hr_api.list_employees:
    tables:                   # a whole list behind one token
      - path: $.employees
        columns:              # what the model may query on
          - name: salary
            semantic_type: salary
            unit: EUR/year
          - name: dept

resources:                  # MCP resources, keyed by URI glob
  "file:///hr/*.json":
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
```

Pick `sqlite` when the vault has to outlive the process, or when tokenizing and
rehydrating happen in different processes. Pick `memory` — the default — when
neither is true: it is faster and puts nothing on disk. Asking for a backend this release does not have fails at load rather than
being ignored.

`encrypt_at_rest: true` seals values with AES-256-GCM. The key comes from
`BLINDFOLD_VAULT_KEY` (32 bytes, base64) and never from the config file, since a
key kept beside the database it protects is decoration. Install with
`pip install blindfold[encryption]`, and generate a key with:

```bash
python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
```

Only the value is sealed; token, session and timestamps stay readable because
the store queries on them. Holding the file still reveals how many records
exist and when.

`default_ttl` deserves a thought before you deploy: it governs how long a conversation containing placeholders stays readable. At the default of one hour, an answer the user comes back to tomorrow rehydrates as `[unknown token]`. A memory vault also loses live records on restart; SQLite preserves them until their TTL.

### The rest of the file **[planned]**

The block below is a design sketch, **not valid current configuration**.
Unknown top-level sections are retained for forward compatibility, but typos or
unimplemented keys inside a section Blindfold already understands are rejected
rather than silently ignored. Nothing below changes runtime behavior today.

```yaml
mode: local                 # local | server

detokenize:
  policy: session_bound     # allow_all | session_bound | claims | webhook
  # webhook_url: https://myapp.internal/authz

tokens:
  consistency:              # per semantic_type
    person_name: stable     # same value → same token (enables equality reasoning)
    salary: fresh           # new token every time (no equality leakage)

identity:
  forward_headers: [Authorization, X-User-Id]   # only meaningful once an HTTP mode exists

compute:
  sandbox: subprocess       # subprocess | docker | disabled
  timeout_s: 5
  network: false
```

Where today's behavior differs from what those planned keys suggest: every token is minted fresh (no `stable` consistency), the policy is always `session_bound`, and `network: false` is not an enforced sandbox boundary. The subprocess sandbox exists only for the explicit `python_unsafe` surface and still has a hard-coded five-second timeout — see [Threat model](#threat-model--limitations).

Two built-in profiles are designed to cover the common cases — **`local`** (single user, memory/SQLite vault, stdio MCP transport) and **`server`** (multi-user, Redis vault, webhook authorization, HTTP transport). **[planned]**: only the `local` shape exists, now with either vault.

## Pluggable architecture

The core is deliberately small: intercept → tokenize → track lineage → blind compute → rehydrate. Everything environment-dependent hides behind three interfaces.

| Port | Contract | Ships today | Designed **[planned]** |
|---|---|---|---|
| `TokenStore` | `mint_token`, `put`, `get`, `resolve`, `find_by_session`, `invalidate_cascade`, `purge_expired` | `memory` *(default)*, `sqlite` | `redis`, `postgres` |
| `DetokenizePolicy` | `can_reveal` / `can_compute` / `can_query` | `session_bound` *(default)* | `allow_all`, `claims`, `webhook` |
| `ComputeSandbox` | `run(code, resolved_inputs) → value` | `subprocess` | `docker` |

The ports exist so the rest is additive rather than a rewrite. The two `TokenStore` implementations are held to one behavioural suite that uses the public interface only, so swapping them changes nothing else.

Notes for adapter authors:

- **TTL lives in the core**, so no storage adapter can forget it: expiry is checked on every `get`. SQLite values can be sealed with AES-256-GCM using a key supplied outside the database.
- Detokenization is an **authorization point**, not a string substitution. `SessionBoundPolicy` refuses any token minted in another session, so a guessed ID resolves to `[redacted]` rather than a value. Per-user separation on top of that arrives with multi-user deployment **[planned]**.
- Separate policy hooks gate arbitrary compute (`can_compute`) and constrained table queries (`can_query`), since permission to use a fixed query language must not imply permission to resolve the same value inside Python.

## Deployment

**Local (personal agent)** — the only deployment that exists today. Everything runs on your machine. With the default memory vault nothing survives the process, which matters because the placeholders you already sent the model *do* survive — in your chat history, your logs, your app's database — so a restart would leave those conversations pointing at values that exist nowhere. Switch to `backend: sqlite` if that matters, and treat the file as the secret it holds.

**Server (internal chatbot, multi-user) [planned]:** Blindfold and its vault would run **server-side, inside your network perimeter**, next to the APIs they wrap — never in the browser, never on the client. Rehydration as the last server-side hop before the response reaches the user's frontend, gated by the configured policy. Intended vault: Redis (native TTL) for tokens and encrypted values, plus Postgres for the lineage/audit log *without* the values. None of this is built; the per-user isolation it implies does not exist yet either.

## Threat model & limitations

Read this before deploying. Honesty here is a feature.

**What Blindfold protects against:** the LLM provider (and the model itself) learning the values returned by your private APIs, including values derived from them through computation — **against a model that follows the protocol**. A model actively trying to extract the values has channels available to it today; they are listed below, and closing them is ongoing work, not a solved problem.

**What it does NOT protect against (non-goals):**

- **Access control between your users and your APIs.** Blindfold forwards the caller's identity headers untouched and lets *your* APIs enforce their own ACLs. If your API answers salary queries to anyone holding a service token, Blindfold will faithfully tokenize data the caller should never have obtained. Enforcement belongs upstream; we just don't break it.
- **Prompt-side leakage.** The user's question still goes to the provider. *"What is Andrea Tuscano's salary?"* reveals a name and an intent even if the answer is tokenized. Optional inbound prompt tokenization (NER-based) is planned, at a cost in answer quality.
- **Inference leakage from planned stable tokens.** If the planned `consistency: stable` option is implemented, equality between occurrences will become visible to the provider. The current implementation always mints fresh tokens and does not read this planned key.
- **A malicious or prompt-injected model when `compute.mode: python_unsafe` is enabled.** This optional profile deliberately assumes a cooperative model.

  What holds: results leave the sandbox only as new vault tokens, derived tokens inherit their inputs' policies and shortest TTL, `resolve()` refuses any token not declared in `inputs`, and the error channel carries exception *types* only — never messages, never child output.

  What does not hold today:

  | Channel | Status | Closable? |
  |---|---|---|
  | Exception text was forwarded to the model — `raise ValueError(resolve(t))` returned the value in one call | **closed** | done: types only, with six regression tests |
  | `open()` and `import` gave the child's code the filesystem | **raised, not closed** | done: builtins are an allow-list, so the obvious routes are absent. Escaping through Python's object graph needs no builtins and remains possible |
  | Network reachable from compute code | **raised, not closed** — `import socket` now fails like any import. It was open on Linux/macOS; on Windows it failed only as a side effect of the stripped environment breaking socket initialization, an accident, not a defense | **properly only at OS level**: a container with networking off, or equivalent sandboxing |
  | Success-vs-failure as a one-bit oracle — `result = 1/0 if resolve(t) > 50000 else 'ok'`, repeated, recovers an exact number in ~20 calls | **open, contained** | **no**, not while the model submits arbitrary Python — only a fixed set of operations (the table design above) removes it. `compute.max_calls_per_token`/`rate_window_s` (default: 8 attempts per 60s) reserve quota atomically against every original secret in the input lineage. Successes, failures and timeouts all count, and copying a value into a derived token does not reset the budget. Blocks are logged and surfaced by `blindfold audit`. A patient attacker can still continue across windows. |

  Capabilities remain optional and address authorization and intent; they are not required for the lineage-aware confidentiality rate limit. A richer CaMeL-style data-flow layer could further reduce the broader prompt-injection risk. Until then: **do not run blind compute against data whose exposure you cannot tolerate, if the model's inputs come from sources you do not control.** Do not run with `sandbox: disabled` on real data at all.
- **Quality-preserving magic.** If the model only sees `⟦tok⟧`, it cannot judge whether a salary is competitive or a diagnosis plausible. Blind compute covers *mechanical* operations (compare, aggregate, filter); *semantic* judgment on hidden values is fundamentally impossible. That's the deal.

**Operational cautions:** rehydration is validated, but the model can still mangle placeholders — instruct it not to (`PLACEHOLDER_PROMPT`, exported from the package root) and expect the occasional `[unknown token]`. Vault compromise equals data compromise: values sit in cleartext in process memory (and on disk, unless `encrypt_at_rest` is on) today, so keep TTLs short and run Blindfold in the same trust zone as the APIs it protects. Use `blindfold audit <transcript>` after a real session to check what actually reached the model, rather than trusting the screen — a working install and no install at all look identical there.

## Comparison

| | Prompt PII redaction | Tool-result tokenization | Reversible (rehydration) | Blind compute on hidden data | Lineage / audit DAG | Self-hosted, open source |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| **Blindfold** | plannedⁱ | ✅ | ✅ⁱⁱⁱ | ✅ⁱᵛ | ✅ | ✅ |
| Microsoft Presidio | ✅ | ❌ | partial | ❌ | ❌ | ✅ |
| Philter | ✅ | ❌ | ✅ | ❌ | ❌ | partial |
| LLM Guard | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| anonymize.dev (MCP) | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ (SaaS) |
| CaMeL (research) | n/a | n/aⁱⁱ | n/a | ✅ | ✅ (capabilities) | ✅ (research code) |

ⁱ Optional inbound NER pass, on the roadmap.
ⁱⁱ CaMeL targets prompt injection, not provider-side privacy; its P-LLM/interpreter split is the closest architectural relative of Blindfold's blind compute, and a direct inspiration.
ⁱⁱⁱ In your own application code. Not available through an MCP client you did not write — see [`LIMITATIONS.md`](LIMITATIONS.md#rehydration-requires-a-client-you-control).
ⁱᵛ Mechanical operations on a handful of tokens. Does not scale to long result sets until collective tokens land, and the sandbox is not hardened against a hostile model — see [Threat model](#threat-model--limitations).

## Related work

- **CaMeL** ([Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813), Google DeepMind) — control/data-flow separation with capabilities; the privileged LLM writes code without ever seeing raw data.
- **Microsoft Presidio** — the reference open-source PII detection/anonymization engine.
- **Philter, LLM Guard, PII Shield** — prompt-side redaction proxies with reversible tokenization.

## Roadmap

Ordered by what the current release most needs, not by ambition.

- [x] MVP: stdio MCP wrapper, memory store, session-bound policy, subprocess sandbox
- [x] **Sandbox output hygiene** — exception types only; child stdout/stderr go to the operator, never to the model
- [x] **Path validation at config load** — syntax the dialect cannot honor is refused at startup instead of being silently reinterpreted
- [x] **Expiry frees memory** — the vault sweeps expired records instead of holding cleartext values for the life of the process
- [x] **SQLite store** — a vault that survives a restart and can be shared between processes
- [x] **Claude Code hooks** — tokenization and reveal without a proxy, and without placeholders reaching the user
- [x] **Mode C implemented experimentally** — session briefing, strict supported-result adapters, shared operation server and display-only reveal
- [x] **Host-specific adapters and fail-closed contracts** — Claude Code preserves each admitted result shape; configured unsupported built-ins are denied before execution
- [x] **Codex plugin** — protected local tool results are replaced before the model continues; placeholders remain visible because Codex has no display-only reveal hook
- [x] **Opt-in Claude Code compatibility check** — one real tool call, pinned to an exact installed version, audited against the persisted transcript
- [x] **Collective (table) tokens** — one placeholder per list, queried by a fixed operation set and prohibited as arbitrary-Python inputs
- [x] **Safe compute profiles** — controlled table operations by default; arbitrary Python only through explicit `python_unsafe` opt-in
- [x] **Trusted-side table capabilities** — an application can authorize one exact session/table/query/expiry without an LLM judge
- [x] **Strict Mode A boundary** — unsupported batches/content and protected shape drift are blocked by default, with permissive passthrough explicitly downgraded
- [x] **128-bit capability tokens and mandatory host sessions** — no shared `unknown` session fallback
- [x] **Encryption at rest** — AES-256-GCM, key from the environment, never from the config file
- [x] **CI on Linux, macOS and Windows** — including the sandbox probes, so the documented behaviour is asserted per platform
- [x] **Restricted builtins in the compute child** — the easy filesystem and network paths are gone without anyone installing Docker; not a boundary, a higher cost
- [x] **Export the placeholder-preserving prompt fragment** as `PLACEHOLDER_PROMPT`, used by both demos and by the Mode C briefing
- [x] **`blindfold audit` diagnostic** — cross-references a transcript against the vault for placeholders and exact cleartext matches; useful evidence, not proof of non-disclosure
- [x] **Lineage-wide compute attempt quota** — atomic across threads and SQLite processes; successes, failures and timeouts share the original secrets' budget, derived tokens cannot reset it, and blocked bursts appear in `blindfold audit`
- [ ] Table joins, group-by and cross-table aggregation — the operations collective tokens do not have yet
- [ ] Docker sandbox — the OS-level answer to network and filesystem, after the cheap in-process measures
- [ ] HTTP proxy mode for plain REST APIs
- [ ] Redis + Postgres adapters, webhook policy, audit log exporter
- [ ] Optional inbound prompt tokenization (NER)
- [ ] Richer CaMeL-style capability propagation beyond exact Mode B table queries

## Documentation

### Current — kept in step with the code

- **[`docs/modes.md`](docs/modes.md)** — which of the four integration modes you want, what each one can and cannot do, and the one question that decides it. Read this before installing anything.
- **[`docs/host-adapters.md`](docs/host-adapters.md)** — exact Claude Code and Codex coverage, failure behavior, compatibility testing, and the evidence required before building a custom client.
- **[`docs/architecture.md`](docs/architecture.md)** — how the code actually works. Component-by-component tour with a full end-to-end frame-by-frame example. Start here after this README.
- **[`LIMITATIONS.md`](LIMITATIONS.md)** — what Blindfold does *not* do, split into by-design (permanent) and MVP (temporary), with a cost estimate on every closable gap. Read before deploying against real data.
- **[`blindfold.example.yaml`](blindfold.example.yaml)** — a copy-paste-ready configuration example, containing exactly the keys the current release reads.
- **[`examples/try_modes.py`](examples/try_modes.py)** — runs the proxy, library, and Claude Code flows against the fake HR server with no API key. Codex needs its real hook host, so its contract is covered by the test suite instead of this scripted demo.
- **[`examples/demo_chat.py`](examples/demo_chat.py)** — a runnable Anthropic + Blindfold + fake HR MCP loop.

### Project history — frozen, not maintained

These two record how the MVP was designed and built in July 2026. They are useful for understanding *why* decisions were made and are **not updated as the code changes** — where they disagree with the three documents above, the documents above are right. (Known example: both describe the JSONPath dialect as supporting single-level wildcards; the implementation handles nested ones.)

- **[`docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md`](docs/superpowers/specs/2026-07-15-blindfold-mvp-design.md)** — the formal MVP design doc: scope, architecture, data model, key flows, testing strategy.
- **[`docs/superpowers/plans/2026-07-15-blindfold-mvp.md`](docs/superpowers/plans/2026-07-15-blindfold-mvp.md)** — the task-by-task implementation plan the MVP was built from. Long (3,200 lines); read it for the reasoning behind a specific file, not front to back.

## Contributing

Issues and PRs welcome. Especially wanted: storage/policy adapters, red-teaming of the threat model, and real-world schema examples. Please read the threat model section before proposing features that move detokenization client-side.

## License

MIT (proposed).
