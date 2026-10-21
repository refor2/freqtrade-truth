"""Offline review demonstration using only deliberately synthetic financial amounts.

The preview numeric statuses do not assert trade, account or order ownership.
No exchange client, credentials or network calls are part of this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict

from freqtrade_truth.core.comparison import reconcile_group
from freqtrade_truth.core.models import FinancialField, NormalizedTradeRecord, SourceKind
from freqtrade_truth.core.reconciliation import ReconciliationTolerance, group_comparable_records


class DemoScenario(TypedDict):
    name: str
    numeric_status: str
    difference: str | None
    verified_trade_identity: bool


class DemoResult(TypedDict):
    status: str
    scope: str
    verified_trade_identity: bool
    uses_exchange_api: bool
    scenarios: list[DemoScenario]


def build_offline_demo() -> DemoResult:
    """Show numeric-match, mismatch and unknown states without claiming proven identity."""
    scenarios: list[DemoScenario] = []
    cases: tuple[tuple[str, Decimal, Decimal | None], ...] = (
        ("equal_amounts_not_verified_ownership", Decimal("12.34"), Decimal("12.34")),
        ("different_amounts", Decimal("10.00"), Decimal("9.80")),
        ("missing_exchange_amount", Decimal("5.00"), None),
    )
    tolerances = ReconciliationTolerance()
    for index, (name, bot_amount, exchange_amount) in enumerate(cases):
        closed_at = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(hours=index)
        bot = NormalizedTradeRecord(
            record_id=f"synthetic:bot:{index}",
            source_kind=SourceKind.FREQTRADE,
            source_name="synthetic-freqtrade",
            trade_ref=f"FT-SYNTH-{index}",
            reported_net_pnl=bot_amount,
            instrument="BTC/USD:BTC",
            settlement_currency="BTC",
            closed_at=closed_at,
        )
        exchange = NormalizedTradeRecord(
            record_id=f"synthetic:exchange:{index}",
            source_kind=SourceKind.EXCHANGE,
            source_name="synthetic-exchange",
            trade_ref=f"EX-SYNTH-{index}",
            reported_net_pnl=exchange_amount,
            instrument="BTC/USD:BTC",
            settlement_currency="BTC",
            closed_at=closed_at,
        )
        groups = group_comparable_records((bot,), (exchange,), tolerance=tolerances)
        if len(groups) != 1:
            raise AssertionError("offline synthetic comparison must produce one group")
        compared = reconcile_group(
            groups[0],
            tolerance=tolerances,
            fields=(FinancialField.REPORTED_NET_PNL,),
        )
        comparison = compared.comparisons[0]
        scenarios.append(
            {
                "name": name,
                "numeric_status": compared.status.value,
                "difference": str(comparison.difference)
                if comparison.difference is not None
                else None,
                "verified_trade_identity": False,
            }
        )
    return {
        "status": "ok",
        "scope": "synthetic_numeric_preview_only",
        "verified_trade_identity": False,
        "uses_exchange_api": False,
        "scenarios": scenarios,
    }
