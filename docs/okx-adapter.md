# OKX V5: read-only full-position close history (first public adapter)

This module adds an **independent OKX adapter** while retaining Bybit. It reads
`GET /api/v5/account/positions-history` only through the injected
`JsonHttpTransport` interface. No account credentials or production connection
are needed for tests. A caller may supply a separate authorized, GET-only OKX
transport from its environment; the library **does not** yet include an OKX
authenticated REST signer or an OKX account-key validation workflow.

## Supported now

- `OkxPositionHistoryAdapter` implements the same public
  `ReadOnlyTradeAdapter` contract as Bybit.
- `instType=FUTURES` and `SWAP` with an explicitly provided OKX `instId`,
  settlement currency, and an explicitly qualified canonical Freqtrade pair.
- Only `type="2"` (**full-position close**) is emitted as a closed record.
  Partial closes and liquidation events are not silently promoted to complete
  economic trades; those need an extended adapter.
- Pagination uses OKX `after=uTime` to read older data, with duplicate-event,
  nonprogressing cursor, returned symbol/currency mismatch and invalid payload
  guards. The GET endpoint retains only recent history (currently last three
  months per OKX docs), therefore completed pagination **does not prove
  complete account history**.
- Native-signed fields: `price_pnl=pnl`,
  `trading_fees=fee` (OKX expense negative/rebate positive),
  `funding=fundingFee` (signed), `other_adjustments=liqPenalty +
  settledPnl` only if both are supplied, and
  `reported_net_pnl=realizedPnl` (already net). Missing components stay
  `None`; the reader refuses an inconsistent fully specified OKX equation.
- The `ccy` field is checked against the explicit settlement currency, but
  **OKX defines it as the margin currency**. Do not treat `ccy` alone as
  proof of settlement denomination; qualify `settleCcy` from current
  instrument metadata and the account product mode first. USD-margined
  products, cross/portfolio margin, and stablecoin conversion may have
  different semantics. No implicit USD/USDC/BTC conversion is performed.
- `trade_ref=posId` is a **source candidate**. It is NOT evidence that a
  specific Freqtrade bot trade owns this position. Do not map public numeric
  `match` to a verified trade identity.

### Example: synthetic transport

```python
from freqtrade_truth.adapters.okx_positions_history import OkxPositionHistoryAdapter

adapter = OkxPositionHistoryAdapter(
    my_authorized_get_only_transport,
    inst_id="BTC-USD-310404",
    inst_type="FUTURES",
    settlement_currency="USD",
    normalized_instrument="BTC/USD:USD-310404",
)
# Await adapter.fetch_closed_trades(ClosedTradeQuery(...)).
```

The named transport is an **integration placeholder**: the private MyOKX
reader is not part of the public package. An OSS user must supply its own
read-authorized OKX `JsonHttpTransport`; no turnkey authenticated OKX CLI
is claimed yet.

### Integration with private BOT OPS UI

The existing private `Bot2FinancialReconciliationService` stays the
**authoritative** source for MyOKX financial truth. The public position
history adapter is source-level numeric input only and must not replace the
strong private order/fill/position/bills identity checks, particularly OKX
stoploss `algoId` -> triggered `ordId` links and USDC-native valuation
unknowns. Any future bridge must be private, version pinned, read-only,
synthetically parity tested and first run in SHADOW mode.

## Official references

- https://www.okx.com/docs-v5/en/ — Get positions history, response fields and
  fee/funding/realizedPnL equation
- https://www.okx.com/docs-v5/trick_en/ — pagination parameters and ordering
- https://www.okx.com/docs-v5/en/ — Get instruments `settleCcy` specification

**Scope:** no real-account data, no production requests, no position ownership
certification, no final `v0.1` financial contract, no trading commands and no
release/cutover authorization.
