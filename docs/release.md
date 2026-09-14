# Releasing VaultCompute

## First alpha scope

`0.1.0a1` is the first public alpha candidate. The product to evaluate is the
Mode B library: schema-driven protection of structured results, exact authorized
table queries and final rendering under application control. Memory and SQLite
storage, optional encryption and the CLI are included.

Mode A remains a beta proxy and Modes C/D are experimental adapters. The Python
wheel contains the package and CLI; plugin assets and examples are provided in
the source archive and repository. The plugin manifests have independent
versions. This release does not claim production certification, coverage of
all host tools, arbitrary-code safety or multi-user API authorization.

## Validate locally

From a checkout with `uv` installed:

```bash
uv sync --locked --all-extras --dev
uv run pytest
uv build --no-sources --out-dir dist/release
uv run python scripts/verify_package.py dist/release --install
uvx --from 'twine>=6.1,<7' twine check --strict dist/release/*
```

Use a fresh output directory for each candidate. The verifier rejects stale
versions and unexpected files; do not upload the existing `dist/` directory
with a wildcard. The verified upload directory is `dist/release/`.

The checks inspect wheel/sdist contents and metadata, compare package source
with the checkout, verify the MIT license and type marker, then install each
artifact in a fresh environment outside the repository. They exercise the CLI,
public API, the README demo, refusal of changed queries, session isolation and
encrypted SQLite persistence. Each run prints SHA-256 digests of both files.

CI repeats source tests and clean artifact installs on Linux, macOS and Windows
with Python 3.11, 3.12 and 3.13. The release workflow runs those gates again on
the selected version tag. A green source test run alone is not a release check.

## One-time account setup

The maintainer must create and verify accounts on both
[PyPI](https://pypi.org/account/register/) and
[TestPyPI](https://test.pypi.org/account/register/), with the authentication
required by each service. These are separate registries. Do not share account
passwords, recovery codes or tokens in issues or chat.

Add a pending GitHub Trusted Publisher under each account's **Publishing** page:

| Field | TestPyPI | PyPI |
|---|---|---|
| Project name | `vaultcompute` | `vaultcompute` |
| Owner | `ManuelPr` | `ManuelPr` |
| Repository | `vaultcompute` | `vaultcompute` |
| Workflow filename | `release.yml` | `release.yml` |
| Environment | `testpypi` | `pypi` |

The pages are [TestPyPI publishing](https://test.pypi.org/manage/account/publishing/)
and [PyPI publishing](https://pypi.org/manage/account/publishing/).
A pending publisher does not reserve a name. At preparation time neither
registry returned a public `vaultcompute` project; recheck immediately before
the first upload. See the [PyPI pending-publisher guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

The repository uses GitHub environments named `testpypi` and `pypi`. Restrict
deployment to version tags and put a required maintainer review on `pypi` where
available. The release workflow is manual and only the publication jobs receive
OIDC permission; it does not require a persistent PyPI API token.

## Tag, stage, then publish

1. Merge the release preparation only after both CI workflows pass. Check that
   the version in `pyproject.toml`, lockfile and release notes agree. Finalize
   the changelog date before building the version tag.
2. Tag that exact commit, for example `v0.1.0a1`, and push the tag. Do not move
   a published tag; use a new alpha version for a changed artifact.
3. Run the **Release** workflow on the tag, choosing `testpypi`:

   ```bash
   gh workflow run release.yml --ref v0.1.0a1 -f registry=testpypi
   ```

4. Wait for the complete workflow, including `check-testpypi`. It downloads
   the registry artifacts, verifies their hashes and repeats clean installs.
   Dependencies are fetched from normal PyPI; only VaultCompute comes from
   TestPyPI. No combined package-index search is used.
5. Run the same workflow on the same tag, choosing `pypi`:

   ```bash
   gh workflow run release.yml --ref v0.1.0a1 -f registry=pypi
   ```

   This rebuilds and validates the tagged source, then requires an identical,
   non-yanked wheel and sdist on TestPyPI before publishing. If they differ,
   stop and prepare a new version; do not bypass the hash check.
6. Confirm the PyPI page, download hashes and installation of the exact version
   in another clean environment. Then publish the GitHub prerelease with the
   tag, notes, verified wheel and sdist. The draft is not a public release.

Before first publication, install the verified local wheel for evaluation:

```bash
uv venv .venv-alpha
uv pip install --python .venv-alpha dist/release/vaultcompute-0.1.0a1-py3-none-any.whl
```

After successful PyPI publication, the equivalent exact-version install is:

```bash
python -m pip install 'vaultcompute==0.1.0a1'
```

These commands are sequential release instructions, not a statement that an
upload has already occurred. Account setup, real pilot feedback and independent
security review cannot be replaced by repository tests.

## Evidence to retain

Keep the commit/tag, CI run links, distribution hashes, registry URLs, installed
Python/platform versions and any real-host compatibility records with the
release. Record pilot findings using [pilot.md](pilot.md) and decide the next
version using [roadmap.md](roadmap.md).
