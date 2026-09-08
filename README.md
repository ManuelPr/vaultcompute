# VaultCompute

**Private data. Usable reasoning.**

VaultCompute is a Python privacy layer for structured LLM tool results. It
replaces declared private values with opaque tokens before your application
sends the result to a model. Real values remain in a local vault; your
application restores authorized values only when displaying the final answer.

Use it when an agent needs to filter, sort or aggregate private records without
putting those records in the model's context. Your application still owns tool
access, user authorization and the conversation loop.

**Status: pre-alpha.** The library, MCP proxy, token stores and host adapters
are implemented. Host integrations are version-sensitive; test their supported
result shapes before relying on them. See [limitations](LIMITATIONS.md).

## How it works

1. **Declare** sensitive fields or whole tables in a schema for each tool.
2. **Protect** the result: store private values in memory or SQLite and replace
   them with fresh, opaque placeholders containing 128 random bits.
3. **Query** a hidden table with `vault_table`. Fixed operations return another
   placeholder, with lineage and inherited restrictions.
4. **Render** the final answer for the user after authorization. Keep the
   rendered cleartext out of subsequent model requests.

For example, a salary tool's result becomes `{"employees": "⟦tok_…⟧"}`. The
model can request a sort by salary and a limit of one row. It receives another
token; only final rendering reveals the selected employee. The abbreviated
placeholder here is illustrative; real placeholders must be copied verbatim.

The default `controlled` profile supports `filter`, `sort_by`, `limit`,
`select`, `sum`, `mean`, `min`, `max` and `count` over declared tables.
It executes no model-written code. Arbitrary Python through `vault_compute`
requires explicit `compute.mode: python_unsafe` opt-in and assumes a cooperative
model; it is not a safe boundary against malicious code or prompt injection.

## Quick start

Install from this repository with Python 3.11+ and `uv`:

```bash
git clone https://github.com/ManuelPr/vaultcompute
cd vaultcompute
uv sync
```

Alternatively, in your own Python environment, run `python -m pip install -e .`.
The commands below use `uv run` to select the project's environment.

Save this complete example as `quickstart.py` in the repository, then run
`uv run python quickstart.py`. It uses synthetic data and a scripted query;
no API key, model service or MCP server is required.

```python
import json
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from vaultcompute import TableQueryCapability, VaultComputeSession
from vaultcompute.config import VaultComputeConfig

sys.stdout.reconfigure(encoding="utf-8")


def list_employees():
    return {"employees": [
        {"name": "Manuel", "salary": 62000},
        {"name": "Andrea", "salary": 71000},
    ]}


config = VaultComputeConfig.model_validate({
    "schemas": {
        "list_employees": {
            "tables": [{
                "path": "$.employees",
                "columns": [{"name": "name"}, {"name": "salary"}],
            }]
        }
    }
})
session = VaultComputeSession(config, session_id=uuid4().hex)
protected = session.call_protected_tool("list_employees", list_employees)
print("Tool result for the model:", json.dumps(protected, ensure_ascii=False))

# The trusted application authorizes this exact request: highest-paid employee.
ops = [
    {"op": "sort_by", "column": "salary", "desc": True},
    {"op": "limit", "n": 1},
    {"op": "select", "columns": ["name", "salary"]},
]
capability = TableQueryCapability.issue(
    session_id=session.session_id,
    table_token=protected["employees"],
    ops=ops,
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
)

# Scripted stand-in for the model's proposed vault_table call.
proposed_query = {"table": protected["employees"], "ops": ops}
result_token = session.execute_authorized_query(
    proposed_query, capability=capability,
)
model_answer = f"Highest-paid employee: {result_token}"
print("Model answer:", model_answer)
print("User sees:", session.render_final_answer(model_answer))
```

The first two lines contain placeholders. The last line shows:

```text
User sees: Highest-paid employee: [{"name": "Andrea", "salary": 71000}]
```

In a real integration, add `session.model_instructions` to the system prompt,
send only protected tool results to the model, and dispatch its proposed table
queries through the authorized handler. Issue capabilities from trusted
application decisions, not automatically from whatever query the model proposes.
Render only at the final user-facing boundary; never add that cleartext to model
history. Async tools use `await session.call_protected_tool_async(...)`.

The library does not supply an LLM conversation loop. See the
[Python API](docs/api.md) for its supported methods and integration contract.

## Choose an integration

| Mode | Use it when | Status | Final values |
|---|---|---|---|
| **B — Python library** | You own the application and model loop | Reference integration | Your application renders them |
| **A — stdio MCP proxy** | You wrap one existing MCP server | Beta | Placeholders unless your client implements final rendering |
| **C — Claude Code plugin** | You use the supported host hook shapes | Experimental | Display-only reveal through the adapter |
| **D — Codex plugin** | You protect supported local tool results | Experimental guardrail | Placeholders remain visible |

Start with **Mode B** when you write the application. For Mode A, configure
your MCP client to launch the proxy with an explicit configuration path:

```bash
uv run vaultcompute --config vaultcompute.yaml -- python -m your_org.some_mcp_server
```

The downstream command above is a placeholder for your own MCP server. The
proxy cannot intercept a third-party client's final answer for display.

