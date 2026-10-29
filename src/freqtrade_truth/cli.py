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
from freqtrade_truth.adapters.freqtrade import FreqtradeResponseError
from freqtrade_truth.adapters.http import HttpTransportError
from freqtrade_truth.adapters.okx_positions_history import OkxPositionHistoryError
from freqtrade_truth.bybit_candidate import BybitCandidateConfig, compare_bybit_inverse
from freqtrade_truth.core.models import ClosedTradeQuery
from freqtrade_truth.freqtrade_cli import FreqtradeCheckConfig, run_freqtrade_check
from freqtrade_truth.offline_demo import build_offline_demo
from freqtrade_truth.okx_cli import OkxCheckConfig, run_okx_check
from freqtrade_truth.okx_instruments import OkxInstrumentPreflightError

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
        normalized_instrument=market.normalized_instrument,
    )
    closed_records = await closed_reader.fetch_closed_trades(
        ClosedTradeQuery(
            closed_from=window_start,
            closed_until=window_end,
            instrument=market.normalized_instrument,
            limit=config.limit,
        )
    )

    ledger_reader = BybitInverseTransactionLogReader(
        authenticated_transport,
        symbol=config.symbol,
        settlement_currency=config.settle_coin,
        normalized_instrument=market.normalized_instrument,
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

    demo = subparsers.add_parser(
        "demo", help="Offline synthetic numeric reconciliation demo; not verified financial truth."
    )
    demo.add_argument("--json", action="store_true", dest="json_output")

    freqtrade = subparsers.add_parser(
        "freqtrade-check",
        help="Read a bounded, redacted closed-trade preview from your own Freqtrade API.",
    )
    freqtrade.add_argument("--url", default="http://127.0.0.1:8080")
    freqtrade.add_argument("--hours", type=int, default=24)
    freqtrade.add_argument("--limit", type=int, default=50)
    freqtrade.add_argument("--max-pages", type=int, default=20)
    freqtrade.add_argument("--json", action="store_true", dest="json_output")

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

    okx = subparsers.add_parser(
        "okx-check",
        help="Read recent closed OKX positions from a separately provisioned Read-only key.",
    )
    okx.add_argument("--region", required=True, help="eea, global, or us (account region).")
    okx.add_argument("--inst-id", required=True, help="Native OKX instrument, e.g. BTC-USD-SWAP.")
    okx.add_argument("--inst-type", required=True, help="SWAP or FUTURES.")
    okx.add_argument("--settle", required=True, help="Qualified native settlement currency.")
    okx.add_argument("--pair", required=True, help="Exact canonical Freqtrade pair.")
    okx.add_argument(
        "--source-amount-ccy",
        default=None,
        help="Actual OKX position-history monetary unit, e.g. USDC for isolated XPERP.",
    )
    okx.add_argument(
        "--isolated-xperp",
        action="store_true",
        help="Explicit special case: isolated USD-settled XPERP with native USDC amounts.",
    )
    okx.add_argument("--hours", default="24", help="Read window in hours, 1..2160.")
    okx.add_argument("--limit", default="50", help="Maximum returned rows, 1..100.")
    okx.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print non-identifying source preview JSON.",
    )

    reconcile = subparsers.add_parser(
        "reconcile-bybit",
        help="Standalone read-only Freqtrade/Bybit inverse numeric candidate report.",
    )
    reconcile.add_argument("--freqtrade-url", default="http://127.0.0.1:8080")
    reconcile.add_argument("--symbol", default="BTCUSD")
    reconcile.add_argument("--base", default="BTC")
    reconcile.add_argument("--quote", default="USD")
    reconcile.add_argument("--settle", default="BTC")
    reconcile.add_argument("--hours", type=int, default=24)
    reconcile.add_argument("--limit", type=int, default=50)
    reconcile.add_argument("--max-pages", type=int, default=20)
    reconcile.add_argument("--close-seconds", type=int, default=60)
    reconcile.add_argument("--net-tolerance", default="0.00000001")
    reconcile.add_argument("--json", action="store_true", dest="json_output")
    reconcile.add_argument(
        "--check-order-links",
        action="store_true",
        help="Opt-in Freqtrade trade-detail GET and positive-filled order-ID candidate review.",
    )
    reconcile.add_argument(
        "--check-execution-ledger",
        action="store_true",
        help="Opt-in Bybit execution/ledger fee candidate checks; requires --check-order-links.",
    )
    reconcile.add_argument(
        "--include-values",
        action="store_true",
        help="Explicitly expose potentially private source-window amount totals on stdout.",
    )

    reconcile_okx = subparsers.add_parser(
        "reconcile-okx",
        help="Bounded, redacted Freqtrade/OKX read-only numeric candidate audit.",
    )
    reconcile_okx.add_argument("--freqtrade-url", default="http://127.0.0.1:8080")
    reconcile_okx.add_argument("--region", required=True, choices=("eea", "global", "us"))
    reconcile_okx.add_argument("--inst-id", required=True)
    reconcile_okx.add_argument("--inst-type", required=True, choices=("SWAP", "FUTURES"))
    reconcile_okx.add_argument("--settle", required=True)
    reconcile_okx.add_argument("--pair", required=True)
    reconcile_okx.add_argument("--source-amount-ccy", default=None)
    reconcile_okx.add_argument("--isolated-xperp", action="store_true")
    reconcile_okx.add_argument("--hours", type=int, default=24)
    reconcile_okx.add_argument("--limit", type=int, default=50)
    reconcile_okx.add_argument("--max-pages", type=int, default=20)
    reconcile_okx.add_argument("--close-seconds", type=int, default=60)
    reconcile_okx.add_argument("--net-tolerance", default="0.00000001")
    reconcile_okx.add_argument("--json", action="store_true", dest="json_output")

    report = subparsers.add_parser(
        "report", help="Offline candidate-only JSON/CSV/HTML report from local normalized data."
    )
    report.add_argument("--input", required=True, help="Local normalized JSON snapshot.")
    report.add_argument("--format", choices=("json", "csv", "html"), default="json")
    report.add_argument(
        "--include-values",
        action="store_true",
        help="Explicitly include potentially private instrument and amount values.",
    )
    serve = subparsers.add_parser(
        "serve", help="Serve a redacted candidate report on localhost only (GET-only)."
    )
    serve.add_argument("--input", required=True, help="Local normalized JSON snapshot.")
    serve.add_argument("--port", type=int, default=8767)
    native = subparsers.add_parser(
        "okx-native-report",
        help="Offline sanitized OKX source-native evidence check; no USD/account proof.",
    )
    native.add_argument("--input", required=True, help="Local sanitized GET-only evidence JSON.")
    native.add_argument(
        "--tolerance",
        default="0.00000001",
        help="Explicit source-native Decimal arithmetic tolerance.",
    )
    settlement = subparsers.add_parser(
        "settlement-audit",
        help="Offline exact-position signed event ledger audit; account ownership UNVERIFIED.",
    )
    settlement.add_argument("--input", required=True, help="Local event evidence JSON file.")
    settlement.add_argument("--tolerance", default="0.00000001")
    settlement.add_argument(
        "--include-values",
        action="store_true",
        help="Explicitly show native monetary values on stdout (private).",
    )
    return parser


