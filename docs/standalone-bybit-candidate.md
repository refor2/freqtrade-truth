# Independent read-only Bybit inverse candidate comparison

This is an independently installable, public Freqtrade Truth source comparison.
It works without proprietary deployment software, without any private bot strategy, and without
changing Freqtrade's engine, settings, positions or orders.

## Setup

Install the package with `python -m pip install .` on your own computer.
Enable your own Freqtrade REST server securely. Use `127.0.0.1` or an SSH
tunnel for HTTP; remote connections require HTTPS. Configure local environment
secrets `FREQTRADE_READ_USERNAME`, `FREQTRADE_READ_PASSWORD`, `BYBIT_API_KEY`,
`BYBIT_API_SECRET` through your OS secret manager. Use a separate Bybit API
key with strictly **Read-only** permission, preferably IP-restricted. Never
paste credentials into shell flags, commits, CI, issues or shared logs.

For an independently qualified Bybit BTC inverse perpetual:

```sh
freqtrade-truth reconcile-bybit --freqtrade-url http://127.0.0.1:8080 \
    --symbol BTCUSD --base BTC --quote USD --settle BTC \
    --hours 24 --limit 50 --max-pages 20 --close-seconds 60 --json
```

The command performs signed GET-only Bybit API permission introspection before
account queries. The public Bybit instrument metadata must agree with the
explicit symbol, base, quote and settlement. Both sources are bounded.
The Freqtrade source reads only `GET /api/v1/trades`, the Bybit private
source reads only `GET /v5/user/query-api` and
`GET /v5/position/closed-pnl`, and Bybit market metadata is public GET.
No POST, DELETE, PUT or order execution paths exist here.

## Optional stronger source order-reference review

Add `--check-order-links` to the same command. This reads each selected
Freqtrade closed trade through its documented read-only
`GET /api/v1/trade/<tradeid>` endpoint (never any Freqtrade POST/DELETE).
Only **positive-filled** exchange order IDs are compared with exact Bybit
closed-PnL `orderId` references. Zero-filled or unknown-filled orders cannot
produce candidates. Missing or malformed trade details fail the entire
command; a duplicate bot claim for one order is ambiguous.

The additional redacted `order_reference_review` field gives
`single_candidate`, `multiple_candidates`, `ambiguous`, `no_candidate`
and `checked_bot_trades`. It never contains order IDs, account IDs or
trade IDs. When the option is absent, this field is `null` and no
Freqtrade detail reads occur.

**This improves candidate identification, not verified trade or account
ownership.** A matching Freqtrade order and Bybit close-order reference does
not prove complete position lifecycle, lack of competing accounts, all
partial fills or correct funding attribution. Numerical closetime grouping
and exact order-reference candidate review remain separate evidence types;
the program never silently promotes either to a certified net PnL.

## Optional three-source execution and fee-ledger corroboration

Add **both** `--check-order-links --check-execution-ledger` to the same
`reconcile-bybit` command. This reuses the exact positive-filled Freqtrade
exchange order IDs and queries the official Bybit **GET**
`/v5/execution/list` for each bounded order and the native-currency Bybit
**GET** `/v5/account/transaction-log` for the declared bounded query
window. These endpoints require the separately permission-verified
read-only key. The execution records are checked against source order IDs,
symbol, unique execution IDs, positive fill quantities, timestamps,
source fee and currency. Candidate account-ledger records must have
compatible transaction ID, order ID, native fee and currency; conflicting,
missing or duplicate source records cannot yield positive candidates.
One shared total Bybit page budget applies to all these endpoint reads.
All authenticated evidence GETs are also checked *before I/O* for an
allowlisted parameter set, explicit native instrument/order/currency scope,
finite source-page size and nonnegative millisecond query window below seven
days. A malformed or widened request fails instead of fetching additional
account data. This is request-scoping hardening, **not** evidence of complete
account history, ownership or accounting correctness.

The returned `execution_ledger_review` contains only redacted counts:
`checked_orders`, `ledger_records_in_window`,
`candidate_trades`, `ambiguous_trades` and `no_candidate_trades`.
An unqualified or truncated source read **fails closed**.
Without this opt-in, the exchange execution and ledger requests do not run.

This is **source-level corroboration, not proven bot/account/position
ownership**. Bybit execution `execId` and account-ledger `tradeId`
might not share the same identifier namespace across all account modes.
Even matching IDs and fees do not prove account exclusivity, all partial
fills, historical retention or funding ownership. The output must not be
marketed as complete/verified net PnL. Source history older than the
declared window remains unknown.

## Interpretation

- `candidate_numeric_equal`: two exactly grouped records with reported
  numeric net amounts within the configured tolerance. **Never verified
  ownership or confirmed accounting.**
- `candidate_numeric_different`: comparable numeric values disagree.
- `unverified_or_ambiguous_groups`: missing records, missing net fields,
  many-to-many temporal links, or unsupported group structure.
- A source limit boundary or failed source query yields a command failure
  instead of a partial numeric result.
- The explicit financial_truth_status is always unverified in this candidate
  adapter regardless of arithmetic, order IDs or execution/ledger candidates.
- Exchange account scope, bot exclusivity, order/fill linkage, all funding
  ownership, full history and USD conversion are **not verified**.
- By default no financial amounts, trade IDs, pairs, account IDs,
  credentials or infrastructure are printed. Operators can add
  `--include-values` to emit **source-only** window native-currency totals
  to their own private stdout; do **not** upload these to public CI/issues.
  If any required source amount is missing, the corresponding total is null;
  there is no invented zero or USD parity claim.

Timestamps are **candidate grouping hints only**. Bybit closed-position PnL
may use different lifecycle boundaries from Freqtrade trades; a candidate
numeric equality cannot establish the same economic position. This v0.1
preview supports **Bybit inverse perpetual only**, not linear contracts.
It is an informational source reconciliation, not an independent accounting
statement, PnL certification or trading control.
