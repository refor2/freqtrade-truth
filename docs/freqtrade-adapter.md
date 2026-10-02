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
