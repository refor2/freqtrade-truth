# Release process

Releases are cut from the protected public `main` branch after all required checks pass.

**0.0.2 pre-alpha candidate:** the unified Bybit source evidence, offline demo and
CCXT dated settlement corrections can be built and tested independently. A
successful CI run or a new package version **does not mean a GitHub tag or release
exists**. First verify GitHub's `main` protection (issue #18) and source
identity limitations (issue #15), and then cut the release. Numerical `MATCH`
remains preview-only, never verified economic ownership.


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
7. Run the release-artifact integrity gate:
   ```bash
   python scripts/release_integrity_check.py
   ```
8. Verify the wheel can be installed and imported in a clean environment.
9. Review release notes for private or sensitive information.

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
