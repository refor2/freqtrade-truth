# Normalized data model

The public model is intentionally small. It exists to make bot-side and exchange-side records comparable without leaking implementation details into the reconciliation core.

## Monetary precision

All financial values use decimal.Decimal.

Binary floating point is not accepted in normalized records because reconciliation differences can be smaller than the representation error introduced by float.

## Signed amount convention

Every monetary field follows one rule:

> Positive values increase equity. Negative values decrease equity.

Examples:

- profitable price movement: positive price_pnl,
- losing price movement: negative price_pnl,
- fee paid: negative trading_fees,
- fee rebate: positive trading_fees,
- funding paid: negative funding,
- funding received: positive funding.

The model does not guess or invert signs. Adapters are responsible for translating upstream conventions into this canonical form.

## Missing is not zero

None means the source did not provide a trustworthy value.

Decimal("0") means the source explicitly represents the value as zero.

These states are not interchangeable.

This distinction is required so future reconciliation can report incomplete evidence instead of manufacturing a false match.

## Timestamps

opened_at, when present, and closed_at must be timezone-aware and normalized to UTC.

Adapters must perform timezone conversion before creating a normalized record.

## Identifiers

The model uses opaque string identifiers:

- record_id identifies the normalized source record,
- trade_ref identifies the logical trade in the originating system.

The core must not assume a particular exchange ID shape or database key format.

Public fixtures use synthetic identifiers only.

## Instrument and settlement currency

instrument is an adapter-normalized instrument label.

settlement_currency uses uppercase canonical form such as USDT, USD, or BTC.

Cross-currency conversion is outside the foundation milestone.

## Financial fields

A normalized closed trade may expose:

- price_pnl,
- trading_fees,
- funding,
- other_adjustments,
- reported_net_pnl.

Adapters declare which fields they can provide through AdapterCapabilities.

No field is silently synthesized merely to satisfy the model.

## Immutability

Normalized trade records are immutable dataclasses.

This makes them safe inputs for deterministic reconciliation and reduces accidental mutation across layers.
