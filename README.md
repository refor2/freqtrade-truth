# Freqtrade Truth

**Freqtrade Truth** is an open-source, read-only financial reconciliation toolkit for Freqtrade deployments.

Its goal is simple: make it easier to verify whether bot-side trade results match exchange-side financial settlement after fees, funding, and other supported adjustments are normalized.

> Status: **pre-alpha preview**. Freqtrade and the first Bybit inverse read paths are implemented and test-covered. Deterministic grouping and normalized field comparison are implemented; exchange-ledger enrichment and reporting are still incomplete. Reconciliation status names, reason codes, attribution rules, and reporting contracts remain provisional until the [v0.1 contract-stabilization gate](https://github.com/refor2/freqtrade-truth/issues/15) is complete. Do not use this preview as the sole source for accounting or trading decisions.

## Read recent OKX history (read-only, EEA supported)

After installing the package, a Freqtrade operator can run `okx-check`
without writing Python. It supports official account-region domains including
`eea.okx.com`, accepts a separately configured **Read-only** API key via
environment variables and returns a redacted native-currency source preview.
No trading endpoints or Freqtrade strategy changes are involved.
See [OKX first-run instructions](docs/okx-quickstart.md) and
[OKX source-adapter limits](docs/okx-adapter.md). **Position-history numeric
values do not prove trade ownership or account-wide financial correctness.**

## Read-only funding candidate reconciliation (0.0.6 candidate)

`freqtrade-truth okx-native-report` now includes a separate
`funding_candidate_states` count. It compares account funding bill **candidates**
with native position-history funding; it validates exact instrument, currency,
bill ID, event time, subtypes 173/174, signed values and cross-cycle conflicts.
Repeat occurrences of one immutable bill ID are not double counted.

The result `candidate_sum_equal` is **not** verified bill ownership. The
report always preserves `funding_ownership_verified=false`,
`funding_ledger_coverage_verified=false` and `usd_verified=false`. A missing
source event never becomes zero. Existing source-native and fee checks remain
independent.

Example: `freqtrade-truth okx-native-report --input examples/okx-fee-evidence.synthetic.json`

The funding candidate checks are exercised with public synthetic examples. Financial Truth v0.1 remains open.

## OKX fee-component evidence (0.0.5 candidate)

Read-only `okx-native-report` now distinguishes two separate financial claims:

- `fee_fill_position_states`: exact source-native sum of signed fees from
  every confirmed OKX fill versus signed fee in the uniquely identified
  position-history row. This does **not** require each accounting bill.
- `fee_ledger_states`: **additional** exact fill-to-bill ledger coverage,
  requiring every fill to have one uniquely identified same-currency bill.
  Missing bills remain `incomplete` even if fill fees match position fees.

Both checks reuse the existing strict position-cycle verifier. They do not
guess an account owner, fill the gap with zero, infer funding by time, or
convert USDC to USD. Reports are redacted aggregates only.

Try the included synthetic mixed-evidence sample:

```sh
freqtrade-truth okx-native-report --input examples/okx-fee-evidence.synthetic.json
```

Synthetic fee-ledger verification demonstrates that incomplete bills and unidentified cycles remain unknown. See [fee verification scope](docs/okx-native-verification.md).

## Read-only OKX native-source verification (0.0.4 candidate)

The installed CLI can now verify source-native OKX position-cycle arithmetic on
local **sanitized GET-only evidence** while preserving rejected ambiguity.
This adds NO trading engine, live integration, 1-second fuzzy matching or USD
conversion:

```bash
freqtrade-truth okx-native-report --input examples/okx-native.synthetic.json
```

Expected synthetic counts: 2 consistent, 1 different, 1 unknown. The output
never exposes private IDs, fills or amounts, and explicitly marks account/FX
verification as false. See [proof and limitations](docs/okx-native-verification.md).

## Ready-to-use offline reports (0.0.3 candidate)

The local report interface is operational, read-only and uses the existing
normalization/reconciliation core. It accepts normalized data (not raw exchange
credentials), and emits **redacted by default** JSON, CSV or static HTML.
There is also a **localhost-only GET report API**:

```bash
freqtrade-truth report --input examples/candidate-report.synthetic.json --format json
freqtrade-truth report --input examples/candidate-report.synthetic.json --format csv
freqtrade-truth report --input examples/candidate-report.synthetic.json --format html > report.html
freqtrade-truth serve --input examples/candidate-report.synthetic.json --port 8767
```

This is a functioning numerical comparison/report library, **not** a finished
authoritative financial truth engine: the explicit `numeric_status=match`
is **not an identity-verified match**. All reports expose
`verified_trade_identity=false`. See [reporting usage and schema](docs/candidate-reporting.md)
and [identity review issue #15](https://github.com/refor2/freqtrade-truth/issues/15).

## Connect an independent Freqtrade bot (source preview)

The public package has a standalone `freqtrade-check` command that contacts
an operator-supplied Freqtrade REST endpoint using GET only. For HTTP, it
accepts literal loopback origins only; remote installations require HTTPS.
Optional Basic credentials come from private environment variables
`FREQTRADE_READ_USERNAME` and `FREQTRADE_READ_PASSWORD`.

```sh
freqtrade-truth freqtrade-check --url http://127.0.0.1:8080 --hours 24 --limit 50 --json
```

It returns only redacted source counts. It does **not** claim complete
history, exchange trade ownership or verified PnL. No private UI, trading
engine changes or proprietary strategy code is required. See the
[standalone first-run walkthrough](docs/first-run.md).

## Independent two-source Bybit inverse numeric comparison

A standalone Freqtrade operator can now compare a bounded recent Freqtrade
closed-trade window to Bybit inverse closed-PnL, using private read-only Bybit
API credentials and optionally Freqtrade Basic Auth credentials held only in
their own local environment (never in public GitHub or shell arguments):

```sh
freqtrade-truth reconcile-bybit --freqtrade-url http://127.0.0.1:8080 \
  --symbol BTCUSD --base BTC --quote USD --settle BTC --hours 24 --json
# Optional: additionally compare exact positive-filled order references
freqtrade-truth reconcile-bybit --freqtrade-url http://127.0.0.1:8080 \
  --symbol BTCUSD --base BTC --quote USD --settle BTC --check-order-links --json
# Optional: include bounded Bybit execution/transaction-ledger corroboration
freqtrade-truth reconcile-bybit --freqtrade-url http://127.0.0.1:8080 \
  --symbol BTCUSD --base BTC --quote USD --settle BTC \
  --check-order-links --check-execution-ledger --json
```

The command validates Bybit's read-only API-key permission, verifies public
inverse product metadata, reads both sources via GET only, and emits
redacted *candidate numeric agreement, mismatch and unknown counts*.
Partial result limits fail closed rather than producing an incomplete match.
**It is not a verified economic trade ownership or accounting/PnL report.**
Bybit closed-PnL rows and Freqtrade trades can represent different lifecycle
units, funding remains unassigned, and observed numerical equality is
not ownership proof. See [standalone runbook](docs/standalone-bybit-candidate.md).

## Try it in one minute — entirely offline

```bash
python -m pip install -e .
freqtrade-truth demo
freqtrade-truth demo --json
```

The demo uses synthetic records only; no credentials or exchange access. It
shows numerical `match`, `review` and `incomplete` examples. **A numeric
`match` is not proof of matching economic trade/account ownership**; the
public v0.1 identity and attribution contract is not yet finalized.
See [the offline demonstration](docs/offline-demo.md).

## Why

Trading systems often expose several financial views at once: strategy results, bot trade records, exchange fills, fees, funding, and realized settlement. Small differences can accumulate or remain unnoticed.

Freqtrade Truth is intended to provide a narrow audit layer that:

- reads bot-side financial records,
- reads exchange-side settlement records,
- normalizes them into a common model,
- highlights unexplained differences,
- keeps the workflow read-only.

It is **not** a trading strategy, signal engine, execution engine, or portfolio manager.

## Safety principles

This repository is public by design.

- Read-only integrations only.
- Synthetic data only in tests, examples, screenshots, and documentation.
- No production credentials, account identifiers, infrastructure details, private logs, or real trade history.
- No proprietary trading strategies, signals, private risk logic, or unpublished research.
- If information is not clearly safe for public release, it does not belong here.

See [SECURITY.md](SECURITY.md) for the mandatory publication policy and [docs/threat-model.md](docs/threat-model.md) for the technical threat model.

## Current foundation

The foundation milestone defines contracts before integrations:

- immutable normalized trade records,
- `Decimal` monetary values,
- timezone-aware UTC timestamps,
- explicit missing values instead of silent zero defaults,
- capability-aware read-only adapter protocol,
- GET-only Freqtrade closed-trade adapter with bounded pagination,
- Bybit V5 inverse closed-PnL adapter with cursor pagination and seven-day window splitting,
- OKX V5 FUTURES/SWAP full-position-close history read adapter with native-currency normalization, duplicate/pagination validation and explicitly unverified ownership (single-endpoint authenticated GET-only OKX signer also available; must use a separately issued Read-permission key),
- GET-only Bybit V5 HMAC authenticated transport for runtime read access,
- fail-closed Bybit API-key preflight requiring read-only mode,
- Bybit inverse transaction-log ledger reader for funding, fees, cash flow, and reported balance change,
- credential-free Bybit inverse market preflight and Trading-symbol discovery,
- dependency-free HTTP transport with optional Basic Auth,
- bounded 429/503 retry policy with Retry-After support,
- synthetic fixtures,
- Python 3.11–3.14 CI,
- linting, formatting, strict typing, tests, and publication-safety checks.

See [docs/data-model.md](docs/data-model.md), [docs/reconciliation.md](docs/reconciliation.md), [docs/freqtrade-adapter.md](docs/freqtrade-adapter.md), [docs/bybit-adapter.md](docs/bybit-adapter.md), [docs/okx-adapter.md](docs/okx-adapter.md), [docs/compatibility.md](docs/compatibility.md), [docs/versioning.md](docs/versioning.md), [docs/first-run.md](docs/first-run.md), [docs/dependency-audit.md](docs/dependency-audit.md), and [ROADMAP.md](ROADMAP.md).

## Planned v0.1 scope

- Freqtrade read adapter.
- One exchange read adapter.
- Normalized financial record model.
- Fee and funding reconciliation.
- Tolerance-based mismatch detection.
- Synthetic fixtures.
- Minimal API and web view.
- CSV / JSON export.
- Automated tests and public-safety checks.

## Architecture

```text
Freqtrade adapter ─┐
                   ├──> normalization core ──> reconciliation ──> report
Exchange adapter ──┘
```

The core remains independent from any trading strategy.

See [docs/architecture.md](docs/architecture.md).

## Development

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/public_safety_check.py
ruff check .
ruff format --check .
mypy
pytest
```

On Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python scripts/public_safety_check.py
ruff check .
ruff format --check .
mypy
pytest
```

## Bybit smoke check

After installation, a public credential-free check can verify the current inverse market contract:

```bash
freqtrade-truth bybit-check
```

The default output is intentionally redacted and contains market metadata only.

Authenticated read-only account checks are opt-in. Credentials are accepted only through environment variables, never CLI flags:

```bash
export BYBIT_API_KEY="..."
export BYBIT_API_SECRET="..."
freqtrade-truth bybit-check --authenticated
```

On PowerShell:

```powershell
$env:BYBIT_API_KEY = "..."
$env:BYBIT_API_SECRET = "..."
freqtrade-truth bybit-check --authenticated
```

Authenticated mode accepts only documented official Bybit API hosts, first verifies that Bybit reports the key as read-only, then reads a bounded recent window from closed-PnL and transaction-log endpoints. It reports record counts only; it does not print trade IDs, order IDs, financial amounts, balances, or credentials.

Use `--json` for the same redacted summary in machine-readable form. The smoke CLI has no order-placement, cancellation, leverage, transfer, or withdrawal path.

## Contributing

Contributions are welcome once they satisfy the public-safety gate described in [CONTRIBUTING.md](CONTRIBUTING.md).

Public changes are tracked in [CHANGELOG.md](CHANGELOG.md). Release steps are documented in [RELEASING.md](RELEASING.md).

## License

MIT. See [LICENSE](LICENSE).

## Project relationship

Freqtrade Truth is an independent community project and is not affiliated with or endorsed by the Freqtrade project or any exchange.


### Bill source coverage diagnosis (0.0.7)

`okx-native-report` also contains `bill_query_window_coverage`.
A missing fill-to-bill link is **not evidence that the bill does not exist**
when the original `trade_window` excluded the fill. The new result
counts missing-before, missing-inside, and missing-after window separately.
It never asserts complete ledger coverage, even when timestamps fall inside.

A complete bill window may differ from the operational trade window.
An upstream **read-only OKX adapter** should explicitly report the fetched
`bill_query_window`, separately from its `trade_window`.
This report uses `bill_query_window` when present and retains the older field
only for backwards-compatible source snapshots.
