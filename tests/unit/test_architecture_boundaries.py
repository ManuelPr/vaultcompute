"""Keep the security kernel independent from every integration surface."""

import ast
from pathlib import Path


CORE = Path(__file__).parents[2] / "src" / "vaultcompute" / "core"
BANNED = (
    "vaultcompute.audit",
    "vaultcompute.cli",
    "vaultcompute.config",
    "vaultcompute.hooks",
    "vaultcompute.hosts",
    "vaultcompute.mcp_server",
    "vaultcompute.proxy",
    "vaultcompute.sandbox",
    "vaultcompute.session",
    "vaultcompute.tools",
)


def test_core_does_not_import_integration_surfaces():
    violations = []
    for path in CORE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        imports += [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        for imported in imports:
            if imported.startswith(BANNED):
                violations.append(f"{path.name}: {imported}")

    assert not violations, "core depends on integration code: " + ", ".join(violations)
