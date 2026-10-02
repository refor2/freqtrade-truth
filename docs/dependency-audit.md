# Dependency audit

Review date: 2026-10-02.

## Runtime dependency surface

Freqtrade Truth currently declares no third-party runtime dependencies. Runtime HTTP, JSON, HMAC, CLI, datetime, and decimal handling use the Python standard library.

This materially reduces the runtime supply-chain surface but does not eliminate build, development, or CI dependencies.

## Build and development dependencies

The build system uses:

- `setuptools>=75`
- `wheel`

The development extra uses bounded major-version ranges for:

- `build`
- `mypy`
- `pytest`
- `ruff`

These packages are development/build tooling and are not imported by the installed runtime package.

## GitHub Actions

CI uses:

- `actions/checkout`
- `actions/setup-python`

The workflow pins these actions to immutable commit SHAs, with the corresponding major tag retained only as a comment for readability.

Workflow permissions are limited to read-only repository contents.

## Automated checks

CI performs:

- package build,
- installation of the built wheel,
- import smoke testing,
- installed console-script and `python -m` CLI smoke testing,
- `pip check`,
- linting and formatting checks,
- strict mypy,
- Python 3.11–3.14 tests,
- public-safety scanning,
- commit-metadata privacy scanning.

Dependabot is configured for both pip and GitHub Actions on a monthly cadence.

## Residual risk

This project is a library rather than a locked application environment, so development/build dependency ranges are not represented by a committed full transitive lockfile. A compromised package index, compromised accepted tool release, or compromised build environment remains a supply-chain risk.

Release qualification should therefore record the exact source commit and CI result used to produce the release. Package-index publishing, provenance attestation, or a stricter reproducible-build scheme should be introduced only as separately reviewed release-engineering changes.

## Conclusion

For the current pre-alpha scope:

- runtime third-party dependency count: zero,
- CI Actions: immutable-SHA pinned,
- dependency update automation: enabled,
- dependency consistency check: enabled,
- known residual build/development supply-chain risk: documented.

This is a dependency review, not a claim of formal software supply-chain certification.
