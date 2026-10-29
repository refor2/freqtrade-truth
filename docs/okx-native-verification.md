# OKX native-source verification — pre-alpha 0.0.4

The original freqtrade-truth library now has an offline verifier for sanitized GET-only account evidence, built on the existing normalized native accounting model. It does not place or cancel orders, log in to a new account, or write trading state.

## Run the included test fixture

    freqtrade-truth okx-native-report --input examples/okx-native.synthetic.json

Expected synthetic aggregate: consistent=2, different=1, unknown=1, conflict=0. No private identifiers or financial amounts are emitted.

## Input

Local JSON with exact schema freqtrade-truth.okx-native.v1 and entries array. Each entry contains side (long/short) and source_payload from the already authorized and sanitized OKX GET-only read source. Only regular files of at most 2 MiB and 1 to 1000 entries are accepted.

The library provides two public Python functions in freqtrade_truth.core.okx_native_verification: verify_okx_position_native(payload, side, tolerance) and verify_okx_native_batch(entries, tolerance). Always pass an explicit nonnegative finite Decimal tolerance.

## What is checked

1. Upstream read_only, verified source identity, PASS source status and absence of source page truncation.
2. Exact Freqtrade exchange order ID and entry/exit role membership; an OKX triggered stoploss order must be explicitly linked to its originating stoploss order.
3. Matching and unique exchange fill IDs; no unrelated instrument/fill/order may be attributed.
4. One and only one fully closed isolated OKX position-history row with exact instrument and direction, whose cTime/uTime completely covers the fill time window.
5. Signed native position equation pnl + fee + funding_fee + liq_penalty = realized_pnl, using existing audit_native_record(). Null values never become zero.
6. Duplicate claims of the identical position cycle (instId, posId, cTime, uTime) are conflicts. Reuse of the same posId in distinct cycles is not an error.

Status consistent verifies source-native arithmetic in the strictly selected cycle under the upstream evidence contract. It does not verify account identity independently from arbitrary JSON, historical USD exchange-rate conversion, or every funding bill ownership. The account_verified_by_library and usd_verified flags are always false.

## Source evidence boundary

The included examples and acceptance results are synthetic only. Source-native
arithmetic and strict position-cycle checks do not independently prove a
real account, all historical fills or USD conversion. The exchange documents
position ID reuse after reopening; never loosen strict lifecycle matching
to fabricate a cycle. See issue #15.

## Fee ledger truth levels (0.0.5 pre-alpha)

`fee_fill_position_states` independently compares exact signed fees from
all Freqtrade-order-linked OKX fills and the selected OKX position-history
fee. `fee_ledger_states` checks a stronger and distinct claim: every fill
must additionally have a unique same-instrument, same-native-currency account
bill of trade subtype 1/2 with matching order ID, trade ID and signed fee.
Cross-trade reused bill IDs or cycle keys fail closed. A funding entry
subtype 173/174 cannot be substituted for a trading-fee bill.

No private-account counts, identifiers, trade activity or fees are
distributed with the open-source release. Test coverage uses synthetic
incomplete-bill and cross-cycle-conflict cases.

The independent fee check does not resolve ownership of unlinked funding,
USD/USDC settlement conversion, or the four subsecond lifecycle identities.
Both value `MATCH` and full Financial Truth `MATCH` must remain separate.


## Funding candidate review — v0.0.6 (27 October 2026)

The pre-existing time-window funding evidence collector is not an ownership
oracle. The separate OKX source-native reviewer preserves this distinction.
It accepts only bill `type=8`, subtype `173` (expense/debit) or `174`
(income/credit) with exact instrument, settlement currency and event timestamp
inside an already strictly matched position cycle.

The reviewer deduplicates repeat `billId` records across overlapping GET-only
per-trade source queries. Inconsistent data under a reused bill ID is a
source conflict. A bill matching two observed cycles is
ambiguous and is never assigned twice. Missing source coverage, invalid
sign/currency or unresolved position identity remain explicitly unverified.

Even exact source funding candidate sums are NOT proof that all account bills
belong to the external trading system trade or that the exchange's historical USD valuation is
known. `funding_ownership_verified`, `funding_ledger_coverage_verified`
and `usd_verified` remain false. This review does not alter trading,
Freqtrade APIs, financial cash accounting or external operator UI.

The included funding tests cover explicit debit, credit, zero,
incomplete coverage and conflicting cycle claims. No real account
metrics are published.

## Independently verified direct fill/account fee ledger — v0.0.7 draft (27 October 2026)

`fee_direct_ledger_states` is **additional evidence**, not a replacement
for `fee_ledger_states` or `states`. The new
`verify_okx_direct_fill_fee_ledger_batch()` accepts source-qualified
Freqtrade exchange-order ownership even when a position-history cycle
cannot be strictly selected because the exchange reports position
`cTime` later than the first fill `fillTime`. It still requires an
exact complete link for every fill: `(ordId, tradeId, billId)`, unique
`billId`, same `instId` and native fee currency, signed fee equality,
trade subtype `1/2` and nontruncated, already authenticated GET-only
evidence. Cross-trade reuse of a bill ID fails closed.

This does **not** relax position-cycle verification. A trade can have
`fee_direct_ledger_states.match` while its position cycle remains
`states.unknown`; it is incorrect to call that trade's full PnL
audited. Source identity is asserted only by the separately authenticated
upstream reader, never by the public library from arbitrary JSON.

Public verification is limited to synthetic test fixtures and
source-native API semantics. Economic account ownership and live-account
settlement remain unverified by this package.

OKX primary API contracts:

- `GET /api/v5/trade/fills-history`: `ordId`, `tradeId`,
  `billId`, `feeCcy`, `fee`, `ts` and `fillTime`.
- `GET /api/v5/account/bills` and `bills-archive`:
  native account journal, `billId`, `subType`, native `ccy` and
  signed `fee`/`pnl`.
- `GET /api/v5/account/positions-history`: the exchange's
  `realizedPnl = pnl + fee + fundingFee + liqPenalty + settledPnl`
  formula; never deduct trading fees again from `realizedPnl`.
- Funding bills `type=8` and `subType=173` (debit) / `174`
  (credit), with the payment in `pnl`. Matching time and instrument
  does not independently prove the trade-owned position cycle.
- `posId` may survive closing and reopening. The official OKX
  fill/position reconciliation guide describes matching `tradeId`
  against live positions updates but warns about aggregated updates
  and liquidation/ADL events. Without the relevant historical event
  chain, an exact position cycle may remain unknown.

Documentation: https://my.okx.com/docs-v5/ ;
https://my.okx.com/docs-v5/trick_en/ .

The native currency (e.g. USDC) is **not** a verified historical USD
value. OKX historical candlesticks may supply a market-data quotation,
but it is not automatically the account's recorded conversion at the
execution time. Historical funding ownership, complete ledger retention,
and event-time USD valuation are still separate v0.1 acceptance gates.
Neither trading execution nor production UI/account settlement behavior
was changed by the public draft commit.
