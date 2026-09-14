# Evidence for the next version

The first alpha is a small, reviewable release of the existing Mode B boundary,
with optional proxy and experimental host adapters. Release readiness and
feature completeness are separate decisions.

| Stage | Evidence needed to advance |
|---|---|
| Alpha candidate | Source tests and clean wheel/sdist installs pass; package contents, license and metadata checked; example runnable; limits documented |
| Public alpha | Candidate verified on TestPyPI and PyPI; exact tag and artifacts recorded; private reporting channel available |
| Next alpha | Findings from 2–3 external evaluations triaged; confirmed privacy/installation defects fixed with regressions; remaining limitations documented |
| Beta for a specific integration | Repeatable real use cases; stable boundary and error behavior; host compatibility evidence where applicable; independent review of that integration's privacy boundary |
| 1.0 for the core/library | Defined API compatibility and upgrade policy; independent security findings addressed or explicitly scoped out; repeatable releases and maintenance capacity demonstrated |

Maturity belongs to each integration. A stable library would not automatically
make a host adapter stable. External pilot runs and independent reviews have
not been completed just because internal tests pass.

## Prioritization

1. Fix confirmed disclosure paths, incorrect authorization and broken installs.
2. Remove repeated integration friction observed in the pilot.
3. Add an operation only when a concrete task cannot be completed with the
   existing query language. Define its authorization and leakage implications
   before implementation.
4. Add a storage backend or transport when a real deployment needs it and can
   exercise the shared conformance checks.

Joins, group-by, HTTP transport and additional storage backends remain
candidates, not commitments. Arbitrary Python is outside the controlled
profile; a stronger sandbox would not by itself remove its result-dependent
success/failure channel.

For each proposed change, record the user task, reproduction, expected outcome,
security boundary affected, smallest useful implementation and validation.
Keep completed work in the changelog rather than accumulating a second history
in the README. Use [pilot.md](pilot.md) for observations and [release.md](release.md)
for shipping a version.
