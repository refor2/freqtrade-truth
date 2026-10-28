# Compatibility

## Python

Freqtrade Truth is continuously tested on:

- Python 3.11
- Python 3.12
- Python 3.13
- Python 3.14

A change is not considered compatible until the full CI matrix passes.

## Freqtrade

The initial Freqtrade adapter uses the documented REST endpoint:

- `GET /api/v1/trades`
- `limit`
- `offset`
- `order_by_id=false`

The implementation was reviewed against the public Freqtrade stable REST documentation and a public Freqtrade source snapshot on 2026-10-02.

Reference source snapshot:

- https://github.com/freqtrade/freqtrade/tree/621a44cef1e275e12f58a6d75e336a0fbb0f1c3b

Relevant upstream schema fields currently consumed:

- `trade_id`
- `pair`
- `quote_currency`
- `is_open`
- `open_timestamp`
- `close_timestamp`
- `profit_abs`
- `funding_fees`

No minimum Freqtrade release number is claimed yet.

If an upstream version changes these fields or their semantics, the adapter must fail closed rather than guess.

## Bybit

The first exchange-side reader targets Bybit V5 inverse-contract closed PnL:

- `GET /v5/position/closed-pnl`
- `category=inverse`
- one explicitly configured symbol and settlement currency per adapter instance

The implementation was reviewed against Bybit's public V5 documentation and inverse-contract P&L documentation on 2026-10-02.

The adapter handles cursor pagination and splits longer queries into API-compliant time windows. It intentionally leaves funding and price-only PnL missing because the closed-PnL response does not provide those components separately.

The package includes a GET-only HMAC transport for Bybit V5 system-generated API keys. It requires HTTPS and follows the official timestamp + API key + receive-window + query-string signing rule.

The Bybit integration also includes a separate inverse transaction-log ledger reader using `GET /v5/account/transaction-log` with cursor pagination and seven-day time windows. Ledger events remain separate from closed-trade records so funding can be reconciled later without premature attribution.

The Bybit integration also includes a public, credential-free `GET /v5/market/instruments-info` preflight that verifies inverse-perpetual contract type, Trading status, base/quote/settlement assets, and current order/leverage metadata before authenticated reads.

See [bybit-adapter.md](bybit-adapter.md) for the exact field mapping and limitations.

## Operating systems

The library itself is platform-neutral Python.

CI currently validates Linux runners. Windows and macOS are expected to work but are not yet part of the release compatibility guarantee.

## Stability

Version 0.0.x is pre-alpha.

Public contracts are intentionally small but may still change before 0.1.0. Breaking changes must be documented in the changelog.


## Dated futures settlement currency (2026-10-21; candidate / NO-LIVE)

The original Freqtrade read adapter extracted everything after the colon
as a settlement currency. For the official CCXT dated-futures symbol
`BTC/USD:USD-310404`, that mistakenly produced `USD-310404`
rather than `USD`. This can prevent correct matching with an OKX
native USD-settled instrument. CCXT's unified format is
`BASE/QUOTE:SETTLE-YYMMDD` for dated futures; the date is **an instrument
identifier**, not part of `SETTLE`. The full `pair` remains unchanged in
the normalized record for instrument/expiry identity.

This scoped read-only candidate correction extracts the **settlement
currency** from `SETTLE` for a plain futures/swap suffix or a six-digit
dated expiry. Unrecognized/malformed suffixes fail closed rather than
producing a currency by guesswork. Option symbols and other unsupported
instrument syntaxes must be separately qualified before use.

Examples (source payload is synthetic):

| Pair | Settlement currency |
| --- | --- |
| `BTC/USD:USD-310404` | `USD` |
| `BTC/USD:BTC-310404` | `BTC` |
| `ETH/USDT:USDT-241231` | `USDT` |
| `BTC/USD:BTC` | `BTC` |
| `BTC/USD` + quote `USD` | `USD` |

**Scope:** this is not an OKX settlement adapter. It neither changes bot
execution nor proves exchange account identity, position PnL, funding,
fee ownership or derivative contract size. An adapter for one exchange cannot certify settlement data
from another exchange or validate private account ownership.
No production deployment, migration or trading operation is performed.

Official source for CCXT contract symbol naming:
https://github.com/ccxt/ccxt/wiki/Manual#contract-naming-conventions
