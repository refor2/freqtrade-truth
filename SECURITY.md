# Security Policy

## Public-source-only rule

This repository is intentionally public. Every contribution must be safe for unrestricted public disclosure.

Do **not** commit, paste, reference, or derive content from confidential or private sources unless that material has first been independently rewritten and verified as safe for public release.

The following are prohibited:

- API keys, access tokens, passwords, passphrases, cookies, session identifiers, private keys, certificates, or credentials of any kind.
- Production IP addresses, hostnames, internal URLs, account identifiers, subaccount identifiers, exchange account details, or infrastructure topology.
- Real order IDs, trade IDs, position history, account balances, private logs, private alerts, private screenshots, or production exports.
- Proprietary trading strategies, signals, ranking logic, risk models, private parameters, unpublished research, or business-sensitive logic.
- Source code, snippets, comments, documentation, fixtures, or configuration copied from a private repository unless independently cleared for public release.
- Personal data, private conversations, customer data, private documents, or any other non-public information.

Use synthetic data only in tests, examples, screenshots, and documentation.

The technical threat model is documented in [docs/threat-model.md](docs/threat-model.md).

## Mandatory pre-publication gate

Every change must pass all of the following checks before merge:

1. **Source classification** — confirm that every added line is either newly authored for this public project or comes from a clearly public source with compatible licensing.
2. **Secret scan** — run the repository safety scanner and verify there are no credentials or secret-like values.
3. **Data sanitization** — verify examples and fixtures are synthetic and contain no real identifiers, trades, balances, endpoints, or logs.
4. **Private-IP / infrastructure review** — verify there are no production network details or deployment-specific values.
5. **Strategy/IP review** — verify no private strategy, model, ranking, risk, admission, correlation, or proprietary research logic is present.
6. **Commit metadata privacy** — protected project identities must use GitHub noreply addresses in reachable commit history.
7. **Diff review** — inspect the complete PR diff before merge.
8. **Fail closed** — if there is uncertainty about whether information is safe to publish, do not publish it.

## Local secrets

Local credentials may only live outside Git tracking, for example in ignored environment files. The public `.env.example` must contain placeholders or blank values only.

Never add real secrets to issues, pull requests, commit messages, Actions logs, documentation, or test fixtures.

Authenticated HTTP connections are permitted only over HTTPS or loopback HTTP. Redirects are not followed by the built-in transport, and credentials must never be embedded in URLs.

## Accidental disclosure

If a secret or confidential value is ever committed:

1. Revoke or rotate it immediately.
2. Treat it as compromised even if the commit is later removed.
3. Remove the material from the repository history where appropriate.
4. Review adjacent commits and logs for related exposure.

## Security reports

Please report vulnerabilities through GitHub's private security reporting feature when available. Do not open a public issue containing exploit details or sensitive information.
