# Freqtrade read adapter

The Freqtrade adapter is intentionally narrow and read-only.

It consumes the public Freqtrade REST `GET /api/v1/trades` endpoint and converts closed trades into the normalized Freqtrade Truth domain model.

Official upstream references:

- https://www.freqtrade.io/en/stable/rest-api/
- https://github.com/freqtrade/freqtrade/blob/develop/docs/rest-api.md

## Supported source behavior

Freqtrade documents the trades endpoint with:

- GET requests,
- a maximum of 500 trades per request,
- `limit` and `offset` pagination,
- optional ordering by latest timestamp.

The adapter therefore requests pages using:

- `limit <= 500`,
- explicit `offset`,
- `order_by_id=false`.

All filtering by reconciliation time window and instrument happens locally after normalization.

## Authentication

The dependency-free transport supports optional HTTP Basic Auth, matching the authentication mode used by the official Freqtrade client.

Credentials are passed by the caller and retained in memory only.

The transport:

- rejects credentials embedded in URLs,
- never includes credentials in exception messages,
- performs GET requests only,
- does not disable TLS certificate verification,
- requires a positive timeout.

## Financial mapping

The first adapter version is deliberately conservative.

It maps:

- Freqtrade `profit_abs` -> normalized `reported_net_pnl`,
- Freqtrade `funding_fees` -> normalized `funding`.

It does **not** synthesize:

- price-only PnL,
- trading-fee decomposition,
- other adjustments.

Those fields remain `None` until a trustworthy source-specific mapping is defined.

This is intentional. Missing data must not be represented as zero.

## Funding sign

Freqtrade documents in its source model that positive funding fees mean the trade gained from funding and negative funding fees mean the trade paid funding.

That convention matches the Freqtrade Truth signed-amount rule directly.

## Settlement currency

For derivatives-style instrument names containing an explicit settlement suffix, for example `BTC/USD:BTC`, the suffix is used.

Otherwise the Freqtrade `quote_currency` field is used.

## Pagination safety

The adapter validates:

- response offset,
- total trade count,
- list/object shapes,
- required trade identifiers,
- required closed timestamps,
- numeric financial values.

Malformed or ambiguous data raises `FreqtradeResponseError`.

The adapter does not silently skip malformed closed trades.

## Testing

Unit tests use synthetic JSON pages only.

No live bot, exchange account, API credential, production endpoint, or real trade history is required by the test suite.

## Provisional exchange-order references for identity review (#15)

The isolated `FreqtradeClosedTradeOrderReader` is a candidate-evidence helper,
**not** part of the stable public financial or attribution contract.

It reads only Freqtrade's documented `GET /api/v1/trade/<tradeid>` endpoint for
a caller-provided trade ID and expected canonical instrument. The response
must explicitly describe the requested closed trade and contain an `orders`
array with valid, unique exchange `order_id` references. All discrepancies
fail closed; an empty order array yields **no observed references**.

The resulting `observed_exchange_order_ids` can be checked against Bybit's
`orderId` values from `GET /v5/position/closed-pnl` during future #15
identity qualification. This is stronger candidate evidence than matching
timestamps/amounts alone, but **does not itself prove** account scope,
fill/execution ownership, completeness, attribution of funding/fees, or a
reconciled final net result. No order IDs are persisted or printed by the
reader, and the core's preview `MATCH` result is unchanged.

Official references:
- https://www.freqtrade.io/en/stable/rest-api/
- https://www.freqtrade.io/en/stable/trade-object/
- https://bybit-exchange.github.io/docs/v5/position/close-pnl

The associated synthetic regression includes one explicitly expected-to-fail
final-identity assertion. That marked debt is a **v0.1 blocker**, not a
financial-correctness PASS, until #15 accepts and implements a verified
cross-source identity/ownership contract.
