"""Codex-specific lifecycle contract tests."""

import io
import json

from vaultcompute import hooks
from vaultcompute.cli import run_hook
from vaultcompute.config import VaultComputeConfig, SensitiveFieldConfig, ToolSchemaConfig
from vaultcompute.core.policy import SessionBoundPolicy
from vaultcompute.core.sqlite_store import SQLiteTokenStore
from vaultcompute.hosts import CODEX

TOOL = "mcp__hr__get_salary"
SESSION = "codex-session-1"


def _config() -> VaultComputeConfig:
    return VaultComputeConfig(
        schemas={
            TOOL: ToolSchemaConfig(
                sensitive_fields=[SensitiveFieldConfig(path="$.salary")]
            )
        }
    )


def test_codex_session_brief_is_explicit_that_tokens_remain_visible(tmp_path):
    store = SQLiteTokenStore(tmp_path / "vault.db")
    try:
        out = hooks.dispatch(
            hooks.SESSION_START,
            {"session_id": SESSION},
            config=_config(),
            store=store,
            policy=SessionBoundPolicy(),
            host=CODEX,
        )
    finally:
        store.close()
    brief = out["hookSpecificOutput"]["additionalContext"]
    assert "no display-only rehydration hook" in brief
    assert "user will see placeholders" in brief


def test_codex_replaces_a_structured_result_with_tokenized_feedback(tmp_path):
    store = SQLiteTokenStore(tmp_path / "vault.db")
    try:
        out = hooks.dispatch(
            hooks.POST_TOOL_USE,
            {
                "session_id": SESSION,
                "tool_name": TOOL,
                "tool_response": {"name": "Andrea", "salary": 71000},
            },
            config=_config(),
            store=store,
            policy=SessionBoundPolicy(),
            host=CODEX,
        )
    finally:
        store.close()
    assert out["continue"] is False
    protected = json.loads(out["stopReason"])
    assert protected["name"] == "Andrea"
    assert protected["salary"].startswith("⟦tok_")
    assert "71000" not in out["stopReason"]


def test_codex_replaces_an_mcp_content_object(tmp_path):
    store = SQLiteTokenStore(tmp_path / "vault.db")
    try:
        out = hooks.dispatch(
            hooks.POST_TOOL_USE,
            {
                "session_id": SESSION,
                "tool_name": TOOL,
                "tool_response": {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"name": "Andrea", "salary": 71000}',
                        }
                    ]
                },
            },
            config=_config(),
            store=store,
            policy=SessionBoundPolicy(),
            host=CODEX,
        )
    finally:
        store.close()
    assert out["continue"] is False
    assert json.loads(out["stopReason"])["salary"].startswith("⟦tok_")


def test_codex_malformed_result_is_replaced_by_safe_feedback(tmp_path):
    store = SQLiteTokenStore(tmp_path / "vault.db")
    try:
        out = hooks.dispatch(
            hooks.POST_TOOL_USE,
            {
                "session_id": SESSION,
                "tool_name": TOOL,
                "tool_response": "Andrea salary is 71000",
            },
            config=_config(),
            store=store,
            policy=SessionBoundPolicy(),
            host=CODEX,
        )
    finally:
        store.close()
    assert out["continue"] is False
    assert "71000" not in out["stopReason"]
    assert "not JSON" in out["stopReason"]


def test_codex_has_no_message_display_event():
    assert hooks.MESSAGE_DISPLAY not in hooks.events_for(CODEX)


def test_codex_cli_selects_the_codex_adapter(monkeypatch, capsys, tmp_path):
    cfg = tmp_path / "vaultcompute.yaml"
    cfg.write_text(
        f"storage:\n  backend: sqlite\n  path: {tmp_path / 'vault.db'}\n"
        f"schemas:\n  {TOOL}:\n    sensitive_fields:\n      - path: $.salary\n",
        encoding="utf-8",
    )
    event = {
        "session_id": SESSION,
        "tool_name": TOOL,
        "tool_response": {"salary": 71000},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = run_hook([hooks.POST_TOOL_USE, "--host", CODEX, "--config", str(cfg)])
    captured = capsys.readouterr()
    assert code == 0
    out = json.loads(captured.out)
    assert out["continue"] is False
    assert "71000" not in out["stopReason"]
