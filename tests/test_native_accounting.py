"""Source-only native-currency accounting; not verified cross-source trade identity."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from freqtrade_truth.core.models import NormalizedTradeRecord, SourceKind
from freqtrade_truth.core.native_accounting import audit_native_record


def record(**changes: Decimal | None) -> NormalizedTradeRecord:
    item = NormalizedTradeRecord(
        record_id="source-example",
        trade_ref="position-example",
        source_kind=SourceKind.EXCHANGE,
        source_name="synthetic-okx",
        instrument="BTC/USD:USDC",
        settlement_currency="USDC",
        closed_at=datetime(2030, 1, 1, tzinfo=UTC),
        price_pnl=Decimal("10.25"),
        trading_fees=Decimal("-0.50"),
        funding=Decimal("-0.25"),
        other_adjustments=Decimal("0"),
        reported_net_pnl=Decimal("9.50"),
    )
    return replace(item, **changes)  # type: ignore[arg-type]


def test_signed_usdc_native_equation_is_consistent_without_usd_fx() -> None:
    result = audit_native_record(record(), tolerance=Decimal("0.00000001"))
    assert result.status == "consistent"
    assert result.difference == Decimal("0.00")
    assert result.currency == "USDC"
    assert result.ownership_verified is False
    assert result.usd_valuation_verified is False


def test_native_difference_is_explicit_not_silently_a_match() -> None:
    result = audit_native_record(
        record(reported_net_pnl=Decimal("9.45")),
        tolerance=Decimal("0.00000001"),
    )
    assert result.status == "different"
    assert result.difference == Decimal("0.05")


@pytest.mark.parametrize(
    "field", ("price_pnl", "trading_fees", "funding", "other_adjustments", "reported_net_pnl")
)
def test_unknown_is_not_zero(field: str) -> None:
    result = audit_native_record(
        replace(record(), **{field: None}),  # type: ignore[arg-type]
        tolerance=Decimal("0.00000001"),
    )
    assert result.status == "incomplete"
    assert result.difference is None


def test_zero_is_observed_zero_and_rebate_is_positive() -> None:
    result = audit_native_record(
        record(
            price_pnl=Decimal("1"),
            trading_fees=Decimal("0.2"),
            funding=Decimal("0"),
            other_adjustments=Decimal("0"),
            reported_net_pnl=Decimal("1.2"),
        ),
        tolerance=Decimal("0"),
    )
    assert result.status == "consistent"


def test_tolerance_is_explicit_and_bounded() -> None:
    with pytest.raises(ValueError):
        audit_native_record(record(), tolerance=Decimal("-1"))
    with pytest.raises(ValueError):
        audit_native_record(record(), tolerance=Decimal("NaN"))