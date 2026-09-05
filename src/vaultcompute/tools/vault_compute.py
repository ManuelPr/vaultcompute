"""vault_compute MCP tool definition and handler."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from vaultcompute.core.compute_attempts import ComputeRateLimitError
from vaultcompute.core.lineage import Lineage, VaultRecord, compose_policy, compose_ttl
from vaultcompute.ports.policy import DetokenizeContext, DetokenizePolicy
from vaultcompute.ports.sandbox import ComputeSandbox, SandboxTimeoutError
from vaultcompute.ports.token_store import TokenStore

VAULT_COMPUTE_TOOL_NAME = "vault_compute"

_TOOL_DESCRIPTION = (
    "UNSAFE COOPERATIVE-MODEL PROFILE: run Python code on hidden values behind "
    "tokens ⟦tok_…⟧. Never probe a hidden value with repeated or adaptive "
    "comparisons, deliberate errors, timeouts, or cloned derived tokens. "
    "Every token the code touches via resolve(...) MUST be listed in `inputs`. "
    "resolve(token) returns the hidden value ALREADY in its real type (a number stays "
    "a number, a string stays a string) — never a JSON string, never the whole tool "
    "response. Do not call json.loads(...) on it or index into it with ['field']. "
    "The code runs with a restricted set of builtins: no import, no open, no eval. "
    "The code MUST assign its result to a variable named `result` and the result "
    "MUST be JSON-serializable. The tool returns a NEW token (not the value); "
    "compose further tokens by calling this tool again."
)


def build_tool_definition() -> dict:
    return {
        "name": VAULT_COMPUTE_TOOL_NAME,
        "description": _TOOL_DESCRIPTION,
        "inputSchema": {
            "type": "object",
            "required": ["code", "inputs"],
            "properties": {
                "code": {"type": "string", "description": "Python code assigning `result`."},
                "inputs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Token strings the code will resolve.",
                },
            },
        },
    }


def _infer_dtype(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "object"


def _lineage_roots(tokens: list[str], store: TokenStore) -> tuple[str, ...]:
    """Return the original secrets behind inputs, not their latest aliases."""
    roots: set[str] = set()
    visited: set[str] = set()

    def visit(token: str) -> None:
        if token in visited:
            return
        visited.add(token)
        record = store.get(token)
        if record is None or not record.lineage.inputs:
            roots.add(token)
            return
        for parent in record.lineage.inputs:
            visit(parent)

    for token in tokens:
        visit(token)
    # A malformed cycle inserted through a custom store must not become a way
    # to escape accounting.
    return tuple(sorted(roots or set(tokens)))


def handle_vault_compute(
    args: dict,
    *,
    store: TokenStore,
    policy: DetokenizePolicy,
    sandbox: ComputeSandbox,
    session_id: str,
    ttl_seconds: int,
    code_timeout_s: float = 5.0,
    max_calls_per_token: int = 8,
    rate_window_s: int = 60,
) -> str:
    code = args.get("code")
    inputs = args.get("inputs")
    if not isinstance(code, str) or not isinstance(inputs, list):
        raise ValueError("vault_compute requires string `code` and list `inputs`")
    if max_calls_per_token < 0 or rate_window_s <= 0:
        raise ValueError(
            "compute rate limit requires max_calls_per_token >= 0 and rate_window_s > 0"
        )

    ctx = DetokenizeContext(session_id=session_id)
    resolved: dict[str, Any] = {}
    input_records: list[VaultRecord] = []
    for token in inputs:
        record = store.get(token)
        if record is None:
            raise ValueError(f"unknown or expired input token: {token}")
        if not policy.can_compute(ctx, record):
            raise ValueError(f"policy denied compute on token: {token}")
        resolved[token] = record.value
        input_records.append(record)

    now = datetime.now(tz=timezone.utc)
    ttl = (
        compose_ttl(input_records)
        if input_records
        else now + timedelta(seconds=ttl_seconds)
    )
    derived_policy = compose_policy([r.policy for r in input_records])
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()

    attempt_id: str | None = None
    if input_records:
        try:
            attempt_id = store.reserve_compute_attempt(
                session_id=session_id,
                root_tokens=_lineage_roots(inputs, store),
                input_tokens=tuple(inputs),
                code_digest=digest,
                # Keep metadata alive for at least the whole quota/sandbox
                # interval even if an input token was seconds from expiry.
                expires_at=max(
                    ttl,
                    now
                    + timedelta(seconds=max(float(rate_window_s), code_timeout_s) + 1),
                ),
                max_attempts=max_calls_per_token,
                window_s=rate_window_s,
            )
        except ComputeRateLimitError as exc:
            print(
                "[vaultcompute] suspicious compute burst blocked: "
                f"{exc.recent_count} lineage attempts in {exc.window_s}s",
                file=sys.stderr,
            )
            raise

    try:
        value = sandbox.run(code=code, inputs=resolved, timeout_s=code_timeout_s)
        new_token = TokenStore.mint_token()
        store.put(
            VaultRecord(
                token=new_token,
                value=value,
                dtype=_infer_dtype(value),
                semantic_type=None,
                unit=None,
                session_id=session_id,
                created_at=now,
                ttl=ttl,
                lineage=Lineage(
                    op="vault_compute", inputs=tuple(inputs), code_digest=digest
                ),
                policy=derived_policy,
            )
        )
    except SandboxTimeoutError:
        if attempt_id is not None:
            store.finish_compute_attempt(attempt_id, "timeout")
        raise
    except Exception:
        if attempt_id is not None:
            store.finish_compute_attempt(attempt_id, "failed")
        raise
    if attempt_id is not None:
        store.finish_compute_attempt(attempt_id, "succeeded")
    return new_token
