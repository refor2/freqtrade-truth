"""Strict, bounded, read-only Freqtrade/OKX numeric candidate comparison.

This is not a source-of-account-ownership oracle. It never sends trading
commands, exports secrets, equates currencies, or declares full history.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from freqtrade_truth.adapters.freqtrade import FreqtradeReadAdapter
from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, StdlibJsonTransport
from freqtrade_truth.adapters.okx_auth import OkxV5ReadOnlyHmacTransport
from freqtrade_truth.adapters.okx_positions_history import OkxPositionHistoryAdapter
from freqtrade_truth.api.reporting import build_report
from freqtrade_truth.core.models import ClosedTradeQuery, FinancialField
from freqtrade_truth.core.native_accounting import audit_native_record
from freqtrade_truth.core.reconciliation import ReconciliationTolerance
from freqtrade_truth.freqtrade_cli import FreqtradeCheckConfig
from freqtrade_truth.okx_cli import OFFICIAL_REGION_BASE_URLS, OkxCheckConfig
from freqtrade_truth.okx_instruments import require_qualified_isolated_xperp


@dataclass(frozen=True, slots=True)
class OkxCandidateConfig:
    """Explicit source identity, local time window and resource budget."""

    freqtrade_url: str
    region: str
    inst_id: str
    inst_type: str
    settle: str
    pair: str
    hours: int = 24
    limit: int = 50
    max_pages: int = 20
    close_seconds: int = 60
    net_tolerance: Decimal = Decimal("0.00000001")
    source_amount_ccy: str | None = None
    isolated_xperp: bool = False

    def __post_init__(self) -> None:
        FreqtradeCheckConfig(
            base_url=self.freqtrade_url,
            hours=self.hours,
            limit=self.limit,
            max_pages=self.max_pages,
        )
        OkxCheckConfig(
            region=self.region,
            inst_id=self.inst_id,
            inst_type=self.inst_type,
            settle=self.settle,
            pair=self.pair,
            hours=self.hours,
            limit=self.limit,
            json_output=True,
            source_amount_ccy=self.source_amount_ccy,
            isolated_xperp=self.isolated_xperp,
        )
        if (
            isinstance(self.close_seconds, bool)
            or not isinstance(self.close_seconds, int)
            or not 0 <= self.close_seconds <= 3600
        ):
            raise ValueError("close_seconds must be 0..3600")
        if (
            not isinstance(self.net_tolerance, Decimal)
            or not self.net_tolerance.is_finite()
            or self.net_tolerance < 0
        ):
            raise ValueError("net_tolerance must be a finite nonnegative Decimal")


class _PageBudget:
    """GET-only source guard; limit exhaustion is incomplete, never success."""

    def __init__(self, transport: JsonHttpTransport, *, max_pages: int, allowed_path: str):
        self._transport = transport
        self._remaining = max_pages
        self._allowed_path = allowed_path

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        if path != self._allowed_path:
            raise ValueError("unapproved GET-only source path")
        if self._remaining <= 0:
            raise ValueError("source page budget exceeded; incomplete history")
        self._remaining -= 1
        return await self._transport.get_json(path, params)


async def compare_okx_candidate(
    config: OkxCandidateConfig,
    *,
    environ: Mapping[str, str],
    freqtrade_transport: JsonHttpTransport | None = None,
    okx_transport: JsonHttpTransport | None = None,
) -> dict[str, object]:
    """Compare independently read native closed trades, without ownership claims."""

    username = environ.get("FREQTRADE_READ_USERNAME", "")
    password = environ.get("FREQTRADE_READ_PASSWORD", "")
    if freqtrade_transport is None:
        if not username or not password:
            raise ValueError("Freqtrade Read-only API credentials are required")
        freqtrade_transport = StdlibJsonTransport(
            config.freqtrade_url, username=username, password=password, timeout_seconds=10.0
        )

    if okx_transport is None:
        key = environ.get("OKX_READ_API_KEY", "")
        secret = environ.get("OKX_READ_API_SECRET", "")
        passphrase = environ.get("OKX_READ_API_PASSPHRASE", "")
        if not key or not secret or not passphrase:
            raise ValueError("a separately provisioned OKX Read-only key is required")
        if config.isolated_xperp:
            await require_qualified_isolated_xperp(
                region=config.region,
                inst_id=config.inst_id,
                inst_type=config.inst_type,
                settle=config.settle,
            )
        okx_transport = OkxV5ReadOnlyHmacTransport(
            OFFICIAL_REGION_BASE_URLS[config.region],
            api_key=key,
            api_secret=secret,
            api_passphrase=passphrase,
        )

    from_ft = FreqtradeReadAdapter(
        _PageBudget(freqtrade_transport, max_pages=config.max_pages, allowed_path="trades")
    )
    from_okx = OkxPositionHistoryAdapter(
        _PageBudget(
            okx_transport,
            max_pages=config.max_pages,
            allowed_path="api/v5/account/positions-history",
        ),
        inst_id=config.inst_id,
        inst_type=config.inst_type,
        settlement_currency=config.source_amount_ccy or config.settle,
        normalized_instrument=config.pair,
        isolated_xperp=config.isolated_xperp,
    )
    end = datetime.now(UTC)
    query = ClosedTradeQuery(
        closed_from=end - timedelta(hours=config.hours),
        closed_until=end,
        instrument=config.pair,
        limit=config.limit,
    )
    bot = tuple(await from_ft.fetch_closed_trades(query))
    exchange = tuple(await from_okx.fetch_closed_trades(query))
    native = [audit_native_record(r, tolerance=config.net_tolerance).status for r in exchange]
    statuses = {
        status: native.count(status) for status in ("consistent", "different", "incomplete")
    }
    tolerance = ReconciliationTolerance(
        close_time=timedelta(seconds=config.close_seconds),
        reported_net_pnl=config.net_tolerance,
    )
    report = build_report(
        bot,
        exchange,
        tolerance=tolerance,
        include_values=False,
        fields=(FinancialField.REPORTED_NET_PNL,),
    )
    # A numeric group never verifies ownership; pre-existing source-local
    # accounting contradictions also block promotion to even a candidate match.
    counts = report["counts"]
    assert isinstance(counts, dict)
    match = int(counts["match"])
    review = int(counts["review"])
    incomplete = int(counts["incomplete"])
    if statuses["different"] or statuses["incomplete"]:
        incomplete += match
        match = 0
    currency_mismatch = bool(
        bot
        and exchange
        and ({r.settlement_currency for r in bot} != {r.settlement_currency for r in exchange})
    )
    return {
        "schema": "freqtrade-truth.okx-candidate.v1",
        "scope": "bounded_cross_source_numeric_candidate_only",
        "read_only": True,
        "freqtrade_record_count": len(bot),
        "okx_record_count": len(exchange),
        "native_source_arithmetic": statuses,
        "candidate_numeric_equal": match,
        "candidate_numeric_different": review,
        "unverified_or_ambiguous_groups": incomplete,
        "source_currency_mismatch": currency_mismatch,
        "native_currency": config.source_amount_ccy or config.settle,
        "history_complete": False,
        "key_permissions_independently_verified": False,
        "verified_trade_identity": False,
        "verified_account_financial_truth": False,
        "funding_ownership_verified": False,
        "usd_valuation_verified": False,
        "warning": (
            "Windowed numeric candidates only. Equal amounts do not prove account ownership, "
            "complete history, funding attribution or cross-currency valuation."
        ),
    }
