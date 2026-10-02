# Freqtrade Truth

**Freqtrade Truth** is an open-source, read-only financial reconciliation toolkit for Freqtrade deployments.

Its goal is simple: make it easier to verify whether bot-side trade results match exchange-side financial settlement after fees, funding, and other supported adjustments are normalized.

> Status: **pre-alpha preview**. Freqtrade and the first Bybit inverse closed-PnL read path are implemented and test-covered; reconciliation is not yet implemented. Do not use this preview as the sole source for accounting or trading decisions.

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
- GET-only Bybit V5 HMAC authenticated transport for runtime read access,
- fail-closed Bybit API-key preflight requiring read-only mode,
- dependency-free HTTP transport with optional Basic Auth,
- bounded 429/503 retry policy with Retry-After support,
- synthetic fixtures,
- Python 3.11–3.14 CI,
- linting, formatting, strict typing, tests, and publication-safety checks.

See [docs/data-model.md](docs/data-model.md), [docs/freqtrade-adapter.md](docs/freqtrade-adapter.md), [docs/bybit-adapter.md](docs/bybit-adapter.md), [docs/compatibility.md](docs/compatibility.md), [docs/versioning.md](docs/versioning.md), and [ROADMAP.md](ROADMAP.md).

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

## Contributing

Contributions are welcome once they satisfy the public-safety gate described in [CONTRIBUTING.md](CONTRIBUTING.md).

Public changes are tracked in [CHANGELOG.md](CHANGELOG.md). Release steps are documented in [RELEASING.md](RELEASING.md).

## License

MIT. See [LICENSE](LICENSE).

## Project relationship

Freqtrade Truth is an independent community project and is not affiliated with or endorsed by the Freqtrade project or any exchange.
