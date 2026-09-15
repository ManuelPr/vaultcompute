"""Check release contents and optionally install both artifacts outside the repo.

Requires uv on PATH. Does not publish, alter the project environment, or reuse
an editable install. Temporary environments are removed when the checks finish.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]


def same_contents(actual: bytes, expected: bytes, name: str) -> bool:
    """Git may check out text as CRLF on Windows; compare its text content.

    Distribution SHA-256 checks remain byte-for-byte. Binary files are also
    compared exactly, so newline normalization cannot hide a binary change.
    """
    path = PurePosixPath(name)
    if path.suffix in {".py", ".md", ".toml", ".yaml", ".yml", ".json", ".lock", ".typed"} or path.name in {"LICENSE", ".gitignore"}:
        return actual.replace(b"\r\n", b"\n") == expected.replace(b"\r\n", b"\n")
    return actual == expected


def metadata_checks(raw: bytes, version: str) -> None:
    metadata = email.parser.BytesParser().parsebytes(raw)
    assert metadata["Metadata-Version"] == "2.4"
    assert metadata["Name"] == "vaultcompute"
    assert metadata["Version"] == version
    assert metadata["License-Expression"] == "MIT"
    assert metadata["License-File"] == "LICENSE"
    assert metadata["Requires-Python"] == ">=3.11"
    assert metadata["Description-Content-Type"] == "text/markdown"
    assert {"demo", "encryption"} <= set(metadata.get_all("Provides-Extra", []))
    description = raw.decode("utf-8").replace("\r\n", "\n").split("\n\n", 1)[1]
    assert description.strip() == (ROOT / "README.md").read_text(encoding="utf-8").strip(), "stale README in metadata"


def check_artifacts(directory: Path) -> tuple[Path, Path, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    wheel = directory / f"vaultcompute-{version}-py3-none-any.whl"
    sdist = directory / f"vaultcompute-{version}.tar.gz"
    files = {path for path in directory.iterdir() if path.name != ".gitignore"}
    assert files == {wheel, sdist}, "use a directory containing only this version's wheel and sdist"
    license_bytes = (ROOT / "LICENSE").read_bytes()
    source_files = {
        p.relative_to(ROOT / "src").as_posix(): p.read_bytes()
        for p in (ROOT / "src/vaultcompute").rglob("*")
        if p.is_file() and (p.suffix == ".py" or p.name == "py.typed")
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        info = f"vaultcompute-{version}.dist-info/"
        expected = set(source_files) | {info + name for name in (
            "METADATA", "WHEEL", "RECORD", "entry_points.txt", "licenses/LICENSE",
        )}
        assert names == expected, f"unexpected wheel contents: {names ^ expected}"
        for name, contents in source_files.items():
            assert same_contents(archive.read(name), contents, name), f"stale source in wheel: {name}"
        metadata_checks(archive.read(info + "METADATA"), version)
        assert same_contents(archive.read(info + "licenses/LICENSE"), license_bytes, "LICENSE")
        assert b"vaultcompute = vaultcompute.cli:main" in archive.read(info + "entry_points.txt")

    allowed_dirs = {"src", "tests", "examples", "scripts", "docs", "plugin", "plugins"}
    allowed_files = {".gitignore", "README.md", "CHANGELOG.md", "LICENSE", "SECURITY.md", "CONTRIBUTING.md",
                     "pyproject.toml", "uv.lock", "vaultcompute.example.yaml", "PKG-INFO"}
    plugin_configs = {"plugin/.mcp.json", "plugins/vaultcompute-codex/.mcp.json"}
    prefix = f"vaultcompute-{version}/"
    with tarfile.open(sdist, "r:gz") as archive:
        members = {member.name.removeprefix(prefix): member for member in archive.getmembers()}
        for name, member in members.items():
            path = PurePosixPath(name)
            assert member.name.startswith(prefix) and member.isfile(), member.name
            assert ".." not in path.parts and not path.is_absolute(), name
            assert path.parts[0] in allowed_dirs or name in allowed_files, name
            assert not name.startswith("docs/archive/"), name
            assert not any(part in {"__pycache__", ".venv", ".git", ".claude"} for part in path.parts), name
            assert not (path.name.startswith(".env") or path.name.endswith((".db", ".db-wal", ".db-shm", ".pyc"))), name
            assert path.name != ".mcp.json" or name in plugin_configs, name
            local = ROOT / name
            if name != "PKG-INFO" and local.is_file():
                assert same_contents(archive.extractfile(member).read(), local.read_bytes(), name), f"stale sdist file: {name}"
        for name in allowed_files | plugin_configs | {"examples/quickstart.py", "scripts/package_smoke.py", "scripts/verify_package.py"}:
            assert name in members, f"missing sdist file: {name}"
        for name, contents in source_files.items():
            assert same_contents(archive.extractfile(members["src/" + name]).read(), contents, name), name
        metadata_checks(archive.extractfile(members["PKG-INFO"]).read(), version)
        assert same_contents(archive.extractfile(members["LICENSE"]).read(), license_bytes, "LICENSE")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    example = re.search(r"```python\n(.*?)\n```", readme, re.S)
    assert example and example.group(1).strip() == (ROOT / "examples/quickstart.py").read_text(encoding="utf-8").strip(), "README and runnable example differ"
    for artifact in (wheel, sdist):
        print(f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}", flush=True)
    print("Artifact contents, MIT license, metadata and source consistency passed.", flush=True)
    return wheel, sdist, version


def install_checks(artifact: Path, version: str, uv: str) -> None:
    with tempfile.TemporaryDirectory(prefix="vaultcompute-package-") as directory:
        work = Path(directory)
        environment = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON", "UV_PUBLISH_TOKEN", "VAULTCOMPUTE_VAULT_KEY"):
            environment.pop(key, None)
        environment["PYTHONIOENCODING"] = "utf-8"

        def run(*command: str) -> subprocess.CompletedProcess:
            return subprocess.run(command, cwd=work, env=environment, check=True)

        venv = work / "venv"
        run(uv, "venv", "--python", sys.executable, str(venv))
        bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
        python = str(bin_dir / ("python.exe" if os.name == "nt" else "python"))
        cli = str(bin_dir / ("vaultcompute.exe" if os.name == "nt" else "vaultcompute"))
        shutil.copyfile(ROOT / "scripts/package_smoke.py", work / "smoke.py")
        shutil.copyfile(ROOT / "examples/quickstart.py", work / "quickstart.py")
        run(uv, "pip", "install", "--python", python, str(artifact))
        run(python, "-I", "smoke.py", "--version", version)
        run(cli, "--help")
        result = subprocess.run([python, "-I", "quickstart.py"], cwd=work, env=environment,
                                check=True, capture_output=True, text=True, encoding="utf-8")
        lines = result.stdout.splitlines()
        assert len(lines) == 3
        assert all(secret not in "\n".join(lines[:2]) for secret in ("John", "James", "62000", "71000"))
        assert lines[-1] == 'User sees: Highest-paid employee: [{"name": "James", "salary": 71000}]'
        print("Installed quickstart output and model-visible privacy checks passed.", flush=True)
        run(uv, "pip", "install", "--python", python, str(artifact) + "[encryption]")
        run(python, "-I", "smoke.py", "--version", version, "--encryption")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--install", action="store_true", help="also test clean wheel and sdist installs")
    args = parser.parse_args()
    wheel, sdist, version = check_artifacts(args.directory.resolve())
    if args.install:
        uv = shutil.which("uv")
        if uv is None:
            parser.error("uv must be on PATH for installation checks")
        for artifact in (wheel, sdist):
            print(f"Checking clean install: {artifact.name}", flush=True)
            install_checks(artifact, version, uv)


if __name__ == "__main__":
    main()
