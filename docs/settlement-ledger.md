# Exact-position settlement ledger — local candidate contract

This standalone operator-side checker provides a useful **arithmetical** audit
without making a claim about an exchange account, trading strategy or position
ownership. It never trades, logs into a source, invokes a network request or
sends account data to GitHub.

## Run

```sh
freqtrade-truth settlement-audit --input examples/settlement-ledger.synthetic.json
freqtrade-truth settlement-audit --input examples/settlement-ledger.synthetic.json --tolerance 0.00000001 --include-values
```

`--include-values` is opt-in; the default JSON redacts all native monetary
amounts, both net differences and every source identifier. Do not publish
private source JSON, `--include-values` results or exchange credentials.

## Exact input contract (schema v1)

- `schema`: `freqtrade-truth.settlement-ledger.v1`.
- `trade`: opaque `trade_ref` and exact `position_ref`, canonical `instrument`,
  uppercase `settlement_currency`, UTC `opened_at`/`closed_at`, optional
  *string-valued* `bot_net_pnl` and `exchange_reported_net_pnl` (null = missing).
- `coverage`: UTC `start_at`/`end_at` and literal booleans
  `price_complete`, `fees_complete`, `funding_complete`, `other_complete`.
  These are **operator claims**, not independently verified exchange coverage.
- `events`: up to 1000 unique records. Each has `id`, `position_ref`,
  `instrument`, `currency`, UTC `occurred_at`, `kind` (one of `price_pnl`,
  `trading_fee`, `funding`, `other_adjustment`) and a decimal **string** amount.
  Do not include foreign positions, unresolved events or duplicated IDs.

All event amounts use signed change-in-equity convention: trading fees paid
negative, rebates positive; funding expenses negative, receipts positive.
The exact native equation is:

`native_net = sum(price_pnl) + sum(trading_fee) + sum(funding) + sum(other_adjustment)`.

The reducer verifies both `exchange_reported_net_pnl - bot_net_pnl` and
`computed_native_net - exchange_reported_net_pnl`; it also compares the
computed net with bot net. Tolerance is an explicit finite nonnegative
Decimal. Aggregation uses a high-precision decimal context to avoid silently
rounding fractional fees. Each event is bound to exactly the supplied
position, canonical instrument, native currency and chronological window.

## Status vocabulary

- `match`: numerical equation agrees under **claimed** complete coverage.
- `review`: one or more provable numeric differences exceed tolerance, even
  when other evidence is incomplete (known mismatch takes precedence).
- `incomplete`: source coverage, price event or expected bot/exchange net missing;
  no silently inferred zero for incomplete components.
- `ambiguous`: duplicate event ID, unsupported kind, wrong position,
  instrument/currency or an event outside the window. No computed net is
  promoted after ambiguity.

A fully-covered funding or fee category with no event may contribute an
explicit candidate zero, but only under the caller's stated completeness
flag; price requires an observed event even for an exact zero.

## Security and limits

The output always sets `account_verified=false`,
`verified_trade_identity=false`, `ledger_coverage_independently_verified=false`,
`verified_financial_truth=false` and `usd_verified=false`, regardless of
numeric agreement. The program does **not** authenticate the account, prove
completeness from pagination/retention, infer funding ownership from time,
convert settlement currency, reconcile a multi-position portfolio or
independently prove the operator's asserted position mapping.

These steps require a separately authorized private read-only exchange
acceptance and independent source ownership/FX evidence. No exchange keys
belong in the repository, Actions Secrets, CI artifacts or chat.

See `examples/settlement-ledger.synthetic.json` for the exact format.
