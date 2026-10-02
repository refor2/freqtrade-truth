# Contributing

Thank you for helping improve Freqtrade Truth.

## Core rule

This is a public repository. Contributions must be safe for unrestricted public disclosure.

Before opening a pull request, read [SECURITY.md](SECURITY.md).

## Development workflow

1. Create a focused branch.
2. Keep changes small and reviewable.
3. Add or update tests.
4. Run:
   ```bash
   python scripts/public_safety_check.py
   python scripts/commit_metadata_check.py
   ruff check .
   pytest
   ```
5. Review the complete diff.
6. Confirm protected project identities use a GitHub noreply commit address.
7. Open a pull request and complete the public-safety checklist.

## Public data only

Tests, documentation, examples, screenshots, and fixtures must use synthetic data.

Do not submit:

- credentials or secret-like values,
- real account, order, trade, balance, or position data,
- production endpoints or infrastructure information,
- private logs or exports,
- proprietary strategy, signal, ranking, admission, correlation, or risk logic,
- code copied from private repositories unless it has been independently cleared for public release.

When in doubt, leave the material out.

## Design principles

- Read-only by default.
- Small interfaces and explicit data models.
- Deterministic reconciliation.
- Clear provenance for reported values.
- Fail closed when required financial data is missing or ambiguous.
- No hidden order-placement capability.
- Tests must not require live exchange credentials.

## Pull requests

A pull request should explain:

- what changed,
- why it is needed,
- how it was tested,
- whether it affects the public data model or adapter contracts.

Large architectural changes should be discussed in an issue first.
