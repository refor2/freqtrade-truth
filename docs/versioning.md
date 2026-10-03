# Versioning

Freqtrade Truth follows semantic-versioning principles with explicit pre-1.0 expectations.

## 0.0.x

The 0.0.x line is pre-alpha.

It is used to validate:

- public domain contracts,
- adapter boundaries,
- security invariants,
- packaging,
- community feedback.

Breaking API changes are possible and must be documented.

Preview reconciliation statuses, reason-code names, attribution contracts, and reporting schemas
are not v0.1-stable until the explicit contract-stabilization gate is complete. Consumers should
treat them as provisional and avoid persisting assumptions that cannot be migrated.

## 0.x

Once the first complete reconciliation path exists, minor 0.x releases should keep public contracts increasingly stable.

Breaking changes require:

- a changelog entry,
- migration notes when practical,
- explicit release notes.

## 1.0

Version 1.0 requires a stable public reconciliation contract and a documented compatibility policy.

## Release integrity

Every release candidate must pass:

1. public-safety scan,
2. Ruff lint and formatting,
3. strict Mypy,
4. tests across the supported Python matrix,
5. dependency consistency check,
6. wheel and source-distribution build,
7. install/import smoke test,
8. final public diff review.

A failed gate blocks release.
