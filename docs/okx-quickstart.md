# OKX quick start — read-only native source check

**Audience:** Freqtrade operators who already have a separate OKX account and
want a reproducible *historical-position source* check, not automatic trading,
not a complete Freqtrade-to-exchange trade-identity audit.

## 1. Install from the public source repository

Requires Python 3.11–3.14. No private BOT OPS UI components:

```bash
git clone https://github.com/refor2/freqtrade-truth.git
cd freqtrade-truth
python -m pip install .
freqtrade-truth --help
freqtrade-truth demo --json  # no credentials or network
```

## 2. Confirm account region, product metadata and permissions

Check your **registered OKX account** domain:

| Region | `--region` | Official REST host |
| --- | --- | --- |
| European Economic Area (accounts registered via my.okx.com) | `eea` | `https://eea.okx.com` |
| Global (not EEA/US) | `global` | `https://www.okx.com` |
| United States | `us` | `https://us.okx.com` |

Create a **separate API key with Read permission only**, preferably IP-bound.
Do not reuse trading keys. Provision three environment variables through your
OS secret store or secure session, never as CLI flags, committed config,
shell-history text, shared logs or GitHub CI:

- `OKX_READ_API_KEY`
- `OKX_READ_API_SECRET`
- `OKX_READ_API_PASSPHRASE`

Also confirm instrument `instId`, `instType`, and `settleCcy` from OKX
instrument metadata and the correct Freqtrade canonical pair. The OKX
position-history `ccy` field is **margin currency**, not a universal proof
of settlement. This first reader safely rejects unsupported/mismatched
margin-vs-settlement cases rather than inventing USD equivalence.

## 3. Run source-only check

Example **only** for a verified BTC-settled BTC inverse SWAP (replace with
the exact instrument mapping for your account):

```bash
freqtrade-truth okx-check \
  --region eea \
  --inst-id BTC-USD-SWAP \
  --inst-type SWAP \
  --settle BTC \
  --pair BTC/USD:BTC \
  --hours 24 \
  --limit 50 \
  --json
```

The command makes **only signed GET requests** to the selected official OKX
`/api/v5/account/positions-history` endpoint. It does not modify anything
at OKX or inside Freqtrade. It summarizes fully closed source positions,
counts available reported PnL values, and emits an optional *native-currency
numeric preview* if every returned record has a reported value. It never
outputs raw position IDs, order IDs, per-trade amount rows or credentials.

The JSON **always** contains:

- `ownership_verified: false` — same instrument/time/amount does not prove
  that a Freqtrade trade owns the OKX position
- `history_complete: false` — endpoint is recent-history only (OKX documents
  the last three months), and a `--limit` may truncate the preview
- `key_permissions_independently_verified: false` — the signer has a
  GET-only allowlist but cannot inspect the API key's configured permissions

If no closed positions exist in the selected time window, the source read may
succeed with zero records; **that does not mean your strategy made no trades**.
If a value is missing, the preview total is `null`, not a fabricated zero.

## 4. Reconciliation against Freqtrade

The `okx-check` command is a **source reader**, not the final reconciliation
product. Do not label its numeric totals as verified trading results or
feed them into an execution or accounting workflow. For full economic
lifecycle ownership, compare Freqtrade order/fill IDs against OKX order,
execution, position and funding/fee ledger data with explicit source and
account provenance; that v0.1 contract is still being developed under
[issue #15](https://github.com/refor2/freqtrade-truth/issues/15).

The private BOT OPS UI bridge is a separate opt-in SHADOW-only integration.
Nothing here enables or changes BOT2/V891 execution.

## Source references

- [OKX official API V5 documentation](https://www.okx.com/docs-v5/en/)
- [OKX EEA API guide](https://my.okx.com/docs-v5/)
- [OKX EEA domain FAQ](https://www.okx.com/en-eu/help/api-faq-eea)
