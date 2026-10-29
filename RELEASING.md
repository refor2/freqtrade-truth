# Release process

Releases are cut from a reviewed public `main` commit with verified CI,
public-safety checks, an installed wheel and an explicit release scope.

**v0.1.0 is a candidate-only financial evidence audit, not certified account
settlement.** Its explicit, intentionally limited contract is documented in
[docs/v0.1-candidate-contract.md](docs/v0.1-candidate-contract.md).
No real account, historical USD conversion, funding ownership or economic
cross-source identity is claimed. Account-level proof belongs to a separately
reviewed future milestone (issue #15).

Main branch protection/rulesets (issue #18) remain recommended defense in depth,
not a runtime dependency, a need for customer keys, or a substitute for exact
prepublication review. Where branch settings cannot be verified, record that
governance limitation explicitly instead of claiming that main is protected.
No API credentials or private operator data go to GitHub or release assets.

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

## Automated v0.1.0 publication

The existing GitHub CI workflow includes a **main-push-only** release job,
conditioned on public safety, static quality, package integrity and the entire
Python test matrix being green. It retrieves artifacts from the same CI run,
rechecks version and artifact integrity, and publishes the exact main commit
as `v0.1.0`. GitHub's ephemeral workflow identity is restricted to that
release job (`contents:write`, `actions:read`); no operator credentials
are stored in GitHub or CI. PRs and forks cannot invoke the release job.
Existing tags/releases are not overwritten. This does not enable GitHub
branch protection or imply account-level settlement certification.

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

The initial v0.1.0 scope is a GitHub release only.

Publishing to a package index should be introduced later as a separate reviewed supply-chain change.
