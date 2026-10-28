# Changelog

All notable public changes to Freqtrade Truth are documented here.

## Unreleased — 0.0.7 exact bill query-window evidence (tag pending)

- Reject out-of-window Bybit inverse closed-PnL and account transaction-log
  rows for each requested chunk instead of silently dropping them while
  reporting pagination completion. Synthetic source and integration regressions
  cover both sides; this is not complete historical-account validation.
- Harden the independently runnable Bybit GET-only candidate mode: enforce
  exact private-request parameter allowlists, finite millisecond windows,
  bounded page size and source identity fields before any authenticated I/O.
  Synthetic negative tests prove malformed/widened reads are rejected locally.
  No change to trade identity or fee/funding verification claims.
- Detect independently whether a sanitized OKX source bill query covers the
  actual associated fill timestamps. Out-of-window fill-without-bill
  is a **source-window gap**, not proof that the exchange lacks that bill.
- Distinguish missing before/inside/after query window using exact Unix ms;
  fail-closed on inconsistent source identity, repeated fill keys, or foreign bills.
- Redacted CLI `bill_query_window_coverage` counts only, without trade or bill IDs.
- Source-window gaps are covered by synthetic validation; no private-account aggregates are published.
- No integration with trading engines or private deployments; v0.1
  event-time FX and strict lifecycle remain unresolved gates.

## Unreleased — 0.0.6 OKX funding candidate integrity (tag pending)

- Added source-native **funding candidate** batch checks on existing
  authenticated GET-only evidence, reusing the strict cycle identity API.
- Validate funding bill type 8, subtypes 173/174, signed values,
  instrument/currency, event time and stable unique bill identifiers.
- Deduplicate recurring account bills across per-trade queries and flag
  conflicting bill content or overlapping candidate position cycles.
- Keep ownership, full account coverage and historical USD validation
  explicitly **false** even for mathematically consistent candidates.
- Funding candidate acceptance is covered by synthetic fixtures; account metrics are not published.
- Public synthetic regressions and reporting do not modify exchange
  accounts, strategies, engines, credentials or private deployments.
