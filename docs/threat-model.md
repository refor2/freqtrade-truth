# Threat model

## Scope

Freqtrade Truth is a read-only financial reconciliation project.

The current trust boundary covers:

1. operator-provided connection configuration,
2. the local transport,
3. Freqtrade or exchange read APIs,
4. normalization adapters,
5. future reconciliation and reporting layers.

The project assumes upstream APIs may return malformed, incomplete, stale, or adversarial data.

## Security objectives

The project must preserve:

- confidentiality of credentials,
- integrity of normalized financial evidence,
- deterministic handling of financial values,
- read-only behavior toward trading accounts,
- clear separation between public source code and private operator data.

## Threats and mitigations

### Credential disclosure

Threats:

- credentials embedded in URLs,
- credentials included in logs or exception messages,
- accidental publication in fixtures or documentation,
- credentials sent over plaintext remote HTTP,
- credentials forwarded during redirects.

Mitigations:

- credentials are passed separately from the base URL,
- the built-in transport does not include credentials in exception messages,
- public-safety CI scans tracked files for secret-like values,
- authenticated non-loopback connections require HTTPS,
- HTTP redirects are rejected rather than followed,
- tests use placeholders only.

### Account mutation

Threat:

A reconciliation tool accidentally gains the ability to place/cancel orders, change leverage, transfer funds, or mutate account state.

Mitigations:

- adapter contracts expose read methods only,
- the built-in transport exposes GET only,
- mutation operations are permanent project non-goals,
- code review should reject account-mutation capabilities.

### Malformed or ambiguous financial data

Threat:

Missing or malformed upstream values are silently treated as valid numbers and create false reconciliation matches.

Mitigations:

- monetary values use Decimal,
- missing values remain None,
- normalized timestamps must be timezone-aware UTC,
- malformed required fields fail closed,
- non-finite numeric values are rejected,
- adapters declare their supported financial fields explicitly.

### Rate limiting and transient upstream failures

Threats:

- repeated transient failures cause tight retry loops,
- a hostile or misconfigured upstream server requests an excessive Retry-After delay,
- retries amplify non-transient authentication or validation failures.

Mitigations:

- retry attempts are strictly bounded,
- only explicitly configured transient status codes are retried,
- Retry-After is honored but capped by a local maximum delay,
- non-configured 4xx/5xx failures fail immediately,
- retry behavior is implemented at the transport layer and covered by offline tests.

### Unexpected or oversized responses

Threats:

- non-JSON responses,
- invalid text encoding,
- unexpectedly large payloads,
- redirect-based behavior changes.

Mitigations:

- responses must be UTF-8 JSON objects,
- response size is bounded,
- non-2xx responses fail closed,
- redirects are not followed.

### Server-side request forgery in future services

Current state:

The base URL is operator-controlled configuration in a local library context.

Future risk:

If a web/API layer ever accepts arbitrary source URLs from untrusted users, that input could become an SSRF vector.

Requirement:

A future remotely accessible service must use an explicit allowlist or preconfigured connector registry. It must not pass arbitrary user-provided URLs into the transport.

### Sensitive data in diagnostics

Threat:

Real trades, balances, account identifiers, infrastructure details, or API responses are copied into issues, test fixtures, screenshots, or CI output.

Mitigations:

- synthetic-only public fixtures,
- publication checklist on pull requests,
- public-safety scanner,
- issue templates explicitly prohibit sensitive submissions,
- fail closed when release safety is uncertain.

### Supply-chain compromise

Threats:

- compromised development dependencies,
- malicious dependency updates,
- compromised GitHub Actions.

Mitigations:

- runtime code is currently standard-library-first,
- dependencies are minimal,
- Dependabot tracks Python and Actions updates,
- CI builds the distributable package before merge,
- workflow permissions default to read-only contents access.

## Out of scope

The project does not attempt to secure:

- the operator's Freqtrade installation,
- exchange account configuration,
- host operating systems,
- VPNs, reverse proxies, or SSH tunnels,
- third-party exchange infrastructure.

Those systems remain separate trust domains.

## Security invariants

The following should remain true across releases:

1. No account mutation capability.
2. No secret in repository history.
3. No real production dataset in tests or documentation.
4. No automatic redirect carrying authentication.
5. No silent conversion of missing financial values to zero.
6. No binary floating-point representation in normalized monetary values.
7. No merge when the public-safety gate fails.
