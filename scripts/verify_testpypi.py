"""Require identical distributions on TestPyPI before publishing to PyPI."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import urlopen

from verify_package import check_artifacts, install_checks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    wheel, sdist, version = check_artifacts(args.directory.resolve())
    endpoint = f"https://test.pypi.org/pypi/vaultcompute/{version}/json"
    for attempt in range(6):
        try:
            with urlopen(endpoint, timeout=30) as response:
                release = json.load(response)
            break
        except HTTPError as error:
            if error.code != 404 or attempt == 5:
                raise
            time.sleep(5)
    published = {item["filename"]: item for item in release["urls"]}
    assert set(published) == {wheel.name, sdist.name}, "TestPyPI contains a different artifact set"
    uv = shutil.which("uv")
    if args.install and uv is None:
        parser.error("uv must be on PATH")
    for artifact in (wheel, sdist):
        item = published[artifact.name]
        assert not item.get("yanked"), "TestPyPI artifact is yanked"
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert item["digests"]["sha256"] == digest, "TestPyPI artifact differs; use a new version"
        if args.install:
            url = item["url"]
            parsed = urlparse(url)
            assert parsed.scheme == "https" and parsed.hostname in {
                "test-files.pythonhosted.org", "files.pythonhosted.org",
            }, "unexpected package download origin"
            with tempfile.TemporaryDirectory(prefix="vaultcompute-testpypi-") as directory:
                with urlopen(url, timeout=60) as response:
                    contents = response.read()
                assert hashlib.sha256(contents).hexdigest() == digest
                downloaded = Path(directory) / artifact.name
                downloaded.write_bytes(contents)
                # Dependencies come from normal PyPI; only our exact release
                # file comes from TestPyPI. No mixed-index dependency lookup.
                install_checks(downloaded, version, uv)
    print(f"TestPyPI {version}: identical wheel and sdist verified.")


if __name__ == "__main__":
    main()
