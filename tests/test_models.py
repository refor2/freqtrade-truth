from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from freqtrade_truth.core.models import (
    AdapterCapabilities,
    ClosedTradeQuery,
    FinancialField,
    NormalizedTradeRecord,
    SourceKind,
)


def make_record(
    *,
    opened_at: datetime | None = datetime(2030, 1, 1, 8, tzinfo=UTC),
    closed_at: datetime = datetime(2030, 1, 1, 12, tzinfo=UTC),
    settlement_currency: str = "USDT",
    price_pnl: Decimal | None = Decimal("10.00"),
    trading_fees: Decimal | None = Decimal("-0.20"),
    funding: Decimal | None = Decimal("-0.05"),
    other_adjustments: Decimal | None = None,
    reported_net_pnl: Decimal | None = Decimal("9.75"),
) -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id="synthetic-record",
        source_kind=SourceKind.FREQTRADE,
        source_name="synthetic-source",
        trade_ref="synthetic-trade",
        instrument="BTC/USDT:USDT",
        settlement_currency=settlement_currency,
        opened_at=opened_at,
        closed_at=closed_at,
        price_pnl=price_pnl,
        trading_fees=trading_fees,
        funding=funding,
        other_adjustments=other_adjustments,
        reported_net_pnl=reported_net_pnl,
    )


def test_normalized_trade_record_accepts_decimal_and_utc() -> None:
    record = make_record()

    assert record.price_pnl == Decimal("10.00")
    assert record.closed_at.tzinfo is UTC


def test_missing_financial_value_remains_missing() -> None:
    record = make_record(funding=None)

    assert record.funding is None


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_non_finite_decimal_is_rejected(value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite Decimal"):
        make_record(price_pnl=value)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_record(closed_at=datetime(2030, 1, 1, 12))


def test_non_utc_timestamp_is_rejected() -> None:
    plus_two = timezone(timedelta(hours=2))

    with pytest.raises(ValueError, match="normalized to UTC"):
        make_record(closed_at=datetime(2030, 1, 1, 12, tzinfo=plus_two))


def test_opened_after_closed_is_rejected() -> None:
    with pytest.raises(ValueError, match="opened_at"):
        make_record(opened_at=datetime(2030, 1, 1, 13, tzinfo=UTC))


def test_lowercase_settlement_currency_is_rejected() -> None:
    with pytest.raises(ValueError, match="uppercase"):
        make_record(settlement_currency="usdt")


def test_closed_trade_query_validates_range_and_limit() -> None:
    with pytest.raises(ValueError, match="closed_from"):
        ClosedTradeQuery(
            closed_from=datetime(2030, 1, 2, tzinfo=UTC),
            closed_until=datetime(2030, 1, 1, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="positive"):
        ClosedTradeQuery(
            closed_from=datetime(2030, 1, 1, tzinfo=UTC),
            closed_until=datetime(2030, 1, 2, tzinfo=UTC),
            limit=0,
        )


def test_adapter_capabilities_are_explicit() -> None:
    capabilities = AdapterCapabilities(
        fields=frozenset({FinancialField.PRICE_PNL, FinancialField.TRADING_FEES})
    )

    assert capabilities.supports(FinancialField.PRICE_PNL)
    assert not capabilities.supports(FinancialField.FUNDING)
