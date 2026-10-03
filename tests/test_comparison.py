from datetime import UTC, datetime
from decimal import Decimal

import pytest

from freqtrade_truth.core import (
    ComparableRecordGroup,
    FinancialField,
    NormalizedTradeRecord,
    ReconciliationReasonCode,
    ReconciliationStatus,
    ReconciliationTolerance,
    SourceKind,
    reconcile_group,
)


def record(
    source_kind: SourceKind,
    record_id: str,
    *,
    price_pnl: Decimal | None = Decimal("10"),
    trading_fees: Decimal | None = Decimal("-0.30"),
    funding: Decimal | None = Decimal("0.10"),
    other_adjustments: Decimal | None = Decimal("0"),
    reported_net_pnl: Decimal | None = Decimal("9.80"),
) -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id=record_id,
        source_kind=source_kind,
        source_name=(
            "synthetic-freqtrade" if source_kind is SourceKind.FREQTRADE else "synthetic-exchange"
        ),
        trade_ref=record_id,
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        closed_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
        price_pnl=price_pnl,
        trading_fees=trading_fees,
        funding=funding,
        other_adjustments=other_adjustments,
        reported_net_pnl=reported_net_pnl,
    )


def group(
    freqtrade_records: tuple[NormalizedTradeRecord, ...],
    exchange_records: tuple[NormalizedTradeRecord, ...],
) -> ComparableRecordGroup:
    return ComparableRecordGroup(
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        freqtrade_records=freqtrade_records,
        exchange_records=exchange_records,
    )


def matching_group() -> ComparableRecordGroup:
    bot = record(SourceKind.FREQTRADE, "bot-1")
    exchange_a = record(
        SourceKind.EXCHANGE,
        "exchange-1",
        price_pnl=Decimal("6"),
        trading_fees=Decimal("-0.10"),
        funding=Decimal("0.04"),
        reported_net_pnl=Decimal("5.94"),
    )
    exchange_b = record(
        SourceKind.EXCHANGE,
        "exchange-2",
        price_pnl=Decimal("4"),
        trading_fees=Decimal("-0.20"),
        funding=Decimal("0.06"),
        reported_net_pnl=Decimal("3.86"),
    )
    return group((bot,), (exchange_a, exchange_b))


def test_matching_components_are_aggregated_exactly() -> None:
    result = reconcile_group(
        matching_group(),
        tolerance=ReconciliationTolerance(),
    )

    assert result.status is ReconciliationStatus.MATCH
    assert result.reason_codes == (ReconciliationReasonCode.VALUE_WITHIN_TOLERANCE,)
    assert [comparison.field for comparison in result.comparisons] == [
        FinancialField.PRICE_PNL,
        FinancialField.TRADING_FEES,
        FinancialField.FUNDING,
        FinancialField.OTHER_ADJUSTMENTS,
        FinancialField.REPORTED_NET_PNL,
    ]
    assert all(comparison.difference == Decimal("0") for comparison in result.comparisons)
    assert result.exchange_record_ids == ("exchange-1", "exchange-2")


def test_absolute_tolerance_is_inclusive() -> None:
    bot = record(
        SourceKind.FREQTRADE,
        "bot-1",
        reported_net_pnl=Decimal("1.000"),
    )
    exchange = record(
        SourceKind.EXCHANGE,
        "exchange-1",
        reported_net_pnl=Decimal("1.005"),
    )

    result = reconcile_group(
        group((bot,), (exchange,)),
        tolerance=ReconciliationTolerance(reported_net_pnl=Decimal("0.005")),
        fields=(FinancialField.REPORTED_NET_PNL,),
    )

    comparison = result.comparisons[0]
    assert result.status is ReconciliationStatus.MATCH
    assert comparison.difference == Decimal("0.005")
    assert comparison.status is ReconciliationStatus.MATCH


def test_known_difference_outside_tolerance_requires_review() -> None:
    bot = record(
        SourceKind.FREQTRADE,
        "bot-1",
        trading_fees=Decimal("-0.20"),
    )
    exchange = record(
        SourceKind.EXCHANGE,
        "exchange-1",
        trading_fees=Decimal("-0.25"),
    )

    result = reconcile_group(
        group((bot,), (exchange,)),
        tolerance=ReconciliationTolerance(trading_fees=Decimal("0.01")),
        fields=(FinancialField.TRADING_FEES,),
    )

    comparison = result.comparisons[0]
    assert result.status is ReconciliationStatus.REVIEW
    assert result.reason_codes == (ReconciliationReasonCode.VALUE_OUTSIDE_TOLERANCE,)
    assert comparison.freqtrade_value == Decimal("-0.20")
    assert comparison.exchange_value == Decimal("-0.25")
    assert comparison.difference == Decimal("-0.05")


def test_missing_values_remain_incomplete_instead_of_becoming_zero() -> None:
    bot = record(SourceKind.FREQTRADE, "bot-1", funding=None)
    exchange = record(SourceKind.EXCHANGE, "exchange-1", funding=None)

    result = reconcile_group(
        group((bot,), (exchange,)),
        tolerance=ReconciliationTolerance(),
        fields=(FinancialField.FUNDING,),
    )

    comparison = result.comparisons[0]
    assert result.status is ReconciliationStatus.INCOMPLETE
    assert comparison.reason_codes == (
        ReconciliationReasonCode.MISSING_FREQTRADE_VALUE,
        ReconciliationReasonCode.MISSING_EXCHANGE_VALUE,
    )
    assert comparison.freqtrade_value is None
    assert comparison.exchange_value is None
    assert comparison.difference is None


