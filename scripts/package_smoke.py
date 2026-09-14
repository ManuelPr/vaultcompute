"""Run inside an installed artifact's isolated environment, outside the repo."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import secrets
import sqlite3
import sys
import sysconfig
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import vaultcompute
from vaultcompute import ProtectionError, TableQueryCapability, VaultComputeSession
from vaultcompute.config import VaultComputeConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--encryption", action="store_true")
    args = parser.parse_args()

    installed = Path(vaultcompute.__file__).resolve()
    assert installed.is_relative_to(Path(sysconfig.get_paths()["purelib"]).resolve()), installed
    assert importlib.metadata.version("vaultcompute") == args.version
    assert (installed.parent / "py.typed").is_file()
    for name in vaultcompute.__all__:
        assert getattr(vaultcompute, name) is not None

    config = VaultComputeConfig.model_validate({"schemas": {"employees": {
        "tables": [{"path": "$.rows", "columns": [{"name": "name"}, {"name": "salary"}]}]
    }}})
    session = VaultComputeSession(config, session_id="artifact-check")

    async def tool():
        return {"rows": [{"name": "Synthetic-A", "salary": 12345},
                         {"name": "Synthetic-B", "salary": 98765}]}

    protected = asyncio.run(session.call_protected_tool_async("employees", tool))
    encoded = json.dumps(protected)
    assert all(value not in encoded for value in ("Synthetic-A", "Synthetic-B", "12345", "98765"))
    ops = [{"op": "sort_by", "column": "salary", "desc": True}, {"op": "limit", "n": 1}]
    cap = TableQueryCapability.issue(
        session_id=session.session_id, table_token=protected["rows"], ops=ops,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    token = session.execute_authorized_query({"table": protected["rows"], "ops": ops}, capability=cap)
    assert json.loads(session.render_final_answer(token)) == [{"name": "Synthetic-B", "salary": 98765}]
    other = VaultComputeSession(config, session_id="other", store=session.store)
    assert other.render_final_answer(token) == "[redacted]"
    try:
        session.protect_tool_result("employees", {"unexpected": "synthetic"})
    except ProtectionError:
        pass
    else:
        raise AssertionError("required-path drift was not blocked")
    try:
        session.execute_authorized_query({"table": protected["rows"], "ops": []}, capability=cap)
    except ValueError:
        pass
    else:
        raise AssertionError("modified query was not refused")

    if args.encryption:
        from vaultcompute.core.sqlite_store import SQLiteTokenStore

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "check.db"
            key = secrets.token_bytes(32)
            store = SQLiteTokenStore(database, encrypt=True, key=key)
            encrypted = VaultComputeSession(config, session_id="encrypted", store=store)
            protected = encrypted.protect_tool_result("employees", {"rows": [{"name": "PRIVATE-SYNTHETIC-MARKER", "salary": 12}]})
            store.close()
            with closing(sqlite3.connect(database)) as connection:
                stored = connection.execute("SELECT value FROM records").fetchone()[0]
                assert "PRIVATE-SYNTHETIC-MARKER" not in stored
            reopened = SQLiteTokenStore(database, encrypt=True, key=key)
            try:
                display = VaultComputeSession(config, session_id="encrypted", store=reopened)
                assert "PRIVATE-SYNTHETIC-MARKER" in display.render_final_answer(protected["rows"])
            finally:
                reopened.close()
    print(f"Installed package {args.version}: API, query authorization, session isolation"
          + (", encrypted SQLite restart" if args.encryption else "") + " passed.")


if __name__ == "__main__":
    main()
