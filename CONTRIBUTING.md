# Contributing

Start with a small, reproducible integration case. Use synthetic data and
include the Python/package versions and integration mode; host reports also
need the host version. Security issues belong in the private channel described
in [SECURITY.md](SECURITY.md).

## Local checks

```bash
uv sync --locked --all-extras --dev
uv run pytest
uv build --no-sources --out-dir dist/release
uv run python scripts/verify_package.py dist/release --install
```

Use a fresh output directory when preparing a new version. The package verifier
rejects mixed or stale release artifacts. It installs the wheel and source
archive into temporary environments outside the checkout, with `PYTHONPATH`
removed, and exercises the public API, CLI and encrypted storage.

Changes to a privacy boundary need a failing regression case before the fix,
then both the focused tests and the full suite. Avoid tests that merely repeat
implementation details. Update the changelog and the supported-shape contract
when behavior changes. Real-host compatibility requires an actual host run;
fixtures are only a regression check.

For new features, explain the user task and why the current operations cannot
complete it. Preserve the separation between the core and host/transport
adapters, and keep raw values out of model-visible errors.

Release preparation and the pilot protocol live in [docs/release.md](docs/release.md)
and [docs/pilot.md](docs/pilot.md). Code is distributed under the repository's
[MIT license](LICENSE).
