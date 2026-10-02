# Changelog

All notable public changes to Freqtrade Truth are documented here.

## Unreleased

### Added

- Bybit inverse transaction-log ledger reader preserving funding, normalized fee signs, cash flow, and reported net change.
- Fail-closed Bybit API-key preflight requiring the authenticated key to report read-only mode.
- GET-only Bybit V5 HMAC transport using the official signing rule and runtime-only credentials.
- Bybit V5 inverse closed-PnL reader with explicit BTCUSD-style settlement currency, cursor pagination, and API-compliant time-window splitting.
- Fail-closed Bybit normalization that preserves missing funding/price PnL instead of deriving unavailable components.
- Shared contract tests exercised across multiple read-only adapter implementations.
- Commit metadata privacy gate for protected project identities.
- Bounded, deterministic retry transport for transient HTTP 429/503 responses.
- Retry-After support with a configured maximum delay cap.
- Structured HTTP transport errors exposing status code and retry metadata.

### Security

- Bybit authenticated transport requires HTTPS and never includes credentials in its own exception messages.
- Retries are limited by attempt count and maximum delay.
- Non-configured HTTP failures are not retried.
- Redirect behavior remains fail-closed and credentials are never forwarded across redirects.

## 0.0.1 - 2026-10-02

### Added

- Public-safe repository foundation and contribution policy.
- Normalized immutable financial domain contracts.
- Read-only adapter protocol.
- Synthetic fixtures and contract tests.
- Freqtrade closed-trade read adapter.
- Dependency-free GET-only HTTP transport.
- Python 3.11–3.14 CI, strict typing, linting, formatting, and packaging checks.
- Public threat model, compatibility statement, versioning policy, and safe issue intake.

### Security

- Public-safety scan required by CI.
- Credentials are prohibited from URLs and public fixtures.
- Authenticated non-loopback HTTP is rejected.
- Redirects are not followed by the built-in transport.
- HTTP response size is bounded.
- HTTP responses and sockets are closed explicitly.
