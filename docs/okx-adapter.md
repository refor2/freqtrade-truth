# OKX V5: read-only full-position close history (first public adapter)

This module adds an **independent OKX adapter** while retaining Bybit. It reads
`GET /api/v5/account/positions-history` only through the injected
`JsonHttpTransport` interface. No account credentials or production connection
are needed for tests. The optional `OkxV5ReadOnlyHmacTransport` now signs requests **only** for
that exact GET path and only for official, explicitly supported OKX HTTPS hosts:
`www.okx.com` (Global), `eea.okx.com` (EEA), and `us.okx.com` (US).
Callers must independently issue a Read-only key and confirm the exact
regional OKX endpoint they are allowed to use. The public package does
not verify an account key's permission flags. The authenticated
`freqtrade-truth okx-check` source-preview CLI is described in
[OKX quick start](okx-quickstart.md).

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

Example with a separately created **Read-permission** OKX key and a verified
instrument/settlement mapping (synthetic identifiers shown):

```python
import os
from freqtrade_truth.adapters.okx_auth import OkxV5ReadOnlyHmacTransport
from freqtrade_truth.adapters.okx_positions_history import OkxPositionHistoryAdapter

transport = OkxV5ReadOnlyHmacTransport(
    api_key=os.environ["OKX_READ_API_KEY"],
    api_secret=os.environ["OKX_READ_API_SECRET"],
    api_passphrase=os.environ["OKX_READ_API_PASSPHRASE"],
)
adapter = OkxPositionHistoryAdapter(
    transport,
    inst_id="BTC-USD-SWAP",
    inst_type="SWAP",
    settlement_currency="BTC",
    normalized_instrument="BTC/USD:BTC",
)
# await adapter.fetch_closed_trades(ClosedTradeQuery(...))
```

The signer supports **only** `GET /api/v5/account/positions-history`.
The CLI `freqtrade-truth okx-check` builds that signed reader from three
environment variables and explicit `--region`/`--inst-id`/`--settle`/`--pair`
inputs. It prints an aggregate source preview, not trade/account ownership.
Any attempt to use a trade/withdrawal path, URL credentials, redirects or
non-official host is rejected; no POST support exists. It is still not
capable of proving that the key itself has only Read permissions, so
use an independently configured read-only account key and appropriate
IP restrictions. The authenticated source endpoint is not accessible from
every location. A 2026-10-21 public `/api/v5/public/instruments` smoke request
from the project's development NUC returned HTTP 403, so **no successful
live OKX account-read claim is made**.
The package does not ship account-specific private adapters.

### Integration boundary

Operators must verify provenance, source permissions and exact
position/order/fill/bill ownership independently. This source
adapter cannot certify financial settlement, control trading, or substitute
for a proven account-native reconciler.

## Official references

- https://www.okx.com/docs-v5/en/ — Get positions history, response fields and
  fee/funding/realizedPnL equation
- https://www.okx.com/docs-v5/trick_en/ — pagination parameters and ordering
- https://www.okx.com/docs-v5/en/ — Get instruments `settleCcy` specification

**Scope:** no real-account data, no production requests, no position ownership
certification, no final `v0.1` financial contract, no trading commands and no
release/cutover authorization.