def _load_candidate_report(path: str, *, include_values: bool = False) -> dict[str, object]:
    """Bounded local input; do not leak file content or credentials on failure."""
    from pathlib import Path

    from freqtrade_truth.api.reporting import build_report
    from freqtrade_truth.api.snapshot import MAX_BYTES, parse_snapshot

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("source must be a regular non-symlink file")
    if source.stat().st_size > MAX_BYTES:
        raise ValueError("snapshot exceeds local input limit")
    with source.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("snapshot exceeds local input limit")
    # Use Decimal for JSON numeric tokens, but all financial fields must be strings.
    parsed = json.loads(data.decode("utf-8"), parse_float=__import__("decimal").Decimal)
    left, right, tolerance = parse_snapshot(parsed)
    return build_report(left, right, tolerance=tolerance, include_values=include_values)


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

    if args.command == "settlement-audit":
        from decimal import Decimal, InvalidOperation
        from pathlib import Path

        from freqtrade_truth.api.snapshot import MAX_BYTES
        from freqtrade_truth.core.settlement_ledger import audit_settlement_ledger

        try:
            tolerance = Decimal(args.tolerance)
            if not tolerance.is_finite() or tolerance < 0:
                raise ValueError("invalid tolerance")
            source = Path(args.input)
            if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_BYTES:
                raise ValueError("invalid source")
            with source.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("invalid source")

            def unique_members(items: list[tuple[str, object]]) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate property")
                    result[key] = value
                return result

            snapshot = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_members)
            result: dict[str, object] = dict(audit_settlement_ledger(snapshot, tolerance=tolerance))
        except (OSError, ValueError, UnicodeError, InvalidOperation):
            print("ERROR: invalid or incomplete local settlement evidence", file=err)
            return 2
        if not args.include_values:
            for name in ("computed_native_net", "exchange_minus_bot", "computed_minus_exchange"):
                result[name] = None
            result["native_components"] = {
                name: None for name in ("price_pnl", "trading_fees", "funding", "other_adjustments")
            }
            result["amounts_redacted"] = True
        else:
            result["amounts_redacted"] = False
        print(json.dumps(result, sort_keys=True), file=out)
        return 0

    if args.command == "reconcile-okx":
        from decimal import Decimal

        from freqtrade_truth.okx_candidate import OkxCandidateConfig, compare_okx_candidate

        try:
            okx_candidate_config = OkxCandidateConfig(
                freqtrade_url=args.freqtrade_url,
                region=args.region,
                inst_id=args.inst_id,
                inst_type=args.inst_type,
                settle=args.settle,
                pair=args.pair,
                source_amount_ccy=args.source_amount_ccy,
                isolated_xperp=args.isolated_xperp,
                hours=args.hours,
                limit=args.limit,
                max_pages=args.max_pages,
                close_seconds=args.close_seconds,
                net_tolerance=Decimal(args.net_tolerance),
            )
            summary = asyncio.run(compare_okx_candidate(okx_candidate_config, environ=env))
        except Exception:  # No exception text: secrets, private instrument IDs or payloads
            print("ERROR: independent read-only OKX candidate audit failed", file=err)
            return 2
        if args.json_output:
            print(json.dumps(summary, sort_keys=True), file=out)
        else:
            print(
                f"Numeric candidates: {summary['candidate_numeric_equal']} equal, "
                f"{summary['candidate_numeric_different']} different, "
                f"{summary['unverified_or_ambiguous_groups']} incomplete/ambiguous",
                file=out,
            )
            print("Account ownership and actual financial truth: NOT VERIFIED", file=out)
        return 0

    if args.command == "reconcile-bybit":
        from decimal import Decimal

        try:
            candidate_config = BybitCandidateConfig(
                freqtrade_url=args.freqtrade_url,
                symbol=args.symbol,
                base=args.base,
                quote=args.quote,
                settle=args.settle,
                hours=args.hours,
                limit=args.limit,
                max_pages=args.max_pages,
                close_seconds=args.close_seconds,
                net_tolerance=Decimal(args.net_tolerance),
                include_values=args.include_values,
                check_order_refs=args.check_order_links,
                check_execution_ledger=args.check_execution_ledger,
            )
            candidate_report = asyncio.run(compare_bybit_inverse(candidate_config, environ=env))
        except Exception:  # Fail closed; never echo HTTP response, secrets, local IDs
            print("ERROR: two-source read-only candidate reconciliation failed", file=err)
            return 2
        if args.json_output:
            print(json.dumps(candidate_report, sort_keys=True), file=out)
        else:
            print(
                f"Source numeric candidates: {candidate_report['candidate_numeric_equal']} equal, "
                f"{candidate_report['candidate_numeric_different']} different, "
                f"{candidate_report['unverified_or_ambiguous_groups']} unknown",
                file=out,
            )
            print("Trade ownership, funding and full account PnL: NOT VERIFIED", file=out)
        return 0

    if args.command == "freqtrade-check":
        try:
            ft_config = FreqtradeCheckConfig(
                base_url=args.url,
                hours=args.hours,
                limit=args.limit,
                max_pages=args.max_pages,
            )
            ft_preview = asyncio.run(run_freqtrade_check(ft_config, environ=env))
        except (ValueError, FreqtradeResponseError, HttpTransportError):
            print("ERROR: Freqtrade read-only source check failed", file=err)
            return 2
        except Exception:  # pragma: no cover - do not leak local source details
            print("ERROR: Freqtrade source check failed unexpectedly", file=err)
            return 2
        if args.json_output:
            print(json.dumps(ft_preview, sort_keys=True), file=out)
        else:
            print(
                f"Freqtrade closed-trade source preview: {ft_preview['closed_trade_count']} "
                "(exchange ownership UNVERIFIED)",
                file=out,
            )
            print("Financial truth and full history: NOT VERIFIED", file=out)
        return 0

    if args.command == "okx-native-report":
        from decimal import Decimal, InvalidOperation
        from pathlib import Path

        from freqtrade_truth.api.native_report import native_source_report
        from freqtrade_truth.api.snapshot import MAX_BYTES

        try:
            tol = Decimal(args.tolerance)
            if not tol.is_finite() or tol < 0:
                raise ValueError("invalid tolerance")
            source = Path(args.input)
            if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_BYTES:
                raise ValueError("invalid source")
            with source.open("rb") as handle:
                data = handle.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("input exceeds limit")
            snapshot = json.loads(data.decode("utf-8"))
            summary = native_source_report(snapshot, tolerance=tol)
        except (OSError, ValueError, UnicodeError, InvalidOperation):
            print("ERROR: invalid or incomplete native evidence", file=err)
            return 2
        print(json.dumps(summary, sort_keys=True), file=out)
        return 0

    if args.command in ("report", "serve"):
        from freqtrade_truth.api.reporting import as_csv, as_html, as_json

        try:
            result = _load_candidate_report(
                str(args.input), include_values=bool(getattr(args, "include_values", False))
            )
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
            print("ERROR: invalid local snapshot or incomplete normalized source", file=err)
            return 2
        if args.command == "report":
            render = {"json": as_json, "csv": as_csv, "html": as_html}[args.format]
            print(render(result), file=out, end="")
            return 0
        if not 1 <= args.port <= 65535:
            print("ERROR: localhost port must be 1..65535", file=err)
            return 2
        from wsgiref.simple_server import make_server

        from freqtrade_truth.api.http import make_application

        try:
            with make_server("127.0.0.1", args.port, make_application(result)) as server:
                print(f"GET-only candidate report: http://127.0.0.1:{args.port}/", file=out)
                server.serve_forever()
        except (OSError, KeyboardInterrupt):
            return 0
        return 0

    if args.command == "demo":
        demo_result = build_offline_demo()
        if args.json_output:
            print(json.dumps(demo_result, sort_keys=True), file=out)
        else:
            print("SYNTHETIC numeric comparison — NOT verified ownership / accounting", file=out)
            for item in demo_result["scenarios"]:
                print(f"  {item['name']}: {item['numeric_status']}", file=out)
        return 0

    if args.command == "okx-check":
        try:
            config_okx = OkxCheckConfig(
                region=str(args.region),
                inst_id=str(args.inst_id),
                inst_type=str(args.inst_type),
                settle=str(args.settle),
                pair=str(args.pair),
                hours=int(args.hours),
                limit=int(args.limit),
                json_output=bool(args.json_output),
                source_amount_ccy=args.source_amount_ccy,
                isolated_xperp=bool(args.isolated_xperp),
            )
            result_okx = asyncio.run(run_okx_check(config_okx, environ=env))
        except (
            OkxPositionHistoryError,
            OkxInstrumentPreflightError,
            HttpTransportError,
            ValueError,
        ) as exc:
            print(f"ERROR: {_redact_message(str(exc), env)}", file=err)
            return 2
        except Exception as exc:  # pragma: no cover - fail closed, no raw payload
            print(f"ERROR: unexpected {type(exc).__name__}", file=err)
            return 2
        if config_okx.json_output:
            print(json.dumps(result_okx, sort_keys=True), file=out)
        else:
            print("OKX source-read: PASS (not verified financial truth)", file=out)
            print(f"Region: {result_okx.get('region')}", file=out)
            print(f"Fully closed positions: {result_okx.get('closed_position_count')}", file=out)
            print(f"Reported native net preview: {result_okx.get('reported_net_native')}", file=out)
            print("Ownership and complete history: NOT VERIFIED", file=out)
        return 0

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
    for variable in (
        "BYBIT_API_KEY",
        "BYBIT_API_SECRET",
        "OKX_READ_API_KEY",
        "OKX_READ_API_SECRET",
        "OKX_READ_API_PASSPHRASE",
    ):
        value = environ.get(variable, "")
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
