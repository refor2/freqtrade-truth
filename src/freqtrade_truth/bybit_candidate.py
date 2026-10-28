"""Independent two-source Bybit inverse / Freqtrade candidate reconciliation.

No private external operator tooling components, no strategy access, no execution endpoints,
no verified economic ownership claims without stronger evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from freqtrade_truth.adapters.bybit import BybitInverseClosedPnlAdapter
from freqtrade_truth.adapters.bybit_auth import BybitV5HmacTransport
from freqtrade_truth.adapters.bybit_execution_evidence import (
    BybitExecutionQuery,
    BybitInverseExecutionReader,
)
from freqtrade_truth.adapters.bybit_market import (
    BybitV5PublicTransport,
    verify_bybit_inverse_perpetual,
)
from freqtrade_truth.adapters.bybit_preflight import verify_bybit_read_only_key
from freqtrade_truth.adapters.bybit_transaction_log import (
    BybitInverseTransactionLogReader,
    BybitTransactionLogQuery,
)
from freqtrade_truth.adapters.execution_ledger_review import (
    review_bot_execution_ledger_candidates,
)
from freqtrade_truth.adapters.freqtrade import FreqtradeReadAdapter
from freqtrade_truth.adapters.freqtrade_order_evidence import (
    FreqtradeClosedTradeOrderEvidence,
    FreqtradeClosedTradeOrderReader,
)
from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, StdlibJsonTransport
from freqtrade_truth.adapters.order_link_review import (
    OrderLinkReviewState,
    review_order_id_candidates,
)
from freqtrade_truth.core.comparison import ReconciliationStatus, reconcile_group
from freqtrade_truth.core.models import ClosedTradeQuery, FinancialField, NormalizedTradeRecord
from freqtrade_truth.core.reconciliation import ReconciliationTolerance, group_comparable_records
from freqtrade_truth.freqtrade_cli import FreqtradeCheckConfig, _BoundedReader

# Explicit fixed public API host. No operator-selected exchange host in this
# first release: avoid credentials ever reaching a lookalike hostname.
_BYBIT_HOST = "https://api.bybit.com"


@dataclass(frozen=True, slots=True)
class BybitCandidateConfig:
    """One bounded inverse-perpetual comparison for an independent operator."""

    freqtrade_url: str
    symbol: str
    base: str
    quote: str
    settle: str
    hours: int = 24
    limit: int = 50
    max_pages: int = 20
    close_seconds: int = 60
    net_tolerance: Decimal = Decimal("0.00000001")
    include_values: bool = False
    check_order_refs: bool = False
    check_execution_ledger: bool = False

    def __post_init__(self) -> None:
        FreqtradeCheckConfig(
            base_url=self.freqtrade_url,
            hours=self.hours,
            limit=self.limit,
            max_pages=self.max_pages,
        )
        for name in ("symbol", "base", "quote", "settle"):
            value = getattr(self, name)
            if not value or value != value.upper() or not value.isascii() or not value.isalnum():
                raise ValueError(f"{name} must be an uppercase alphanumeric token")
        if self.symbol != f"{self.base}{self.quote}":
            raise ValueError("symbol must be canonical BASE+QUOTE for this inverse preview")
        if isinstance(self.hours, bool) or not 1 <= self.hours <= 168:
            raise ValueError("Bybit inverse read window must be 1..168 hours")
        if isinstance(self.close_seconds, bool) or not isinstance(self.close_seconds, int):
            raise ValueError("close_seconds must be a nonnegative integer")
        if not 0 <= self.close_seconds <= 3600:
            raise ValueError("close_seconds must be 0..3600")
        if not isinstance(self.include_values, bool):
            raise ValueError("include_values must be bool")
        if not isinstance(self.check_order_refs, bool):
            raise ValueError("check_order_refs must be bool")
        if not isinstance(self.check_execution_ledger, bool):
            raise ValueError("check_execution_ledger must be bool")
        if self.check_execution_ledger and not self.check_order_refs:
            raise ValueError("execution ledger review requires order reference review")
        if (
            not isinstance(self.net_tolerance, Decimal)
            or not self.net_tolerance.is_finite()
            or self.net_tolerance < 0
        ):
            raise ValueError("net_tolerance must be a nonnegative finite Decimal")


class _BoundedBybit:
    """Restrict authenticated Bybit methods and page count for the audit."""

    def __init__(self, transport: JsonHttpTransport, max_pages: int) -> None:
        self._transport = transport
        self._max_pages = max_pages
        self._pages = 0

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        if path == "v5/user/query-api":
            if params:
                raise ValueError("unexpected parameters for key preflight")
        elif path in (
            "v5/position/closed-pnl",
            "v5/execution/list",
            "v5/account/transaction-log",
        ):
            if not params or params.get("category") != "inverse":
                raise ValueError("Bybit private evidence request missing inverse category")
            if path == "v5/execution/list" and (
                not isinstance(params.get("orderId"), str) or params.get("execType") != "Trade"
            ):
                raise ValueError("Bybit execution GET requires exact order and Trade type")
            if self._pages >= self._max_pages:
                raise ValueError("Bybit total page budget exceeded; evidence incomplete")
            self._pages += 1
        else:
            raise ValueError("Bybit two-source audit endpoint is not allowed")
        return await self._transport.get_json(path, params)


class _DetailOnlyTransport:
    """Permit one GET trade/{id} per previously selected closed Freqtrade trade."""

    def __init__(self, transport: JsonHttpTransport, trade_ids: frozenset[int]) -> None:
        self._transport = transport
        self._allowed = frozenset(f"trade/{trade_id}" for trade_id in trade_ids)
        self._seen: set[str] = set()

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        if path not in self._allowed or path in self._seen or params:
            raise ValueError("unapproved or repeated Freqtrade trade-detail request")
        self._seen.add(path)
        return await self._transport.get_json(path)


async def _review_source_order_candidates(
    bot_records: tuple[NormalizedTradeRecord, ...],
    exchange_records: tuple[NormalizedTradeRecord, ...],
    transport: JsonHttpTransport,
    *,
    settlement_currency: str,
) -> tuple[dict[str, object], tuple[FreqtradeClosedTradeOrderEvidence, ...]]:
    """Get positive-filled order-ID candidates without ownership promotion."""

    trade_ids: list[int] = []
    for record in bot_records:
        value = record.trade_ref
        if not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 18:
            raise ValueError("noncanonical closed Freqtrade trade ID")
        trade_id = int(value)
        if not 0 < trade_id <= 2**63 - 1:
            raise ValueError("Freqtrade trade ID is out of range")
        trade_ids.append(trade_id)
    if len(set(trade_ids)) != len(trade_ids):
        raise ValueError("duplicate Freqtrade trade ID within source selection")

    detail_reader = FreqtradeClosedTradeOrderReader(
        _DetailOnlyTransport(transport, frozenset(trade_ids))
    )
    observations = tuple(
        [
            await detail_reader.fetch(trade_id, expected_instrument=record.instrument)
            for trade_id, record in zip(trade_ids, bot_records, strict=True)
        ]
    )
    reviews = review_order_id_candidates(
        observations,
        exchange_records,
        settlement_currency=settlement_currency,
    )
    single_candidate = sum(
        entry.state is OrderLinkReviewState.CANDIDATE and len(entry.candidate_record_ids) == 1
        for entry in reviews
    )
    multiple = sum(
        entry.state is OrderLinkReviewState.CANDIDATE and len(entry.candidate_record_ids) > 1
        for entry in reviews
    )
    ambiguous = sum(entry.state is OrderLinkReviewState.AMBIGUOUS for entry in reviews)
    no_candidate = sum(entry.state is OrderLinkReviewState.NO_CANDIDATE for entry in reviews)
    return {
        "checked_bot_trades": len(observations),
        "single_candidate": single_candidate,
        "multiple_candidates": multiple,
        "ambiguous": ambiguous,
        "no_candidate": no_candidate,
        "candidate_only": True,
        "verified_trade_ownership": False,
        "verified_complete_lifecycle": False,
        "unverified_funding": True,
    }, observations


async def _review_three_source_candidates(
    observations: tuple[FreqtradeClosedTradeOrderEvidence, ...],
    bybit_transport: JsonHttpTransport,
    *,
    symbol: str,
    settlement_currency: str,
    normalized_instrument: str,
    start_at: datetime,
    end_at: datetime,
    max_orders: int,
) -> dict[str, object]:
    """Corroborate exact filled order -> executions -> ledger fee, candidate only."""

    order_ids = tuple(
        sorted(
            {
                order_id
                for item in observations
                for order_id in item.positive_fill_exchange_order_ids
            }
        )
    )
    if len(order_ids) > min(max_orders, 12):
        raise ValueError("too many execution orders for bounded read-only evidence pass")
    if not order_ids:
        return {
            "checked_orders": 0,
            "ledger_records_in_window": 0,
            "candidate_trades": 0,
            "ambiguous_trades": 0,
            "no_candidate_trades": len(observations),
            "pagination_exhausted": False,
            "verified_account_ownership": False,
            "verified_full_funding": False,
            "verified_complete_history": False,
        }

    execution_reader = BybitInverseExecutionReader(
        bybit_transport,
        symbol=symbol,
        settlement_currency=settlement_currency,
    )
    windows = tuple(
        [
            await execution_reader.fetch_executions(
                BybitExecutionQuery(order_id=order_id, start_at=start_at, end_at=end_at)
            )
            for order_id in order_ids
        ]
    )
    ledger_reader = BybitInverseTransactionLogReader(
        bybit_transport,
        symbol=symbol,
        settlement_currency=settlement_currency,
    )
    ledger = await ledger_reader.fetch_paginated_window_evidence(
        BybitTransactionLogQuery(start_at=start_at, end_at=end_at)
    )
    reviews = review_bot_execution_ledger_candidates(
        observations,
        windows,
        ledger,
        settlement_currency=settlement_currency,
        normalized_instrument=normalized_instrument,
    )
    return {
        "checked_orders": len(windows),
        "ledger_records_in_window": len(ledger.records),
        "candidate_trades": sum(entry.state is OrderLinkReviewState.CANDIDATE for entry in reviews),
        "ambiguous_trades": sum(entry.state is OrderLinkReviewState.AMBIGUOUS for entry in reviews),
        "no_candidate_trades": sum(
            entry.state is OrderLinkReviewState.NO_CANDIDATE for entry in reviews
        ),
        "pagination_exhausted": ledger.pagination_exhausted
        and all(window.pagination_exhausted for window in windows),
        "verified_account_ownership": False,
        "verified_full_funding": False,
        "verified_complete_history": False,
    }


async def compare_bybit_inverse(
    config: BybitCandidateConfig,
    *,
    environ: Mapping[str, str],
    freqtrade_transport: JsonHttpTransport | None = None,
    bybit_transport: JsonHttpTransport | None = None,
    public_transport: JsonHttpTransport | None = None,
) -> dict[str, object]:
    """Compare live sources by currency/instrument/time, **candidate only**.

    Source read errors raise without returning a partial financial report.
    Numeric agreement cannot prove fill, position, account or funding ownership.
    """

    ft_user = environ.get("FREQTRADE_READ_USERNAME", "")
    ft_pass = environ.get("FREQTRADE_READ_PASSWORD", "")
    api_key = environ.get("BYBIT_API_KEY", "")
    api_secret = environ.get("BYBIT_API_SECRET", "")
    if bool(ft_user) != bool(ft_pass):
        raise ValueError("Freqtrade username/password must be supplied together")
    if not api_key or not api_secret:
        raise ValueError("a separate read-only Bybit API credential is required")

    public_transport = public_transport or BybitV5PublicTransport(_BYBIT_HOST)
    bybit_transport = bybit_transport or BybitV5HmacTransport(
        _BYBIT_HOST,
        api_key=api_key,
        api_secret=api_secret,
    )
    bybit_bounded = _BoundedBybit(bybit_transport, config.max_pages)
    # Before retrieving account records we independently verify that Bybit
    # itself marks the provided key readOnly=1.
    await verify_bybit_read_only_key(bybit_bounded)
    market = await verify_bybit_inverse_perpetual(
        public_transport,
        symbol=config.symbol,
        expected_base_coin=config.base,
        expected_quote_coin=config.quote,
        expected_settle_coin=config.settle,
    )
    ft_transport = freqtrade_transport or StdlibJsonTransport(
        config.freqtrade_url,
        username=ft_user or None,
        password=ft_pass or None,
    )
    ft_bounded = _BoundedReader(ft_transport, config.max_pages)
    raw_end = datetime.now(UTC)
    query_end = raw_end.replace(microsecond=raw_end.microsecond // 1000 * 1000)
    query = ClosedTradeQuery(
        closed_from=query_end - timedelta(hours=config.hours),
        closed_until=query_end,
        instrument=market.normalized_instrument,
        limit=config.limit,
    )
    left = await FreqtradeReadAdapter(ft_bounded).fetch_closed_trades(query)
    right = await BybitInverseClosedPnlAdapter(
        bybit_bounded,
        symbol=config.symbol,
        settlement_currency=market.settle_coin,
        normalized_instrument=market.normalized_instrument,
    ).fetch_closed_trades(query)
    if len(left) >= config.limit or len(right) >= config.limit:
        # A record limit reached exactly at the boundary is not evidence of
        # exhausted historical coverage. Reject instead of silently truncating.
        raise ValueError("source record limit reached; comparison would be truncated")

    # Separate optional evidence; never promote order equality to verified
    # owner, fill/position lifecycle, funding or financial accounting.
    order_reference_review: dict[str, object] | None = None
    observations: tuple[FreqtradeClosedTradeOrderEvidence, ...] = ()
    if config.check_order_refs:
        order_reference_review, observations = await _review_source_order_candidates(
            tuple(left), tuple(right), ft_transport, settlement_currency=market.settle_coin
        )
    execution_ledger_review = (
        await _review_three_source_candidates(
            observations,
            bybit_bounded,
            symbol=config.symbol,
            settlement_currency=market.settle_coin,
            normalized_instrument=market.normalized_instrument,
            start_at=query.closed_from,
            end_at=query.closed_until,
            max_orders=config.max_pages,
        )
        if config.check_execution_ledger
        else None
    )

    tolerance = ReconciliationTolerance(
        close_time=timedelta(seconds=config.close_seconds),
        reported_net_pnl=config.net_tolerance,
    )
    groups = group_comparable_records(left, right, tolerance=tolerance)
    numeric_equal = 0
    numeric_different = 0
    unverified = 0
    for group in groups:
        if len(group.freqtrade_records) != 1 or len(group.exchange_records) != 1:
            unverified += 1
            continue
        match = reconcile_group(
            group,
            tolerance=tolerance,
            fields=(FinancialField.REPORTED_NET_PNL,),
        )
        if match.status is ReconciliationStatus.MATCH:
            numeric_equal += 1
        elif match.status is ReconciliationStatus.REVIEW:
            numeric_different += 1
        else:
            unverified += 1
    # Optional operator-only source totals; never silently sum unknown fields.
    left_amounts = [row.reported_net_pnl for row in left]
    right_amounts = [row.reported_net_pnl for row in right]
    left_total = (
        sum((v for v in left_amounts if v is not None), Decimal("0"))
        if left_amounts and all(v is not None for v in left_amounts)
        else None
    )
    right_total = (
        sum((v for v in right_amounts if v is not None), Decimal("0"))
        if right_amounts and all(v is not None for v in right_amounts)
        else None
    )
    report: dict[str, object] = {
        "schema": "freqtrade-truth.bybit-inverse-candidate.v1",
        "status": "candidate_report",
        "read_only": True,
        "exchange_key_read_only_verified": True,
        "freqtrade_records": len(left),
        "bybit_records": len(right),
        "candidate_numeric_equal": numeric_equal,
        "candidate_numeric_different": numeric_different,
        "unverified_or_ambiguous_groups": unverified,
        "comparison_groups": len(groups),
        "order_reference_review": order_reference_review,
        "execution_ledger_review": execution_ledger_review,
        "source_currency": market.settle_coin,
        "history_complete": False,
        "trade_identity_verified": False,
        "funding_ownership_verified": False,
        "account_financial_truth_verified": False,
        "financial_truth_status": "unverified",
        "usd_valuation_verified": False,
        "values_redacted": not config.include_values,
        "notice": (
            "Candidate numeric comparison only. No confirmed trade ownership, "
            "complete lifecycle, fee/funding attribution or USD-equivalent net."
        ),
    }
    if config.include_values:
        report["source_window_totals"] = {
            "currency": market.settle_coin,
            "freqtrade_reported_net": str(left_total) if left_total is not None else None,
            "bybit_reported_net": str(right_total) if right_total is not None else None,
            "difference_bybit_minus_freqtrade": (
                str(right_total - left_total)
                if left_total is not None and right_total is not None
                else None
            ),
            "scope": "bounded source-only totals, NOT verified financial truth",
        }
    return report
