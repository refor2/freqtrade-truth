# Bybit inverse closed-PnL adapter

## Scope

The initial Bybit integration reads closed PnL for one configured inverse-contract symbol through:

- `GET /v5/position/closed-pnl`
- `category=inverse`
- an explicit uppercase symbol such as the synthetic/example target `BTCUSD`

The adapter is read-only and accepts an injected JSON transport. The package also provides a GET-only HMAC transport for Bybit V5 system-generated API keys.

Official sources reviewed:

- https://bybit-exchange.github.io/docs/v5/position/close-pnl
- https://www.bybit.com/en/help-center/article/Profit-Loss-calculations-Inverse-Contracts
- https://bybit-exchange.github.io/docs/v5/account/transaction-log
- https://bybit-exchange.github.io/docs/v5/guide

## Mapping

| Bybit field | Normalized field | Rule |
| --- | --- | --- |
| `orderId` | `trade_ref` | preserved as source reference |
| `symbol` | source transport identity | must match configured symbol |
| `updatedTime` | `closed_at` | milliseconds converted to UTC |
| configured currency | `settlement_currency` | explicit, e.g. `BTC` for `BTCUSD` |
| `openFee + closeFee` | `trading_fees` | sign inverted to project equity convention |
| `closedPnl` | `reported_net_pnl` | preserved as exchange-reported net result |

Bybit documents inverse contracts as settling in the underlying asset. The adapter therefore requires the settlement currency explicitly instead of guessing it from a symbol parser.

For reconciliation, the adapter can also receive an explicit `normalized_instrument`. The public market preflight exposes this as `BybitInverseMarketInfo.normalized_instrument` using canonical `BASE/QUOTE:SETTLEMENT` form, for example `BTC/USD:BTC`. The exchange-native symbol is still used for Bybit requests and response validation; only the normalized record identity changes.

## Important semantic limitation

Bybit's documented closed PnL includes position PnL, opening/closing trading fees, and funding. The closed-PnL endpoint does not expose funding as a separate field.

For that reason:

- `reported_net_pnl` is populated from `closedPnl`,
- `trading_fees` is populated only when both `openFee` and `closeFee` are present,
- `funding` remains `None`,
- `price_pnl` remains `None`.

The adapter does not derive a missing funding value by subtraction.

A future transaction-log enrichment layer may provide a separately sourced funding component.

### Closed-PnL economic contract: no double-counting (#15-B, 2026-10-21)

Official Bybit documentation says inverse **Closed P&L** already incorporates
opening/closing trading fees and allocated funding. Partial closures may receive
prorated opening fees and funding. The `closedPnl` source field is therefore an
**already-net** result, not price-only PnL.

Synthetic example in settlement BTC:

| Source item | Source amount | Normalized meaning |
| --- | ---: | --- |
| `closedPnl` | `0.00103` | `reported_net_pnl = 0.00103` |
| `openFee` | `0.00008` | Fee cost, included in reported net |
| `closeFee` | `-0.00002` | Fee rebate, included in reported net |
| Derived fee breakdown | — | `trading_fees = -0.00006` (equity-signed) |
| Funding component | Not supplied here | `funding = None`, **not** zero |

Do **not** subtract the fee breakdown from `reported_net_pnl`, add ledger funding
to it again, or derive gross price PnL from these fields. A separate ledger
stream is candidate evidence until trade/position ownership, coverage and
non-overlap are proven. Fee and funding values across settlement currencies
must not be silently summed or converted. This documents source semantics,
**not** approval of final v0.1 reconciliation statuses/attribution.

The Closed-PnL endpoint can include `execType` values other than `Trade`
(e.g. `BustTrade`, `SessionSettlePnL`, `Settle`, `MovePosition`,
`CorporateAction`). If the field is explicitly present, this preliminary
trade-only adapter accepts `Trade` and **fails closed** for any other or
malformed value. Historical payloads omitting the optional `execType` continue
to normalize for backwards compatibility; omission is **not independent
proof** of event classification, complete settlement, or trade ownership.

Official sources:
- https://bybit-exchange.github.io/docs/v5/position/close-pnl
- https://www.bybit.com/en/help-center/article/Profit-Loss-calculations-Inverse-Contracts
- https://bybit-exchange.github.io/docs/v5/account/transaction-log

## Pagination and time windows

Bybit limits a closed-PnL request to a maximum seven-day interval and supports cursor pagination.

The adapter:

