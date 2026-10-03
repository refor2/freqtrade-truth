# Architecture

## Objective

Freqtrade Truth is a narrow, read-only reconciliation layer.

It compares normalized bot-side financial records with normalized exchange-side settlement records and reports explained or unexplained differences.

## Boundaries

```text
┌────────────────────┐
│ Freqtrade read API │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Freqtrade adapter  │
└─────────┬──────────┘
          │
          ├──────────────┐
          │              ▼
          │     ┌──────────────────┐
          │     │ Normalized model │
          │     └────────┬─────────┘
          │              │
          │              ▼
          │     ┌──────────────────┐
          │     │ Reconciliation   │
          │     └────────┬─────────┘
          │              │
          │              ▼
          │     ┌──────────────────┐
          │     │ Report / export  │
          │     └──────────────────┘
          │
┌─────────┴──────────┐
│ Exchange adapter   │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Exchange read API  │
└────────────────────┘
```

## Layering rules

Dependencies point inward:

1. **Adapters** depend on public domain contracts.
2. **Core** depends only on normalized domain contracts.
3. **API / UI** consume core services and public result models.
4. Domain contracts never depend on HTTP clients, exchange SDKs, databases, UI frameworks, or strategy code.

This keeps exchange integrations replaceable and makes the core deterministic and testable without live credentials.

## Domain representation

Financial values use `decimal.Decimal`, never binary floating point.

All normalized timestamps are timezone-aware UTC values.

Missing financial values remain `None`; adapters must not silently map unavailable values to zero.

Signed amount convention:

- positive values increase account equity,
- negative values decrease account equity.

For example, a trading fee paid by the account is negative, while a rebate received by the account is positive.

See [data-model.md](data-model.md).

## Adapter contract

Adapters are read-only translators.

They retrieve the minimum records required for reconciliation and translate them into normalized public domain objects.

The public adapter interface intentionally exposes only fetch/capability operations. Order placement, cancellation, leverage changes, withdrawals, transfers, strategy decisions, and other account mutations are outside the contract.

Adapters must:

- normalize timestamps to UTC,
- use `Decimal` for monetary values,
- preserve missing data as missing,
- expose their supported financial fields,
- avoid hidden mutation or execution methods,
- be testable without live credentials.

## Core contract

The core remains deterministic and exchange-agnostic.

Given normalized input records and an explicit tolerance policy, identical inputs must produce
identical grouping and, as later comparison stages are added, identical reconciliation results.

The first reconciliation stage groups records by exact canonical instrument and settlement
currency, then connects opposite-source records whose close timestamps fall within the explicit
close-time tolerance. Connected components are preserved intact. A component containing more
than one Freqtrade record is therefore visible as ambiguous rather than being silently assigned
by a nearest-record heuristic.

Financial comparison stages build on the same explicit tolerance contract; they do not infer
missing values or mutate source records.

## Missing data

Missing or ambiguous financial data must not be silently converted to zero.

The reconciliation layer should preserve uncertainty explicitly and fail closed when a trustworthy comparison cannot be produced.

## Non-goals

The project must not become:

- an order execution engine,
- a trading strategy framework,
- a signal generator,
- a portfolio allocation engine,
- an account mutation tool,
- a store for private production data.

## Public-data rule

Repository fixtures and documentation use synthetic data only. No real account, order, trade, balance, endpoint, infrastructure, or private strategy information belongs in this repository.
