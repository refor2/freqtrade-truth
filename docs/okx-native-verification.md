# OKX native-source verification — pre-alpha 0.0.4

The original freqtrade-truth library now has an offline verifier for sanitized GET-only account evidence, built on the existing normalized native accounting model. It does not place or cancel orders, log in to a new account, or write trading state.

## Run the included test fixture

    freqtrade-truth okx-native-report --input examples/okx-native.synthetic.json

Expected synthetic aggregate: consistent=2, different=1, unknown=1, conflict=0. No private identifiers or financial amounts are emitted.

## Input

Local JSON with exact schema freqtrade-truth.okx-native.v1 and entries array. Each entry contains side (long/short) and source_payload from the already authorized and sanitized MyOKX GET-only read source. Only regular files of at most 2 MiB and 1 to 1000 entries are accepted.

The library provides two public Python functions in freqtrade_truth.core.okx_native_verification: verify_okx_position_native(payload, side, tolerance) and verify_okx_native_batch(entries, tolerance). Always pass an explicit nonnegative finite Decimal tolerance.

## What is checked

1. Upstream read_only, verified source identity, PASS source status and absence of source page truncation.
2. Exact Freqtrade exchange order ID and entry/exit role membership; an OKX triggered stoploss order must be explicitly linked to its originating stoploss order.
3. Matching and unique exchange fill IDs; no unrelated instrument/fill/order may be attributed.
4. One and only one fully closed isolated OKX position-history row with exact instrument and direction, whose cTime/uTime completely covers the fill time window.
5. Signed native position equation pnl + fee + funding_fee + liq_penalty = realized_pnl, using existing audit_native_record(). Null values never become zero.
6. Duplicate claims of the identical position cycle (instId, posId, cTime, uTime) are conflicts. Reuse of the same posId in distinct cycles is not an error.

Status consistent verifies source-native arithmetic in the strictly selected cycle under the upstream evidence contract. It does not verify account identity independently from arbitrary JSON, historical USD exchange-rate conversion, or every funding bill ownership. The account_verified_by_library and usd_verified flags are always false.

## Independently executed source check — 2026-10-23

All 28 available BOT2 closed trade records were processed using the existing c48 MyOKX read-only source. Result: 24 native consistent, four unknown because OKX position creation cTime followed first fillTime by less than one second. Independent production native audit reached exactly the same 24/4 classification. All 28 retained PARTIAL/UNKNOWN for USD valuation. No private account data or credentials were copied to GitHub; no BOT2 code was changed.

OKX docs explicitly state posId is reused after close/reopen (https://www.okx.com/docs-v5/trick_en/). Do not loosen the time predicate or assign a reused posId without separate evidence. Source limitations and final v0.1 Financial Truth contract gates remain recorded in issue https://github.com/refor2/freqtrade-truth/issues/15 .

This is a usable library feature, not a claim of production financial MATCH or completed historical USD valuation.
