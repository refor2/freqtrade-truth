# First-run walkthrough

This walkthrough verifies the packaged project without requiring private data.

## 1. Install a development checkout

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 2. Run the offline synthetic checks

These tests use only synthetic fixtures and do not contact an exchange:

```bash
pytest tests/test_cli.py tests/test_bybit_adapter.py tests/test_bybit_auth.py \
  tests/test_bybit_market.py tests/test_bybit_preflight.py \
  tests/test_bybit_transaction_log.py
```

Then run the publication gates:

```bash
python scripts/public_safety_check.py
python scripts/commit_metadata_check.py
ruff check .
ruff format --check .
mypy
pytest
```

No live credentials are required for any repository test.

## 3. Run a real packaged offline demonstration

```bash
freqtrade-truth demo
freqtrade-truth demo --json
```

The command exercises the deterministic financial comparison core with
synthetic data, without network, credentials or an exchange account.
Its numerical `match` result is **not** a verified cross-source economic
trade match. See [offline demo](offline-demo.md) for exact examples and limits.

## 4. Optional credential-free public market check

The CLI can verify current public Bybit inverse-market metadata:

```bash
freqtrade-truth bybit-check
```

The command checks the configured inverse perpetual and prints only public market metadata.

A representative redacted shape is:

```text
Bybit smoke check: PASS
Mode: public
Market: BTCUSD / InversePerpetual / Trading
Settlement: BTC/USD -> BTC
Trading metadata: leverage <min>-<max>, min qty <qty>, qty step <step>, tick <tick>
Account access: skipped (public-only mode)
```

Values in documentation are illustrative. Current exchange metadata is read at runtime.

## 5. Optional authenticated read-only check

Use only a Bybit API key configured as read-only. Do not pass credentials as command-line arguments.

Bash:

```bash
export BYBIT_API_KEY="<local-read-only-key>"
export BYBIT_API_SECRET="<local-secret>"
freqtrade-truth bybit-check --authenticated
unset BYBIT_API_KEY BYBIT_API_SECRET
```

PowerShell:

```powershell
$env:BYBIT_API_KEY = "<local-read-only-key>"
$env:BYBIT_API_SECRET = "<local-secret>"
freqtrade-truth bybit-check --authenticated
Remove-Item Env:BYBIT_API_KEY
Remove-Item Env:BYBIT_API_SECRET
```

Authenticated mode:

1. accepts only documented official Bybit HTTPS API hosts,
2. verifies that Bybit reports the API key as read-only,
3. uses an authenticated transport restricted to the exact read endpoints required by this project,
4. reads a bounded recent closed-PnL window,
5. reads a bounded recent transaction-log window,
6. prints record counts rather than identifiers, balances, or financial amounts.

The CLI does not expose order placement, cancellation, leverage changes, transfers, or withdrawals.

## 6. Safe diagnostics

Do not paste authenticated command output, shell history, environment dumps, HTTP traces, exchange responses, or screenshots into public issues without reviewing them first.

Known CLI errors redact the configured Bybit credentials. Unexpected exceptions are reduced to the exception type at the CLI boundary.

A failed public preflight, failed read-only-key check, malformed response, disallowed host, disallowed authenticated endpoint, or transport error is a failure. The tool does not silently downgrade those conditions to success.