- Historical USD settlement and account-level bill ownership remain
  blocked by missing authoritative evidence (issue #15).

## Unreleased — 0.0.5 independent OKX fee evidence gates (tag pending)

- Reuse strict position-cycle/linked-order identity and Decimal arithmetic:
  independently compare complete OKX fill fees to position-history fee.
- Keep the separate ledger completeness gate fail-closed; every fill requires
  a unique matching bill ID, exact order ID, trade ID, native currency and
  fee. Incomplete, ambiguous and conflicting ownership are never MATCH.
- Batch anti-duplication across trade claims for both position cycles and
  bill IDs; no fee/ledger values or identifiers leak from the CLI report.
- Fee reconciliation is validated in synthetic fixtures; no private trade aggregates are released.
- Synthetic negative fixtures, package and CI coverage added; no external
  trading deployment changes. v0.1 identity and USD gates remain open.

## Unreleased — 0.0.4 OKX source-native cycle verification (tag pending)

- Reuse the existing strict OKX sanitized GET-only account evidence shape and the
  previously audited `audit_native_record()` native money equation.
- New deterministic library verifier covers strong Freqtrade order IDs, conditional
  stoploss child IDs, strict OKX fill membership and unique non-overlapping
  cTime/uTime position cycles, source truncation and missing amounts.
- `PositionNativeStatus` clearly separates consistent/different/unknown/conflict;
  repeated `posId` on separate cycles is allowed, two claims on the *same*
  complete cycle are rejected.
- Local `okx-native-report` CLI consumes sanitized snapshots and emits **counts
  only**, never IDs/amounts, without external account access or writes.
- Source-native cycle verification is deterministic and tested with synthetic fixtures.
- Additional synthetic negative tests, exact-package/CI verification, docs.
- Still **no independent USD settlement FX or 4/4 strict cycle identity**;
  a signed account reader is required for source trust. Not v0.1 Financial Truth.

## Unreleased — 0.0.3 read-only candidate reporting (tag pending)

- Reuse the existing deterministic grouping and numerical comparison core with
  standalone redacted JSON, CSV and static HTML reports.
- Add bounded local normalized snapshot input, offline CLI reporting and an
  optional loopback-only GET HTTP view (no POST, JS, credentials or trading).
- Explicitly expose numeric results as *candidates*, always unverified for
  cross-system trade identity and account financial truth.
- Add full synthetic match/review/incomplete, unsafe input, redaction and HTTP
  regression tests. Includes synthetic OKX XPERP native USDC coverage.
- **Not** a stable financial reconciliation v0.1 release: the verified ownership,
  field attribution and event-time USD valuation gates remain open in issue #15.

## Unreleased — 0.0.2 pre-alpha release candidate (tag pending)

The 0.0.2 code integrates the independent Bybit/Freqtrade evidence drafts and
the previously merged offline demo. This **does not** finalize v0.1 financial
identity/ownership semantics: numerical `MATCH` is a preview-only field
comparison, never proof of shared economic trade ownership. See issue #15.

### Added

- Source-only positive-filled Freqtrade order candidates and read-only Bybit
  unique `execId` execution evidence for partial fills.
- Exhaustive source cursor pagination evidence with explicit incomplete-window
  failure; this does **not** prove retained full account history.
- Candidate-only Bybit transaction-log fee corroboration, three-source
  order/execution/ledger checks and non-ownership safety restrictions.
- Fully offline `freqtrade-truth demo [--json]` with synthetic numerical
  matches, mismatches and unknowns; installed CLI smoke test in CI.
- Regression tests preventing a positive-filled order with no exchange
  executions from being accepted as a complete candidate.

### Fixed

- Preserve the difference between source-native Bybit closed-PnL and already
  included fees/funding; exclude non-Trade closed-PnL events.
- Treat observed `funding=0` on TRADE fee records as zero, not a nonzero
  funding charge.
- For CCXT dated contracts such as `BTC/USD:USD-310404`, normalize
  settlement currency as `USD` while keeping the complete instrument identity.

### Limitations / Release gate

- No cross-source authoritative bot/account/position ownership or complete
  ledger lifecycle proof; no financial `MATCH` verification, fee/funding
  attribution, real-account end-to-end evidence or stable v0.1 financial API.
- Read-only public Bybit inverse adapter only, not an OKX/OKX settlement
  implementation. Private bots and private accounts are entirely outside scope.
- Issue #15 (contract) and #18 (protected main before release tag) remain open.
  Keep release as candidate until governance gates are met.

## Previous Unreleased

### Added

- Release-artifact integrity checks for package metadata, archive safety, typed-package marker, and build layout.
- Mechanical read-only regression guards for public adapter methods, HTTP verbs, and CLI commands.
- Fail-closed funding ledger evidence windows with explicit coverage, ambiguity, and candidate-funding states.
- Canonical instrument identity support for accepted Bybit transaction-log records while preserving symbol-less events as ambiguous evidence.
- Deterministic financial-field reconciliation results with MATCH / REVIEW / INCOMPLETE states and machine-readable reason codes.
- Exact Decimal aggregation across exchange-side partial records with fail-closed handling for missing components.
- Deterministic reconciliation grouping by canonical instrument, settlement currency, and explicit close-time tolerance; ambiguous many-to-many components remain visible instead of being guessed.
- Explicit absolute financial tolerance contract for price PnL, trading fees, funding, other adjustments, and reported net PnL.
- Canonical derivative instrument identity in `BASE/QUOTE:SETTLEMENT` form, including Bybit market-preflight support and optional normalized closed-PnL output.
- Redacted `freqtrade-truth bybit-check` CLI with credential-free market verification and opt-in authenticated read-only smoke checks.
- Credential-free Bybit inverse-market preflight and Trading-symbol discovery using public V5 instrument metadata.
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

- Authenticated Bybit transport now allowlists only the read endpoints required by the project.
- Authenticated CLI host selection is restricted to documented official Bybit API hosts.
- CI GitHub Actions are pinned to immutable commit SHAs and the installed wheel CLI is smoke-tested.
- Threat-model, dependency, and synthetic first-run reviews completed for the current pre-alpha scope.
- CLI credentials are accepted only through environment variables and are redacted from known runtime errors.
- Authenticated CLI output reports only bounded record counts and public market metadata, not account identifiers, trade IDs, balances, or financial amounts.
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