def test_partial_exchange_component_is_not_partially_summed() -> None:
    bot = record(
        SourceKind.FREQTRADE,
        "bot-1",
        trading_fees=Decimal("-0.30"),
    )
    exchange_a = record(
        SourceKind.EXCHANGE,
        "exchange-1",
        trading_fees=Decimal("-0.10"),
    )
    exchange_b = record(
        SourceKind.EXCHANGE,
        "exchange-2",
        trading_fees=None,
    )

    result = reconcile_group(
        group((bot,), (exchange_a, exchange_b)),
        tolerance=ReconciliationTolerance(),
        fields=(FinancialField.TRADING_FEES,),
    )

    comparison = result.comparisons[0]
    assert result.status is ReconciliationStatus.INCOMPLETE
    assert comparison.reason_codes == (ReconciliationReasonCode.MISSING_EXCHANGE_VALUE,)
    assert comparison.exchange_value is None
    assert comparison.difference is None


def test_structural_missing_record_states_are_explicit() -> None:
    bot = record(SourceKind.FREQTRADE, "bot-1")
    exchange = record(SourceKind.EXCHANGE, "exchange-1")

    missing_exchange = reconcile_group(
        group((bot,), ()),
        tolerance=ReconciliationTolerance(),
    )
    missing_freqtrade = reconcile_group(
        group((), (exchange,)),
        tolerance=ReconciliationTolerance(),
    )

    assert missing_exchange.status is ReconciliationStatus.INCOMPLETE
    assert missing_exchange.reason_codes == (ReconciliationReasonCode.MISSING_EXCHANGE_RECORD,)
    assert missing_exchange.comparisons == ()

    assert missing_freqtrade.status is ReconciliationStatus.INCOMPLETE
    assert missing_freqtrade.reason_codes == (ReconciliationReasonCode.MISSING_FREQTRADE_RECORD,)
    assert missing_freqtrade.comparisons == ()


def test_ambiguous_freqtrade_group_requires_review_without_guessing() -> None:
    bot_a = record(SourceKind.FREQTRADE, "bot-1")
    bot_b = record(SourceKind.FREQTRADE, "bot-2")
    exchange = record(SourceKind.EXCHANGE, "exchange-1")

    result = reconcile_group(
        group((bot_a, bot_b), (exchange,)),
        tolerance=ReconciliationTolerance(),
    )

    assert result.status is ReconciliationStatus.REVIEW
    assert result.reason_codes == (ReconciliationReasonCode.AMBIGUOUS_FREQTRADE_RECORDS,)
    assert result.comparisons == ()


def test_known_mismatch_takes_precedence_over_other_incomplete_field() -> None:
    bot = record(
        SourceKind.FREQTRADE,
        "bot-1",
        trading_fees=Decimal("-0.20"),
        funding=None,
    )
    exchange = record(
        SourceKind.EXCHANGE,
        "exchange-1",
        trading_fees=Decimal("-0.40"),
        funding=None,
    )

    result = reconcile_group(
        group((bot,), (exchange,)),
        tolerance=ReconciliationTolerance(),
        fields=(FinancialField.TRADING_FEES, FinancialField.FUNDING),
    )

    assert result.status is ReconciliationStatus.REVIEW
    assert result.reason_codes == (
        ReconciliationReasonCode.VALUE_OUTSIDE_TOLERANCE,
        ReconciliationReasonCode.MISSING_FREQTRADE_VALUE,
        ReconciliationReasonCode.MISSING_EXCHANGE_VALUE,
    )


def test_field_selection_is_canonical_and_rejects_ambiguous_requests() -> None:
    result = reconcile_group(
        matching_group(),
        tolerance=ReconciliationTolerance(),
        fields=(
            FinancialField.REPORTED_NET_PNL,
            FinancialField.PRICE_PNL,
        ),
    )

    assert [comparison.field for comparison in result.comparisons] == [
        FinancialField.PRICE_PNL,
        FinancialField.REPORTED_NET_PNL,
    ]

    with pytest.raises(ValueError, match="empty"):
        reconcile_group(
            matching_group(),
            tolerance=ReconciliationTolerance(),
            fields=(),
        )

    with pytest.raises(ValueError, match="duplicates"):
        reconcile_group(
            matching_group(),
            tolerance=ReconciliationTolerance(),
            fields=(
                FinancialField.FUNDING,
                FinancialField.FUNDING,
            ),
        )


def test_exchange_input_order_does_not_change_result() -> None:
    original = matching_group()
    reversed_group = group(
        original.freqtrade_records,
        tuple(reversed(original.exchange_records)),
    )

    forward = reconcile_group(
        original,
        tolerance=ReconciliationTolerance(),
    )
    backward = reconcile_group(
        reversed_group,
        tolerance=ReconciliationTolerance(),
    )

    assert backward == forward
