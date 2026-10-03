# Roadmap

Freqtrade Truth is developed in small, auditable milestones. The roadmap prioritizes correctness, read-only behavior, testability, and public safety over feature count.

## v0.1 — Financial truth foundation

### M1 — Public contracts

Status: in progress

- [x] Normalized closed-trade domain model.
- [x] Signed financial amount convention.
- [x] UTC timestamp contract.
- [x] Explicit missing-value semantics.
- [x] Read-only adapter protocol.
- [x] Adapter capability declaration.
- [x] Synthetic fixtures.
- [x] Strict typing and compatibility CI.
- [ ] Stabilize public model names after community feedback.

### M2 — Read adapters

- [x] Freqtrade closed-trade reader.
- [x] First exchange settlement reader (Bybit inverse closed PnL).
- [x] Bybit GET-only HMAC authentication transport.
- [x] Bybit read-only API-key preflight.
- [x] Bybit inverse transaction-log ledger reader.
- [x] Bybit public inverse-market preflight and Trading-symbol discovery.
- [x] Adapter contract tests shared across implementations.
- [x] Freqtrade pagination and time-window handling.
- [x] Bounded retry support for HTTP 429/503 with Retry-After handling.
- [x] Stable source name and source trade reference for normalized Freqtrade records.
- [x] No live credentials required for unit tests.
- [x] Redacted Bybit runtime smoke CLI with public-only default and opt-in read-only account checks.

### M3 — Deterministic reconciliation

- [x] Group comparable records.
- [x] Explicit tolerance policy.
- [x] Price PnL comparison when both sides provide normalized values.
- [x] Trading-fee comparison when both sides provide normalized values.
- [x] Funding comparison when both sides provide normalized values.
- [x] Reported net-PnL comparison when both sides provide normalized values.
- [x] Missing/incomplete-data outcome.
- [x] Machine-readable reason codes.
- [x] Deterministic unit and property-oriented tests.
- [ ] Ledger enrichment / attribution for exchange components absent from closed-trade records.

### M4 — Reporting surface

- [ ] Minimal read-only HTTP API.
- [ ] Minimal reconciliation table.
- [ ] JSON export.
- [ ] CSV export.
- [ ] Clear MATCH / REVIEW / INCOMPLETE states.
- [ ] No trading or account-mutation controls.

### M5 — v0.1 release hardening

- [x] Threat-model review.
- [x] Dependency audit.
- [x] Documentation walkthrough using synthetic data.
- [x] Reproducible release process.
- [x] Changelog.
- [ ] Signed/tagged release.
- [x] Public issue templates.
- [x] Compatibility statement.

## Post-v0.1 candidates

These are deliberately not commitments:

- additional exchanges,
- pluggable persistence,
- batch reconciliation,
- richer provenance,
- metrics / observability integration,
- additional report formats.

## Permanent non-goals

Freqtrade Truth will not include:

- trading signals,
- strategy logic,
- order placement,
- order cancellation,
- leverage changes,
- transfers or withdrawals,
- portfolio allocation,
- proprietary ranking or risk logic,
- private production datasets.

Any proposal that requires mutation of a trading account belongs outside this project.
