# Bybit inverse closed-PnL adapter

## Scope

The initial Bybit integration reads closed PnL for one configured inverse-contract symbol through:

- `GET /v5/position/closed-pnl`
- `category=inverse`
- an explicit uppercase symbol such as the synthetic/example target `BTCUSD`

The adapter is read-only and accepts an injected JSON transport. Authentication and request signing are intentionally outside this first adapter increment.

Official sources reviewed:

- https://bybit-exchange.github.io/docs/v5/position/close-pnl
- https://www.bybit.com/en/help-center/article/Profit-Loss-calculations-Inverse-Contracts
- https://bybit-exchange.github.io/docs/v5/account/transaction-log

## Mapping

| Bybit field | Normalized field | Rule |
| --- | --- | --- |
| `orderId` | `trade_ref` | preserved as source reference |
| `symbol` | `instrument` | must match configured symbol |
| `updatedTime` | `closed_at` | milliseconds converted to UTC |
| configured currency | `settlement_currency` | explicit, e.g. `BTC` for `BTCUSD` |
| `openFee + closeFee` | `trading_fees` | sign inverted to project equity convention |
| `closedPnl` | `reported_net_pnl` | preserved as exchange-reported net result |

Bybit documents inverse contracts as settling in the underlying asset. The adapter therefore requires the settlement currency explicitly instead of guessing it from a symbol parser.

## Important semantic limitation

Bybit's documented closed PnL includes position PnL, opening/closing trading fees, and funding. The closed-PnL endpoint does not expose funding as a separate field.

For that reason:

- `reported_net_pnl` is populated from `closedPnl`,
- `trading_fees` is populated only when both `openFee` and `closeFee` are present,
- `funding` remains `None`,
- `price_pnl` remains `None`.

The adapter does not derive a missing funding value by subtraction.

A future transaction-log enrichment layer may provide a separately sourced funding component.

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
