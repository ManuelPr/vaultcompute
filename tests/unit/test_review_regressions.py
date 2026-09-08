"""Privacy boundary regressions reproduced during the project review."""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest

from vaultcompute import cli
from vaultcompute.config import VaultComputeConfig
from vaultcompute.core.vault import MemoryTokenStore
from vaultcompute.hosts.claude_code import handle_post_tool_use
from vaultcompute.proxy import (
    _pump_child_to_client,
    _tokenize_resource_read,
    build_proxy_state,
    ProxyProtectionError,
)


@pytest.mark.parametrize("host", ["proxy", "claude"])
async def test_parallel_structured_content_is_blocked(host):
    config = VaultComputeConfig.model_validate({"schemas": {
        "mcp__hr__salary": {"sensitive_fields": [{"path": "$.salary"}]}
    }})
    response = {
        "content": [{"type": "text", "text": '{"salary": 76543}'}],
        "structuredContent": {"salary": 76543},
    }
    if host == "claude":
        out = handle_post_tool_use(
            {"tool_name": "mcp__hr__salary", "session_id": "review",
             "tool_response": response}, config=config, store=MemoryTokenStore(),
        )
        assert out.get("continue") is False
    else:
        state = build_proxy_state(config)
        state.pending_calls[1] = "mcp__hr__salary"
        stream = asyncio.StreamReader()
        stream.feed_data((json.dumps({"id": 1, "result": response}) + "\n").encode())
        stream.feed_eof()
        written = []
        await _pump_child_to_client(SimpleNamespace(stdout=stream), written.append, state)
        out = json.loads(written[0])
        assert "error" in out
    assert "76543" not in json.dumps(out)


def resource_config(first, second):
    return VaultComputeConfig.model_validate({"resources": {
        "db://*": {"sensitive_fields": first},
        "db://hr": {"sensitive_fields": second},
    }})


@pytest.mark.parametrize("reverse", [False, True])
def test_resource_overlap_preserves_all_declared_values(reverse):
    fields = [[{"path": "$.employee.salary"}], [{"path": "$.employee"}]]
    if reverse:
        fields.reverse()
    state = build_proxy_state(resource_config(*fields))
    employee = {"salary": 76543, "bank": "SYNTHETIC-IBAN"}
    msg = {"result": {"contents": [{"uri": "db://hr", "text": json.dumps({"employee": employee})}]}}
    _tokenize_resource_read(msg, "db://hr", state)
    text = msg["result"]["contents"][0]["text"]
    assert "76543" not in text and "SYNTHETIC-IBAN" not in text
    assert state.store.resolve(json.loads(text)["employee"]) == employee


def test_resource_merge_preserves_required_descendant():
    state = build_proxy_state(resource_config(
        [{"path": "$.employee", "required": False}],
        [{"path": "$.employee.salary"}],
    ))
    msg = {"result": {"contents": [{"uri": "db://hr", "text": '{"employee": {"bank": "secret"}}'}]}}
    with pytest.raises(ProxyProtectionError, match="missing"):
        _tokenize_resource_read(msg, "db://hr", state)


def test_resource_tables_are_refused_until_supported():
    with pytest.raises(ValueError, match="resource.*tables|tables.*resource"):
        VaultComputeConfig.model_validate({"resources": {"db://hr": {
            "tables": [{"path": "$.employees", "columns": [{"name": "salary"}]}]
        }}})


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_hook_missing_encryption_key_emits_block(tmp_path, monkeypatch, capsys, host):
    config = tmp_path / "config.yaml"
    config.write_text("storage:\n  backend: sqlite\n  encrypt_at_rest: true\n", encoding="utf-8")
    monkeypatch.delenv("VAULTCOMPUTE_VAULT_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id":"review"}'))
    assert cli.run_hook(["post-tool-use", "--host", host, "--config", str(config)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["continue"] is False
    assert "VaultKeyError" in out["stopReason"]


@pytest.mark.parametrize("paths", [
    ["$.items[*].salary", "$.items[0].salary"],
    ["$.items[0].salary", "$.items[*].salary"],
    ["$.items[*]", "$.items[0].salary"],
    ["$.items[0].children[*]", "$.items[*].children[1].salary"],
])
def test_wildcard_index_overlaps_are_refused(paths):
    with pytest.raises(ValueError, match="overlap"):
        VaultComputeConfig.model_validate({"schemas": {"hr": {
            "sensitive_fields": [{"path": path} for path in paths]
        }}})


@pytest.mark.parametrize("paths", [
    ["$.items[*].name", "$.items[0].salary"],
    ["$.items[0].salary", "$.items[1].salary"],
])
def test_nonoverlapping_index_declarations_remain_valid(paths):
    VaultComputeConfig.model_validate({"schemas": {"hr": {
        "sensitive_fields": [{"path": path} for path in paths]
    }}})


def test_table_field_wildcard_overlap_is_refused():
    with pytest.raises(ValueError, match="overlap"):
        VaultComputeConfig.model_validate({"schemas": {"hr": {
            "tables": [{"path": "$.teams[*].employees", "columns": [{"name": "salary"}]}],
            "sensitive_fields": [{"path": "$.teams[0].employees[0].salary"}],
        }}})


@pytest.mark.parametrize("reverse", [False, True])
def test_resource_wildcards_do_not_retokenize_intersection(reverse):
    fields = [[{"path": "$.items[0].salary"}], [{"path": "$.items[*].salary"}]]
    if reverse:
        fields.reverse()
    state = build_proxy_state(resource_config(*fields))
    msg = {"result": {"contents": [{"uri": "db://hr", "text":
        '{"items": [{"salary": 76543}, {"salary": 12345}]}'}]}}
    _tokenize_resource_read(msg, "db://hr", state)
    rows = json.loads(msg["result"]["contents"][0]["text"])["items"]
    assert [state.store.resolve(row["salary"]) for row in rows] == [76543, 12345]
    assert len(state.store.find_by_session(state.session_id)) == 2


def test_duplicate_resource_path_keeps_stronger_requirement():
    state = build_proxy_state(resource_config(
        [{"path": "$.salary", "required": False}], [{"path": "$.salary"}],
    ))
    msg = {"result": {"contents": [{"uri": "db://hr", "text": '{}'}]}}
    with pytest.raises(ProxyProtectionError, match="missing"):
        _tokenize_resource_read(msg, "db://hr", state)


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_hook_sqlite_open_failure_emits_block(tmp_path, monkeypatch, capsys, host):
    # An existing directory is not a valid SQLite database file.
    config = tmp_path / "config.yaml"
    config.write_text(
        f"storage:\n  backend: sqlite\n  path: {json.dumps(str(tmp_path))}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id":"review"}'))
    assert cli.run_hook(["post-tool-use", "--host", host, "--config", str(config)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["continue"] is False
    assert "OperationalError" in out["stopReason"]
