"""Paid, opt-in compatibility check against an installed Claude Code.

This is deliberately not part of the ordinary test suite: it starts a real
model turn and consumes the user's Claude allowance. It verifies the boundary
the unit tests cannot: the installed host accepts VaultCompute's replacement and
the raw canary is absent from Claude Code's persisted transcript.

Run from the repository root after ``uv tool install . --force``:

    uv run python scripts/verify_claude_code.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from vaultcompute.audit import audit, read_transcript, session_ids_in
from vaultcompute.config import build_token_store, load_config

CANARY = "918273645"


def _find_string(value: Any, key: str) -> str | None:
    if isinstance(value, dict):
        found = value.get(key)
        if isinstance(found, str):
            return found
        for child in value.values():
            nested = _find_string(child, key)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _find_string(child, key)
            if nested is not None:
                return nested
    return None


def _transcript_for(session_id: str) -> Path | None:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None
    named = list(root.rglob(f"*{session_id}*.jsonl"))
    if named:
        return max(named, key=lambda path: path.stat().st_mtime)
    for path in sorted(
        root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if session_id in session_ids_in(path):
            return path
    return None


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one paid VaultCompute compatibility turn in Claude Code."
    )
    parser.add_argument(
        "--expect-version",
        help="fail before the paid turn unless `claude --version` matches exactly",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    claude = shutil.which("claude")
    vaultcompute = shutil.which("vaultcompute")
    if claude is None:
        print("Claude Code is not installed.", file=sys.stderr)
        return 2
    if vaultcompute is None:
        print(
            "`vaultcompute` is not on PATH; run `uv tool install . --force`.",
            file=sys.stderr,
        )
        return 2

    version = subprocess.check_output(
        [claude, "--version"], text=True, encoding="utf-8"
    ).strip()
    if args.expect_version is not None and version != args.expect_version:
        print(
            f"Claude Code version mismatch: expected {args.expect_version!r}, got {version!r}. "
            "No paid test was started.",
            file=sys.stderr,
        )
        return 2

    auth = subprocess.run(
        [claude, "auth", "status"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if auth.returncode != 0 or '"loggedIn": true' not in auth.stdout:
        print(
            "Claude Code is not authenticated; no paid test was started.",
            file=sys.stderr,
        )
        return 2

    repo = Path(__file__).resolve().parents[1]
    plugin = repo / "plugin"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(repo) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )

    with tempfile.TemporaryDirectory(prefix="vaultcompute-claude-live-") as raw_tmp:
        workdir = Path(raw_tmp)
        config_path = workdir / "vaultcompute.yaml"
        vault_path = workdir / "vault.db"
        config_path.write_text(
            "storage:\n"
            "  backend: sqlite\n"
            f"  path: {json.dumps(str(vault_path))}\n"
            "schemas:\n"
            "  mcp__hr__get_salary:\n"
            "    sensitive_fields:\n"
            "      - path: $.salary\n"
            "        semantic_type: compatibility_canary\n",
            encoding="utf-8",
        )
        mcp_path = workdir / "hr-mcp.json"
        mcp_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "hr": {
                            "command": sys.executable,
                            "args": ["-m", "examples.fake_hr_mcp"],
                            "env": {
                                "VAULTCOMPUTE_LIVE_CANARY": CANARY,
                                "PYTHONPATH": str(repo),
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        # The fake server normally returns 71000. The environment override is
        # consumed by its live-test-only branch, keeping the canary unmistakable.
        command = [
            claude,
            "--print",
            "--output-format",
            "json",
            "--plugin-dir",
            str(plugin),
            "--mcp-config",
            str(mcp_path),
            "--allowedTools",
            "mcp__hr__get_salary",
            "--max-turns",
            "3",
            (
                "Call the hr get_salary tool for James Brown exactly once. "
                "Then answer with the returned salary placeholder verbatim."
            ),
        ]
        completed = subprocess.run(
            command,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            print(completed.stderr, file=sys.stderr)
            return completed.returncode
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            print(
                "Claude Code did not return its documented JSON result.",
                file=sys.stderr,
            )
            return 1
        session_id = _find_string(result, "session_id") or _find_string(
            result, "sessionId"
        )
        if session_id is None:
            print(
                "Could not find the Claude session id in the result.", file=sys.stderr
            )
            return 1
        transcript = _transcript_for(session_id)
        if transcript is None:
            print(f"Could not locate the transcript for {session_id}.", file=sys.stderr)
            return 1

        config = load_config(config_path)
        store = build_token_store(config)
        try:
            report = audit(read_transcript(transcript), store, session_id)
        finally:
            close = getattr(store, "close", None)
            if close is not None:
                close()

        print(f"Claude Code version: {version}")
        print(f"Transcript: {transcript}")
        print(report.render())
        if report.records == 0 or report.placeholders_seen == 0 or not report.clean:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
