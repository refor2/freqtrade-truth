# Independent Freqtrade / OKX numeric candidate (read-only)

`freqtrade-truth reconcile-okx` runs **two independent, bounded GET-only reads**:
the operator's Freqtrade closed-trade history and the OKX V5 position-history
endpoint. Neither side sends trade orders or changes exchange account state.

## Credentials and isolation

Use a separately provisioned **Read-only** OKX API key. Do not reuse a
production trading key. Keep credentials solely in your isolated local
environment (never in GitHub, issue comments, screenshots, logs or report
artifacts). Export the following variables only inside that environment:

- `OKX_READ_API_KEY`, `OKX_READ_API_SECRET`, `OKX_READ_API_PASSPHRASE`
- `FREQTRADE_READ_USERNAME`, `FREQTRADE_READ_PASSWORD`

For local Freqtrade, an HTTP origin is permitted only on loopback;
use HTTPS for a remote operator-controlled API. OKX is pinned to the official
region-specific HTTPS domains. Credentials are never emitted.

## Example

For an XPERP account with **independently qualified** USD settlement and
native USDC amounts (substitute your actual qualified instrument):

```sh
freqtrade-truth reconcile-okx \
  --freqtrade-url http://127.0.0.1:8080 \
  --region eea \
  --inst-id BTC-USD_UM_XPERP-310404 \
  --inst-type FUTURES \
  --settle USD \
  --pair BTC/USD:USD-310404 \
  --source-amount-ccy USDC \
  --isolated-xperp \
  --hours 24 \
  --limit 50 \
  --max-pages 20 \
  --close-seconds 60 \
  --net-tolerance 0.00000001 \
  --json
```

This is a public illustrative instrument identifier, **not proof that your
account owns a trade**. The XPERP preflight checks live public
`FUTURES/xperp/linear/USD` metadata before any signed OKX account GET.

## Exact semantics

- Only explicitly selected, canonical instrument and matching native
  settlement currency can be compared.
- Freqtrade and OKX values are compared using `Decimal`; comparison scope
  is **reported net PnL only**. The bot does not expose a source-proven
  independent price/fee decomposition at this stage.
- The OKX native position equation is audited separately. Unknown or
  contradictory native components block candidate equality.
- Positions are **time-window candidates**, not independently proven
  one-to-one trade ownership. Ambiguous components are not assigned.
- If the Freqtrade pair is `...:USD-YYMMDD` and the native OKX position
  amount is in `USDC`, this is a **currency mismatch**. The tool does not
  treat USD and USDC as equal or invent FX rates.
- Every query is bounded (hours, rows, number of pages); page exhaustion
  is an error. No query-window read establishes complete historical coverage.
- Any contradictory native currency in a bounded result invalidates candidate
  equality for that result; there is no implicit per-row FX conversion.
- `candidate_numeric_different` counts known out-of-tolerance comparisons,
  **not** review states caused solely by multiple competing bot records;
  ambiguous links remain incomplete/ambiguous and never become owned PnL.
- Output exposes only aggregate counts, never exchange account identifiers,
  order IDs, amounts, API keys or credentials. It cannot verify event
  ownership, funding attribution, full ledger coverage, or USD valuation.
- Even a `candidate_numeric_equal` result **does not** constitute
  verified account financial truth.

A real financial-truth claim requires separate account authentication,
proven position-to-trade links, complete order/fill/fee/funding ledger
coverage, signed native accounting and qualified currency valuation.

An OKX HTTP 401 or similar authentication error cannot be circumvented by
this library. Keep IP restrictions enabled; correct access in the operator's
own OKX API settings instead of reusing a live trading key.
