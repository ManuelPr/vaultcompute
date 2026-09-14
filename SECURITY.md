# Security policy

VaultCompute protects declared structured tool results. Its alpha status is
not a claim of independent security certification. See
[LIMITATIONS.md](LIMITATIONS.md) for the supported boundary and known risks.

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/ManuelPr/vaultcompute/security/advisories/new).
Do not open a public issue containing private data, vault keys or an exploit
that exposes someone else's information. Reports should use synthetic data.

Include the package version, operating system, Python version, integration
mode, host version where relevant, and the smallest configuration and steps
that reproduce the issue. Describe what the model received and what you
expected to remain private. Do not attach a real vault or production transcript.

For installation problems without a security impact, use a normal issue.

## Supported versions

During the alpha series, fixes target the latest alpha on `main`. Older alphas
do not receive separate backports; upgrade and repeat your integration checks.
No stable release or long-term support commitment exists yet. Host adapters
remain experimental and must be checked against the installed host version.

The maintainer will investigate privately and coordinate disclosure when a
report is confirmed. This volunteer project does not promise a response-time
SLA or a paid bug bounty.
