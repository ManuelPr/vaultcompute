"""Static checks that keep the shipped host plugins aligned with the CLI."""

from __future__ import annotations

import json
from pathlib import Path

from vaultcompute import hooks
from vaultcompute.hosts import CLAUDE_CODE, CODEX

ROOT = Path(__file__).resolve().parents[2]


def _commands(path: Path) -> dict[str, list[str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        event: [handler["command"] for group in groups for handler in group["hooks"]]
        for event, groups in document["hooks"].items()
    }


def test_claude_plugin_declares_every_supported_event():
    commands = _commands(ROOT / "plugin" / "hooks" / "hooks.json")

    assert set(commands) == {
        "SessionStart",
        "PreToolUse",
        "PostToolUse",
        "MessageDisplay",
    }
    assert commands["PreToolUse"] == [f"vaultcompute hook {hooks.PRE_TOOL_USE}"]
    assert commands["PostToolUse"] == [f"vaultcompute hook {hooks.POST_TOOL_USE}"]
    assert hooks.events_for(CLAUDE_CODE) == (
        hooks.PRE_TOOL_USE,
        hooks.POST_TOOL_USE,
        hooks.MESSAGE_DISPLAY,
        hooks.SESSION_START,
    )


def test_codex_plugin_declares_only_supported_events_and_selects_its_adapter():
    commands = _commands(ROOT / "plugins" / "vaultcompute-codex" / "hooks" / "hooks.json")

    assert set(commands) == {"SessionStart", "PreToolUse", "PostToolUse"}
    assert all(
        command.endswith("--host codex")
        for event_commands in commands.values()
        for command in event_commands
    )
    assert hooks.events_for(CODEX) == (
        hooks.PRE_TOOL_USE,
        hooks.POST_TOOL_USE,
        hooks.SESSION_START,
    )


def test_codex_plugin_exposes_vault_compute_server():
    manifest = json.loads(
        (
            ROOT / "plugins" / "vaultcompute-codex" / ".codex-plugin" / "plugin.json"
        ).read_text(encoding="utf-8")
    )
    mcp = json.loads(
        (ROOT / "plugins" / "vaultcompute-codex" / ".mcp.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "vaultcompute-codex"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert mcp["mcpServers"]["vaultcompute"]["command"] == "vaultcompute"
    assert mcp["mcpServers"]["vaultcompute"]["args"] == ["mcp-server"]
