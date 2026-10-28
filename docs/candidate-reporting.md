# Offline candidate reports — read-only library feature

This uses the existing `NormalizedTradeRecord`, `group_comparable_records`, and
`reconcile_group` algorithms. There is **no new accounting engine**, trading
method or exchange query in the reporting feature.

## Install and reproduce

```bash
python -m pip install .
freqtrade-truth demo --json
freqtrade-truth report --input examples/candidate-report.synthetic.json --format json
freqtrade-truth report --input examples/candidate-report.synthetic.json --format csv
freqtrade-truth report --input examples/candidate-report.synthetic.json --format html > report.html
freqtrade-truth serve --input examples/candidate-report.synthetic.json --port 8767
```

The final command serves only on **127.0.0.1**:
`/` (static HTML), `/report.json`, `/report.csv`, and `/health`.
Only GET works. There are **no POST/PUT/DELETE routes**, JavaScript, order
endpoints, accounts, persistence, cache writes or remote dependencies.

For programmatic use:

```python
from freqtrade_truth.api.reporting import build_report, as_json, as_csv, as_html
from freqtrade_truth.api.snapshot import parse_snapshot

# payload: strictly normalized local snapshot dict, not raw API secrets or logs
freqtrade, exchange, tolerance = parse_snapshot(payload)
report = build_report(freqtrade, exchange, tolerance=tolerance)
print(as_json(report))
```

## Input contract

One local UTF-8 JSON file, maximum **2 MB** and **1000 normalized records**,
with exact top-level keys:

- `schema`: `freqtrade-truth.candidate.v1`
- `freqtrade`, `exchange`: arrays of existing normalized-record fields
- `tolerance`: object containing optional integer `close_seconds` (0–86400)
  and optional string decimal tolerances (default zero).

Each record requires `record_id`, `source_name`, `trade_ref`,
`instrument`, `settlement_currency`, `closed_at` in UTC ISO8601.
Optional `opened_at` is also UTC. Optional financial fields must be **decimal
strings or null**: `price_pnl`, `trading_fees`, `funding`,
`other_adjustments`, `reported_net_pnl`. No amount may be supplied as
a JSON floating-point value. Missing is **not** zero. Empty inputs, foreign
fields, non-UTC timestamps, non-finite/oversized decimals and duplicates fail
closed. Source kind is established from the array, not trusted from arbitrary input.

Example: [synthetic candidate-report JSON](../examples/candidate-report.synthetic.json)
demonstrates three separate groups: one equal, one different and one incomplete.

## Safety and interpretation

All results intentionally expose two separate concepts:

- `numeric_status`: existing numerical comparison `match`, `review`,
  `incomplete`. A numeric `match` is **NOT** a verified trade match.
- `identity_status`: **always `unverified`**, even if numerical values,
  canonical instrument, settlement currency and close time match.
- financial_truth_status: always unverified, both in each row and top-level
  report. It cannot become verified via numeric equality or time grouping.

The top-level `verified_trade_identity` and
`verified_account_financial_truth` are **always false**. This feature does
not create or accept synthetic ownership proofs. An authoritative Freqtrade
order/OKX fill/position/ledger ownership contract is still under review
at [issue #15](https://github.com/refor2/freqtrade-truth/issues/15).

The default CLI report is **redacted** (no source IDs, trade IDs, symbols
or financial amounts). To explicitly inspect your own local normalized
values, pass `--include-values` with `report`. It reveals per-field values
in JSON and CSV on **your own machine**, and must not be uploaded to public
repos or included in issue screenshots. HTML remains summary-only.
The `serve` mode is always redacted and is loopback-only.

Historical XPERP USD-quoted instruments with native USDC amounts are **not**
converted here. No peg or current FX quote implies valid historical USD parity.
Any source-incomplete / ambiguous result remains incomplete or review. Reports
are not financial statements, compliance records, or proof of trading success.

## Release boundary

The reporting surface is operational and covered by synthetic, offline tests,
but **v0.1 verified financial truth is NOT released** until account-scoped
ownership and currency attribution gates in issue #15 pass. Existing Freqtrade,
This public package does not modify external trading systems or accounts.
