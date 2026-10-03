"""Deterministic financial comparison results for reconciliation groups."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from freqtrade_truth.core.models import FinancialField, NormalizedTradeRecord
from freqtrade_truth.core.reconciliation import ComparableRecordGroup, ReconciliationTolerance


class ReconciliationStatus(StrEnum):
    """High-level reconciliation outcome."""

    MATCH = "match"
    REVIEW = "review"
    INCOMPLETE = "incomplete"


class ReconciliationReasonCode(StrEnum):
    """Machine-readable reasons explaining reconciliation outcomes."""

    VALUE_WITHIN_TOLERANCE = "value_within_tolerance"
    VALUE_OUTSIDE_TOLERANCE = "value_outside_tolerance"
    MISSING_FREQTRADE_VALUE = "missing_freqtrade_value"
    MISSING_EXCHANGE_VALUE = "missing_exchange_value"
    MISSING_FREQTRADE_RECORD = "missing_freqtrade_record"
    MISSING_EXCHANGE_RECORD = "missing_exchange_record"
    AMBIGUOUS_FREQTRADE_RECORDS = "ambiguous_freqtrade_records"


@dataclass(frozen=True, slots=True)
class FinancialFieldComparison:
    """Comparison of one normalized financial field."""

    field: FinancialField
    status: ReconciliationStatus
    reason_codes: tuple[ReconciliationReasonCode, ...]
    freqtrade_value: Decimal | None
    exchange_value: Decimal | None
    difference: Decimal | None
    tolerance: Decimal


@dataclass(frozen=True, slots=True)
class ReconciliationGroupResult:
    """Deterministic result for one comparable-record group."""

    instrument: str
    settlement_currency: str
    status: ReconciliationStatus
    reason_codes: tuple[ReconciliationReasonCode, ...]
    freqtrade_record_ids: tuple[str, ...]
    exchange_record_ids: tuple[str, ...]
    comparisons: tuple[FinancialFieldComparison, ...]


_FIELD_ORDER = (
    FinancialField.PRICE_PNL,
    FinancialField.TRADING_FEES,
    FinancialField.FUNDING,
    FinancialField.OTHER_ADJUSTMENTS,
    FinancialField.REPORTED_NET_PNL,
)


def reconcile_group(
    group: ComparableRecordGroup,
    *,
    tolerance: ReconciliationTolerance,
    fields: Sequence[FinancialField] | None = None,
) -> ReconciliationGroupResult:
    """Compare one grouped set without inventing missing data or resolving ambiguity."""

    selected_fields = _normalize_fields(fields)

    if not group.freqtrade_records:
        return _group_result(
            group,
            status=ReconciliationStatus.INCOMPLETE,
            reason_codes=(ReconciliationReasonCode.MISSING_FREQTRADE_RECORD,),
        )

    if len(group.freqtrade_records) > 1:
        return _group_result(
            group,
            status=ReconciliationStatus.REVIEW,
            reason_codes=(ReconciliationReasonCode.AMBIGUOUS_FREQTRADE_RECORDS,),
        )

    if not group.exchange_records:
        return _group_result(
            group,
            status=ReconciliationStatus.INCOMPLETE,
            reason_codes=(ReconciliationReasonCode.MISSING_EXCHANGE_RECORD,),
        )

    freqtrade_record = group.freqtrade_records[0]
    comparisons = tuple(
        _compare_field(
            field,
            freqtrade_record=freqtrade_record,
            exchange_records=group.exchange_records,
            tolerance=tolerance.for_field(field),
        )
        for field in selected_fields
    )
    reason_codes = _unique_reason_codes(comparisons)

    if any(item.status is ReconciliationStatus.REVIEW for item in comparisons):
        status = ReconciliationStatus.REVIEW
    elif any(item.status is ReconciliationStatus.INCOMPLETE for item in comparisons):
        status = ReconciliationStatus.INCOMPLETE
    else:
        status = ReconciliationStatus.MATCH

    return _group_result(
        group,
        status=status,
        reason_codes=reason_codes,
        comparisons=comparisons,
    )


def _group_result(
    group: ComparableRecordGroup,
    *,
    status: ReconciliationStatus,
    reason_codes: tuple[ReconciliationReasonCode, ...],
    comparisons: tuple[FinancialFieldComparison, ...] = (),
) -> ReconciliationGroupResult:
    return ReconciliationGroupResult(
        instrument=group.instrument,
        settlement_currency=group.settlement_currency,
        status=status,
        reason_codes=reason_codes,
        freqtrade_record_ids=tuple(
            sorted(record.record_id for record in group.freqtrade_records)
        ),
        exchange_record_ids=tuple(
            sorted(record.record_id for record in group.exchange_records)
        ),
        comparisons=comparisons,
    )


def _normalize_fields(
    fields: Sequence[FinancialField] | None,
) -> tuple[FinancialField, ...]:
    if fields is None:
        return _FIELD_ORDER

    requested = tuple(fields)
    if not requested:
        raise ValueError("fields must not be empty when provided")
    if len(set(requested)) != len(requested):
        raise ValueError("fields must not contain duplicates")

    requested_set = set(requested)
    return tuple(field for field in _FIELD_ORDER if field in requested_set)


def _compare_field(
    field: FinancialField,
    *,
    freqtrade_record: NormalizedTradeRecord,
    exchange_records: Sequence[NormalizedTradeRecord],
    tolerance: Decimal,
) -> FinancialFieldComparison:
    freqtrade_value = _financial_value(freqtrade_record, field)
    exchange_values = tuple(_financial_value(record, field) for record in exchange_records)

    missing_reasons: list[ReconciliationReasonCode] = []
    if freqtrade_value is None:
        missing_reasons.append(ReconciliationReasonCode.MISSING_FREQTRADE_VALUE)
    if any(value is None for value in exchange_values):
        missing_reasons.append(ReconciliationReasonCode.MISSING_EXCHANGE_VALUE)

    if missing_reasons:
        return FinancialFieldComparison(
            field=field,
            status=ReconciliationStatus.INCOMPLETE,
            reason_codes=tuple(missing_reasons),
            freqtrade_value=freqtrade_value,
            exchange_value=None,
            difference=None,
            tolerance=tolerance,
        )

    if freqtrade_value is None:
        raise AssertionError("freqtrade value unexpectedly missing after validation")

    present_exchange_values = tuple(value for value in exchange_values if value is not None)
    exchange_value = sum(present_exchange_values, Decimal("0"))
    difference = exchange_value - freqtrade_value

    if abs(difference) <= tolerance:
        status = ReconciliationStatus.MATCH
        reason = ReconciliationReasonCode.VALUE_WITHIN_TOLERANCE
    else:
        status = ReconciliationStatus.REVIEW
        reason = ReconciliationReasonCode.VALUE_OUTSIDE_TOLERANCE

    return FinancialFieldComparison(
        field=field,
        status=status,
        reason_codes=(reason,),
        freqtrade_value=freqtrade_value,
        exchange_value=exchange_value,
        difference=difference,
        tolerance=tolerance,
    )


def _financial_value(
    record: NormalizedTradeRecord,
    field: FinancialField,
) -> Decimal | None:
    if field is FinancialField.PRICE_PNL:
        return record.price_pnl
    if field is FinancialField.TRADING_FEES:
        return record.trading_fees
    if field is FinancialField.FUNDING:
        return record.funding
    if field is FinancialField.OTHER_ADJUSTMENTS:
        return record.other_adjustments
    if field is FinancialField.REPORTED_NET_PNL:
        return record.reported_net_pnl
    raise AssertionError(f"unsupported financial field: {field!r}")


def _unique_reason_codes(
    comparisons: Sequence[FinancialFieldComparison],
) -> tuple[ReconciliationReasonCode, ...]:
    seen: set[ReconciliationReasonCode] = set()
    result: list[ReconciliationReasonCode] = []
    for comparison in comparisons:
        for reason in comparison.reason_codes:
            if reason not in seen:
                seen.add(reason)
                result.append(reason)
    return tuple(result)
