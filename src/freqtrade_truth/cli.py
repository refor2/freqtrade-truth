"""Command-line smoke checks for public and authenticated read-only integrations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TextIO
from urllib.parse import urlsplit

from freqtrade_truth.adapters.bybit import (
    BybitInverseClosedPnlAdapter,
    BybitResponseError,
)
from freqtrade_truth.adapters.bybit_auth import BybitV5HmacTransport
from freqtrade_truth.adapters.bybit_market import (
    BybitMarketPreflightError,
    BybitV5PublicTransport,
    verify_bybit_inverse_perpetual,
)
from freqtrade_truth.adapters.bybit_preflight import (
    BybitApiKeySafetyError,
    verify_bybit_read_only_key,
)
from freqtrade_truth.adapters.bybit_transaction_log import (
    BybitInverseTransactionLogReader,
    BybitTransactionLogError,
    BybitTransactionLogQuery,
)
from freqtrade_truth.adapters.http import HttpTransportError
from freqtrade_truth.core.models import ClosedTradeQuery

_DEFAULT_BYBIT_BASE_URL = "https://api.bybit.com"
_MAX_SMOKE_WINDOW_HOURS = 168
_MAX_RECORD_LIMIT = 100
_OFFICIAL_BYBIT_API_HOSTS = frozenset(
    {
        "api.bybit.com",
        "api.bytick.com",
        "api-testnet.bybit.com",
        "api-demo.bybit.com",
        "api.bybit.tr",
        "api.bybit.kz",
        "api.bybitgeorgia.ge",
        "api.bybit.ae",
        "api.bybit.eu",
        "api.bybit.id",
        "api.manepa.jp",
        "api-testnet.manepa.jp",
        "api.spark-fintech.com",
        "api-testnet.spark-fintech.com",
    }
)


@dataclass(frozen=True, slots=True)
class BybitCheckConfig:
    """Validated configuration for one Bybit smoke check."""

    symbol: str
    base_coin: str
    quote_coin: str
    settle_coin: str
    base_url: str
    authenticated: bool
    hours: int
    limit: int
    json_output: bool

    def __post_init__(self) -> None:
        for field_name in ("symbol", "base_coin", "quote_coin", "settle_coin"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or value != value.upper():
                raise ValueError(f"{field_name} must be a non-empty uppercase value")

        if self.hours <= 0 or self.hours > _MAX_SMOKE_WINDOW_HOURS:
            raise ValueError(f"hours must be between 1 and {_MAX_SMOKE_WINDOW_HOURS}")
        if self.limit <= 0 or self.limit > _MAX_RECORD_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_RECORD_LIMIT}")


async def run_bybit_check(
    config: BybitCheckConfig,
    *,
    environ: Mapping[str, str],
) -> dict[str, object]:
    """Run a public market preflight and optional authenticated read-only smoke check."""

    if config.authenticated:
        _require_official_bybit_base_url(config.base_url)

    public_transport = BybitV5PublicTransport(config.base_url)
    market = await verify_bybit_inverse_perpetual(
        public_transport,
        symbol=config.symbol,
        expected_base_coin=config.base_coin,
        expected_quote_coin=config.quote_coin,
        expected_settle_coin=config.settle_coin,
    )

    result: dict[str, object] = {
        "status": "ok",
        "mode": "public",
        "market": {
            "symbol": market.symbol,
            "contract_type": market.contract_type,
            "status": market.status,
            "base_coin": market.base_coin,
            "quote_coin": market.quote_coin,
            "settle_coin": market.settle_coin,
            "min_leverage": str(market.min_leverage),
            "max_leverage": str(market.max_leverage),
            "leverage_step": str(market.leverage_step),
            "min_order_qty": str(market.min_order_qty),
            "qty_step": str(market.qty_step),
            "tick_size": str(market.tick_size),
        },
    }

    if not config.authenticated:
        return result

    api_key = environ.get("BYBIT_API_KEY", "")
    api_secret = environ.get("BYBIT_API_SECRET", "")
    if not api_key or not api_secret:
        raise ValueError(
            "authenticated mode requires BYBIT_API_KEY and BYBIT_API_SECRET environment variables"
        )

    authenticated_transport = BybitV5HmacTransport(
        config.base_url,
        api_key=api_key,
        api_secret=api_secret,
    )
    await verify_bybit_read_only_key(authenticated_transport)

    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(hours=config.hours)

    closed_reader = BybitInverseClosedPnlAdapter(
        authenticated_transport,
        symbol=config.symbol,
        settlement_currency=config.settle_coin,
    )
    closed_records = await closed_reader.fetch_closed_trades(
        ClosedTradeQuery(
            closed_from=window_start,
            closed_until=window_end,
            instrument=config.symbol,
            limit=config.limit,
        )
    )

    ledger_reader = BybitInverseTransactionLogReader(
        authenticated_transport,
        symbol=config.symbol,
        settlement_currency=config.settle_coin,
    )
    ledger_records = await ledger_reader.fetch(
        BybitTransactionLogQuery(
            start_at=window_start,
            end_at=window_end,
            limit=config.limit,
        )
    )

    result["mode"] = "authenticated-read-only"
    result["authenticated"] = {
        "read_only_key_verified": True,
        "window_hours": config.hours,
        "closed_pnl_records": len(closed_records),
        "transaction_log_records": len(ledger_records),
        "record_limit": config.limit,
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    """Build the public CLI parser."""

    parser = argparse.ArgumentParser(
        prog="freqtrade-truth",
        description="Read-only financial verification tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bybit = subparsers.add_parser(
        "bybit-check",
        help="Run a read-only Bybit inverse market and account smoke check.",
    )
    bybit.add_argument("--symbol", default="BTCUSD")
    bybit.add_argument("--base", default="BTC", dest="base_coin")
    bybit.add_argument("--quote", default="USD", dest="quote_coin")
    bybit.add_argument("--settle", default="BTC", dest="settle_coin")
    bybit.add_argument("--base-url", default=_DEFAULT_BYBIT_BASE_URL)
    bybit.add_argument(
        "--authenticated",
        action="store_true",
        help=(
            "Also verify a read-only API key and read recent account records. "
            "Credentials are accepted only through BYBIT_API_KEY and BYBIT_API_SECRET."
        ),
    )
    bybit.add_argument(
        "--hours",
        type=int,
        default=24,
        help=f"Authenticated read window in hours (1-{_MAX_SMOKE_WINDOW_HOURS}).",
    )
    bybit.add_argument(
        "--limit",
        type=int,
        default=50,
        help=f"Maximum records per authenticated reader (1-{_MAX_RECORD_LIMIT}).",
    )
    bybit.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit the redacted smoke-check summary as JSON.",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process-style exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    env = environ if environ is not None else os.environ

    if args.command != "bybit-check":
        parser.error("unsupported command")

    try:
        config = BybitCheckConfig(
            symbol=str(args.symbol),
            base_coin=str(args.base_coin),
            quote_coin=str(args.quote_coin),
            settle_coin=str(args.settle_coin),
            base_url=str(args.base_url),
            authenticated=bool(args.authenticated),
            hours=int(args.hours),
            limit=int(args.limit),
            json_output=bool(args.json_output),
        )
        result = asyncio.run(run_bybit_check(config, environ=env))
    except (
        BybitApiKeySafetyError,
        BybitMarketPreflightError,
        BybitResponseError,
        BybitTransactionLogError,
        HttpTransportError,
        ValueError,
    ) as exc:
        print(f"ERROR: {_redact_message(str(exc), env)}", file=err)
        return 2
    except Exception as exc:  # pragma: no cover - defensive redaction boundary
        print(f"ERROR: unexpected {type(exc).__name__}", file=err)
        return 2

    if config.json_output:
        print(json.dumps(result, sort_keys=True), file=out)
    else:
        _print_human_summary(result, out)

    return 0


def _print_human_summary(result: Mapping[str, object], out: TextIO) -> None:
    market_value = result.get("market")
    if not isinstance(market_value, dict):
        raise ValueError("smoke-check result is missing market summary")

    market = market_value
    print("Bybit smoke check: PASS", file=out)
    print(f"Mode: {result.get('mode')}", file=out)
    print(
        f"Market: {market.get('symbol')} / {market.get('contract_type')} / {market.get('status')}",
        file=out,
    )
    print(
        "Settlement: "
        f"{market.get('base_coin')}/{market.get('quote_coin')} -> {market.get('settle_coin')}",
        file=out,
    )
    print(
        "Trading metadata: "
        f"leverage {market.get('min_leverage')}-{market.get('max_leverage')}, "
        f"min qty {market.get('min_order_qty')}, qty step {market.get('qty_step')}, "
        f"tick {market.get('tick_size')}",
        file=out,
    )

    auth_value = result.get("authenticated")
    if isinstance(auth_value, dict):
        print("API key: read-only verified", file=out)
        print(
            "Recent records: "
            f"closed PnL={auth_value.get('closed_pnl_records')}, "
            f"transaction log={auth_value.get('transaction_log_records')}",
            file=out,
        )
    else:
        print("Account access: skipped (public-only mode)", file=out)


def _require_official_bybit_base_url(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _OFFICIAL_BYBIT_API_HOSTS
        or parsed.port is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("authenticated mode requires an official Bybit HTTPS API base URL")


def _redact_message(message: str, environ: Mapping[str, str]) -> str:
    redacted = message
    for variable in ("BYBIT_API_KEY", "BYBIT_API_SECRET"):
        value = environ.get(variable, "")
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