- splits longer queries into non-overlapping millisecond windows shorter than or equal to seven days,
- follows `nextPageCursor`,
- rejects repeated cursors,
- rejects duplicate normalized records,
- applies deterministic chronological sorting before a query limit.

## Failure behavior

The adapter fails closed when:

- `retCode` is non-zero,
- the result category is not `inverse`,
- the response symbol differs from the configured symbol,
- required fields are malformed,
- only one of `openFee` / `closeFee` is present,
- a cursor repeats,
- a duplicate record identifier appears.

All tests use synthetic values only.

## HMAC authenticated transport

`BybitV5HmacTransport` implements the official V5 GET signing rule for system-generated HMAC keys:

`timestamp + api_key + recv_window + queryString`

The resulting HMAC-SHA256 signature is sent as lowercase hexadecimal together with:

- `X-BAPI-API-KEY`
- `X-BAPI-TIMESTAMP`
- `X-BAPI-SIGN`
- `X-BAPI-RECV-WINDOW`

The transport is GET-only, requires HTTPS, rejects credentials embedded in URLs, rejects non-V5 paths, never includes credentials in its own exception messages, and bounds response size.

The exact query string used to compute the signature is also used in the outgoing request. This avoids signature drift caused by signing one parameter representation and sending another.

Credentials are runtime-only values. They must never be committed to this repository, fixtures, logs, screenshots, issues, or pull requests.

Bybit documents that authenticated timestamps must satisfy the server-time/receive-window rule. Systems using this transport should keep their local clock synchronized.

For production use, create a read-only Bybit API key and keep the secret outside Git tracking. The current transport does not create, modify, or cancel orders and exposes no mutation methods.

## Read-only API key preflight

Before reading account data, call `verify_bybit_read_only_key()` with the authenticated transport.

The preflight calls:

- `GET /v5/user/query-api`

and requires the response to report:

- `readOnly = 1`

Any unsuccessful, malformed, or read/write response fails closed with `BybitApiKeySafetyError`.

This check does not modify the API key. It only verifies the access mode reported by Bybit.

## Transaction-log ledger reader

`BybitInverseTransactionLogReader` reads:

- `GET /v5/account/transaction-log`
- `accountType=UNIFIED`
- `category=inverse`
- an explicit settlement currency such as `BTC`

The transaction log is treated as a ledger stream, not as a closed-trade stream.

Like the closed-PnL adapter, the reader can receive an explicit `normalized_instrument`. Raw Bybit symbols are still used for response filtering; accepted symbol-bearing ledger records are then exposed with the canonical instrument identity. Symbol-less ledger records remain `None` so downstream evidence collection can treat them as ambiguous instead of guessing.

This distinction is intentional: funding settlements can occur while a position is still open and may span multiple settlement events before the eventual close. The reader therefore preserves each source ledger event independently rather than attaching funding to a closed trade prematurely.

Normalized signs:

- Bybit `funding`: preserved as-is because positive means funding received and negative means funding paid.
- Bybit `fee`: sign-inverted so project `trading_fees` is negative for an expense and positive for a rebate.
- `cashFlow`: preserved as reported.
- `change`: preserved as `reported_net_change`.

When funding and fee are both present, the reader validates the documented identity:

`change = cashFlow + funding + normalized_trading_fees`

A mismatch fails closed.

The reader follows cursor pagination, splits time ranges into API-compliant seven-day windows, filters other symbols locally, rejects duplicate source records and repeated cursors, and uses synthetic data only in tests.

## Public inverse market preflight

The package includes a credential-free public market preflight based on:

- `GET /v5/market/instruments-info`
- `category=inverse`
- `status=Trading`

`verify_bybit_inverse_perpetual()` verifies that the requested symbol still matches the expected public contract invariants before authenticated account reads begin.

For a `BTCUSD` deployment the caller can require:

- `contractType = InversePerpetual`
- `status = Trading`
- `baseCoin = BTC`
- `quoteCoin = USD`
- `settleCoin = BTC`

The preflight also returns the current public minimum/maximum leverage, leverage step, minimum order quantity, quantity step, and tick size. These values are read from Bybit at runtime rather than hard-coded as permanent assumptions.

`list_bybit_inverse_trading_symbols()` retrieves the complete current Trading inverse-symbol set using cursor pagination.

`BybitV5PublicTransport` is a credential-free HTTPS GET transport for public V5 endpoints. It rejects non-V5 paths, credentials embedded in URLs, oversized responses, and non-object JSON payloads.

A changed contract type, delisted/non-Trading symbol, changed settlement asset, malformed response, duplicate symbol, or repeated pagination cursor fails closed.
