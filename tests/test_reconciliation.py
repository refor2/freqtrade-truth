from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from freqtrade_truth.core import (
    FinancialField,
    NormalizedTradeRecord,
    ReconciliationTolerance,
    SourceKind,
    canonical_derivative_instrument,
    group_comparable_records,
)


def record(
    source_kind: SourceKind,
    record_id: str,
    *,
    seconds: int = 0,
    instrument: str = "BTC/USD:BTC",
    settlement_currency: str = "BTC",
) -> NormalizedTradeRecord:
    source_name = "synthetic-freqtrade" if source_kind is SourceKind.FREQTRADE else "synthetic-exchange"
    return NormalizedTradeRecord(
        record_id=record_id,
        source_kind=source_kind,
        source_name=source_name,
        trade_ref=record_id,
        instrument=instrument,
        settlement_currency=settlement_currency,
        closed_at=datetime(2030, 1, 1, 12, tzinfo=UTC) + timedelta(seconds=seconds),
    )


def test_canonical_derivative_instrument_matches_freqtrade_style_identity() -> None:
    assert canonical_derivative_instrument("BTC", "USD", "BTC") == "BTC/USD:BTC"


@pytest.mark.parametrize(
    "args",
    [
        ("btc", "USD", "BTC"),
        ("BTC", "usd", "BTC"),
        ("BTC", "USD", " btc "),
        ("BTC/USD", "USD", "BTC"),
        ("BTC", "USD:BTC", "BTC"),
    ],
)
def test_canonical_derivative_instrument_rejects_ambiguous_tokens(
    args: tuple[str, str, str],
) -> None:
    with pytest.raises(ValueError):
        canonical_derivative_instrument(*args)


def test_tolerance_is_explicit_and_field_addressable() -> None:
    tolerance = ReconciliationTolerance(
        close_time=timedelta(seconds=3),
        price_pnl=Decimal("0.01"),
        trading_fees=Decimal("0.001"),
        funding=Decimal("0.0001"),
        other_adjustments=Decimal("0"),
        reported_net_pnl=Decimal("0.01"),
    )

    assert tolerance.close_time == timedelta(seconds=3)
    assert tolerance.for_field(FinancialField.PRICE_PNL) == Decimal("0.01")
    assert tolerance.for_field(FinancialField.TRADING_FEES) == Decimal("0.001")
    assert tolerance.for_field(FinancialField.FUNDING) == Decimal("0.0001")
    assert tolerance.for_field(FinancialField.OTHER_ADJUSTMENTS) == Decimal("0")
    assert tolerance.for_field(FinancialField.REPORTED_NET_PNL) == Decimal("0.01")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"close_time": timedelta(microseconds=-1)},
        {"price_pnl": Decimal("-0.01")},
        {"trading_fees": Decimal("NaN")},
        {"funding": Decimal("Infinity")},
    ],
)
def test_tolerance_rejects_negative_or_non_finite_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ReconciliationTolerance(**kwargs)  # type: ignore[arg-type]


def test_groups_one_freqtrade_trade_with_multiple_exchange_records() -> None:
    bot = record(SourceKind.FREQTRADE, "bot-1")
    exchange_a = record(SourceKind.EXCHANGE, "exchange-1", seconds=1)
    exchange_b = record(SourceKind.EXCHANGE, "exchange-2", seconds=2)

    groups = group_comparable_records(
        [bot],
        [exchange_b, exchange_a],
        tolerance=ReconciliationTolerance(close_time=timedelta(seconds=2)),
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.is_structurally_comparable
    assert group.freqtrade_records == (bot,)
    assert group.exchange_records == (exchange_a, exchange_b)


def test_ambiguous_time_links_remain_one_visible_component() -> None:
    bot_a = record(SourceKind.FREQTRADE, "bot-1", seconds=0)
    bot_b = record(SourceKind.FREQTRADE, "bot-2", seconds=4)
    exchange = record(SourceKind.EXCHANGE, "exchange-1", seconds=2)

    groups = group_comparable_records(
        [bot_b, bot_a],
        [exchange],
        tolerance=ReconciliationTolerance(close_time=timedelta(seconds=2)),
    )

    assert len(groups) == 1
    assert groups[0].freqtrade_records == (bot_a, bot_b)
    assert groups[0].exchange_records == (exchange,)
    assert not groups[0].is_structurally_comparable


def test_records_outside_time_or_identity_boundary_are_not_joined() -> None:
    bot = record(SourceKind.FREQTRADE, "bot-1")
    late_exchange = record(SourceKind.EXCHANGE, "exchange-late", seconds=10)
    other_currency = record(
        SourceKind.EXCHANGE,
        "exchange-usd",
        seconds=0,
        settlement_currency="USD",
    )
    other_instrument = record(
        SourceKind.EXCHANGE,
        "exchange-eth",
        seconds=0,
        instrument="ETH/USD:ETH",
        settlement_currency="ETH",
    )

    groups = group_comparable_records(
        [bot],
        [other_currency, late_exchange, other_instrument],
        tolerance=ReconciliationTolerance(close_time=timedelta(seconds=2)),
    )

    assert len(groups) == 4
    assert sum(group.is_structurally_comparable for group in groups) == 0
    assert {(group.instrument, group.settlement_currency) for group in groups} == {
        ("BTC/USD:BTC", "BTC"),
        ("BTC/USD:BTC", "USD"),
        ("ETH/USD:ETH", "ETH"),
    }


def test_grouping_is_deterministic_for_input_order() -> None:
    bot_a = record(SourceKind.FREQTRADE, "bot-a", seconds=0)
    bot_b = record(SourceKind.FREQTRADE, "bot-b", seconds=20)
    exchange_a = record(SourceKind.EXCHANGE, "exchange-a", seconds=1)
    exchange_b = record(SourceKind.EXCHANGE, "exchange-b", seconds=21)
    tolerance = ReconciliationTolerance(close_time=timedelta(seconds=2))

    forward = group_comparable_records(
        [bot_a, bot_b],
        [exchange_a, exchange_b],
        tolerance=tolerance,
    )
    reversed_inputs = group_comparable_records(
        [bot_b, bot_a],
        [exchange_b, exchange_a],
        tolerance=tolerance,
    )

    assert reversed_inputs == forward


def test_duplicate_or_wrong_source_input_fails_closed() -> None:
    bot = record(SourceKind.FREQTRADE, "bot-1")
    exchange = record(SourceKind.EXCHANGE, "exchange-1")

    with pytest.raises(ValueError, match="duplicate"):
        group_comparable_records(
            [bot, bot],
            [],
            tolerance=ReconciliationTolerance(),
        )

    with pytest.raises(ValueError, match="wrong source kind"):
        group_comparable_records(
            [exchange],
            [],
            tolerance=ReconciliationTolerance(),
        )
