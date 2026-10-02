# Release process

Releases are cut from the protected public `main` branch after all required checks pass.

## Before tagging

1. Confirm the target commit is on `main`.
2. Confirm GitHub CI is green.
3. Run the public-safety review on the complete release diff.
4. Update `CHANGELOG.md`.
5. Verify the package version in `pyproject.toml` and `freqtrade_truth.__version__` match.
6. Build locally or in CI:
   ```bash
   python -m build
   ```
7. Verify the wheel can be installed and imported in a clean environment.
8. Review release notes for private or sensitive information.

## Tagging

Tags use the format:

```text
vMAJOR.MINOR.PATCH
```

Example:

```text
v0.0.1
```

## GitHub release

The GitHub release should contain:

- a concise public summary,
- compatibility notes,
- security-relevant changes,
- known limitations,
- a link to the changelog.

Do not attach private datasets, logs, credentials, or environment-specific configuration.

## Package publishing

The 0.0.1 preview is a GitHub release only.

Publishing to a package index should be introduced later as a separate reviewed supply-chain change.