For host setup, follow the [Claude Code plugin guide](plugin/README.md) or the
[Codex plugin guide](plugins/vaultcompute-codex/README.md). Both require a shared
SQLite vault and the `vaultcompute` command on the host's `PATH`.
The [host adapter contract](docs/host-adapters.md) lists supported shapes,
failure behavior and real-host verification. Hook fixtures alone do not prove
compatibility with an installed host version.

## Configuration

The Python example above creates its configuration directly. For the CLI and
plugins, create `vaultcompute.yaml`; start from
[vaultcompute.example.yaml](vaultcompute.example.yaml). A minimal declaration is:

```yaml
schemas:
  get_salary:
    sensitive_fields:
      - path: $.salary
        semantic_type: salary
        unit: EUR/year
      - path: $.bonus
        required: false
```

- Tool names must match the names passed to the integration exactly.
- Paths are **required by default**. A missing required path stops protection;
  `required: false` permits a legitimate absence. Unsupported path syntax and
  overlapping declarations within one schema are rejected at load.
- A `tables` declaration hides the entire list. Its columns specify what the
  model may query. Scalar placeholders are not queryable with `vault_table`.
- MCP `resources` use URI globs and support **`sensitive_fields` only**.
  Resource `tables` declarations are rejected. An array can be hidden as a
  sensitive field, but that does not make it a queryable table.
- `compute.mode` defaults to `controlled`; the proxy and host operation server
  also accept `disabled` and the explicit `python_unsafe` profile.

### Storage and expiry

Memory storage is the default and loses its vault when the process ends.
Use SQLite when tokens must survive restarts or be shared across processes:

```yaml
storage:
  backend: sqlite
  path: ./vault.db
  encrypt_at_rest: true
tokens:
  default_ttl: 3600
```

For encryption, install the optional dependency from the repository:

```bash
uv sync --extra encryption
```

Or use `python -m pip install -e ".[encryption]"` in your own environment.
Encrypted storage requires `VAULTCOMPUTE_VAULT_KEY` in every process that opens
the vault: a base64-encoded 32-byte key, supplied outside the configuration and
database. Values are encrypted with AES-256-GCM; session and lineage metadata
remain readable. Without encryption enabled, SQLite stores cleartext values.

The default token lifetime is one hour. SQLite persistence does not extend it:
expired or unknown tokens render as `[unknown token]`; tokens denied by policy
render as `[redacted]`.

## Threat model & limitations

Protection applies to **declared values on supported result paths**. It does
not cover the entire conversation or replace your application's access control.

- **Undeclared values remain visible.** Review schemas whenever tools change.
  User prompts, tool arguments and values sent through other application paths
  are outside this protection.
- **Unsupported results are blocked on strict paths.** Mode A strict rejects
  batches and protected results with non-JSON text, images, blobs or
  `structuredContent`. The Claude Code MCP adapter also rejects
  `structuredContent`. `proxy.strict: false` allows compatibility passthrough
  and gives up that blocking guarantee.
- **Host coverage is limited.** Tools that do not enter an adapter's supported
  hooks are not protected. Host telemetry may record original results before
  a hook runs. The Codex adapter does not provide display-only rehydration.
- **The vault and final rendering are trusted.** Real values exist in process
  memory. Session-bound policy is the default; application authorization must
  decide which user may access the underlying data and approve a query.
- **Arbitrary Python is unsafe.** Its restricted subprocess and lineage-wide
  attempt quota do not eliminate extraction through deliberate errors or
  sandbox escapes. Table-derived tokens cannot be used as Python inputs.
- **Hidden values limit reasoning.** The model can request supported mechanical
  operations, but cannot independently assess the meaning of an unseen value.

`vaultcompute audit` checks transcripts against live vault records for exact
cleartext matches and suspicious compute attempts. It is diagnostic evidence,
not proof of non-disclosure; undeclared, transformed or expired values can evade
the check. See [LIMITATIONS.md](LIMITATIONS.md) for the detailed threat model.

## Development and next steps

From the repository:

```bash
uv sync --all-extras --dev
uv run pytest
```

The CI workflow defines a test matrix for Linux, macOS and Windows on Python
3.11–3.13. Host compatibility also needs real-host verification.

The next release priorities are testing the built package in clean environments,
verifying supported host versions and exercising real integration cases.
Joins, group-by, HTTP transport and additional storage backends are possible
future work, not available features.

## Documentation and contributing

- [Python API](docs/api.md): supported imports and the application boundary.
- [Integration modes](docs/modes.md): detailed setup and tradeoffs.
- [Host adapters](docs/host-adapters.md): exact coverage and compatibility checks.
- [Architecture](docs/architecture.md): core, storage, policy and adapters.
- [Configuration example](vaultcompute.example.yaml): supported YAML settings.
- [Limitations](LIMITATIONS.md) and [changelog](CHANGELOG.md).

Issues and pull requests are welcome. Useful contributions include reproducible
bugs, tests using synthetic data, schema examples and integration feedback.
Include the package version, integration mode and host version where relevant;
do not put real private data or vault keys in public reports.

## License

[MIT](LICENSE). Copyright © 2026 Manuel Pernigotto.
