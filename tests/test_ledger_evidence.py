from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from freqtrade_truth.core import (
    ComparableRecordGroup,
    FundingEvidenceReasonCode,
    FundingEvidenceStatus,
    LedgerCoverage,
    NormalizedTradeRecord,
    SourceKind,
    collect_funding_window_evidence,
)


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    record_id: str
    instrument: str | None
    settlement_currency: str
    occurred_at: datetime
    funding: Decimal | None


def trade(
    record_id: str,
    *,
    opened_at: datetime | None,
    closed_at: datetime,
) -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id=record_id,
        source_kind=SourceKind.FREQTRADE,
        source_name="synthetic-freqtrade",
        trade_ref=record_id,
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        opened_at=opened_at,
        closed_at=closed_at,
    )


def exchange(record_id: str, *, closed_at: datetime) -> NormalizedTradeRecord:
    return NormalizedTradeRecord(
        record_id=record_id,
        source_kind=SourceKind.EXCHANGE,
        source_name="synthetic-exchange",
        trade_ref=record_id,
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        closed_at=closed_at,
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


def base_times() -> tuple[datetime, datetime, LedgerCoverage]:
    opened_at = datetime(2030, 1, 1, 11, 30, tzinfo=UTC)
    closed_at = datetime(2030, 1, 1, 12, 30, tzinfo=UTC)
    coverage = LedgerCoverage(
        start_at=datetime(2030, 1, 1, 11, tzinfo=UTC),
        end_at=datetime(2030, 1, 1, 13, tzinfo=UTC),
    )
    return opened_at, closed_at, coverage


def test_collects_exact_instrument_funding_candidates() -> None:
    opened_at, closed_at, coverage = base_times()
    target = group(
        (trade("bot-1", opened_at=opened_at, closed_at=closed_at),),
        (exchange("exchange-1", closed_at=closed_at),),
    )
    records = (
        LedgerRecord(
            record_id="funding-2",
            instrument="BTC/USD:BTC",
            settlement_currency="BTC",
            occurred_at=datetime(2030, 1, 1, 12, 15, tzinfo=UTC),
            funding=Decimal("-0.002"),
        ),
        LedgerRecord(
            record_id="other-instrument",
            instrument="ETH/USD:BTC",
            settlement_currency="BTC",
            occurred_at=datetime(2030, 1, 1, 12, 10, tzinfo=UTC),
            funding=Decimal("9"),
        ),
        LedgerRecord(
            record_id="funding-1",
            instrument="BTC/USD:BTC",
            settlement_currency="BTC",
            occurred_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
            funding=Decimal("0.010"),
        ),
    )

    evidence = collect_funding_window_evidence(
        target,
        records,
        coverage=coverage,
    )

    assert evidence.status is FundingEvidenceStatus.AVAILABLE
    assert evidence.reason_codes == (FundingEvidenceReasonCode.FUNDING_EVENTS_FOUND,)
    assert evidence.window_start == opened_at
    assert evidence.window_end == closed_at
    assert evidence.candidate_funding == Decimal("0.008")
    assert evidence.candidate_record_ids == ("funding-1", "funding-2")
    assert evidence.ambiguous_record_ids == ()


def test_unknown_instrument_funding_event_makes_window_ambiguous() -> None:
    opened_at, closed_at, coverage = base_times()
    target = group(
        (trade("bot-1", opened_at=opened_at, closed_at=closed_at),),
        (exchange("exchange-1", closed_at=closed_at),),
    )
    records = (
        LedgerRecord(
            record_id="unknown-symbol",
            instrument=None,
            settlement_currency="BTC",
            occurred_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
            funding=Decimal("0.001"),
        ),
        LedgerRecord(
            record_id="known-symbol",
            instrument="BTC/USD:BTC",
            settlement_currency="BTC",
            occurred_at=datetime(2030, 1, 1, 12, 5, tzinfo=UTC),
            funding=Decimal("0.002"),
        ),
    )

    evidence = collect_funding_window_evidence(
        target,
        records,
        coverage=coverage,
    )

    assert evidence.status is FundingEvidenceStatus.AMBIGUOUS
    assert evidence.reason_codes == (FundingEvidenceReasonCode.UNKNOWN_INSTRUMENT_FUNDING_EVENT,)
    assert evidence.candidate_funding is None
    assert evidence.candidate_record_ids == ()
    assert evidence.ambiguous_record_ids == ("unknown-symbol",)


def test_complete_window_with_no_funding_events_reports_zero_candidate() -> None:
    opened_at, closed_at, coverage = base_times()
    target = group(
        (trade("bot-1", opened_at=opened_at, closed_at=closed_at),),
        (exchange("exchange-1", closed_at=closed_at),),
    )
    records = (
        LedgerRecord(
            record_id="trade-fee-only",
            instrument="BTC/USD:BTC",
            settlement_currency="BTC",
            occurred_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
            funding=None,
        ),
    )

    evidence = collect_funding_window_evidence(
        target,
        records,
        coverage=coverage,
    )

    assert evidence.status is FundingEvidenceStatus.NO_EVENTS
    assert evidence.reason_codes == (FundingEvidenceReasonCode.NO_FUNDING_EVENTS,)
    assert evidence.candidate_funding == Decimal("0")
    assert evidence.candidate_record_ids == ()


def test_incomplete_coverage_does_not_claim_zero_or_partial_funding() -> None:
    opened_at, closed_at, _ = base_times()
    target = group(
        (trade("bot-1", opened_at=opened_at, closed_at=closed_at),),
        (exchange("exchange-1", closed_at=closed_at),),
    )
    coverage = LedgerCoverage(
        start_at=opened_at + timedelta(minutes=1),
        end_at=closed_at,
    )

    evidence = collect_funding_window_evidence(
        target,
        (),
        coverage=coverage,
    )

    assert evidence.status is FundingEvidenceStatus.INCOMPLETE
    assert evidence.reason_codes == (FundingEvidenceReasonCode.LEDGER_COVERAGE_INCOMPLETE,)
    assert evidence.candidate_funding is None


def test_missing_trade_open_time_is_incomplete() -> None:
    _, closed_at, coverage = base_times()
    target = group(
        (trade("bot-1", opened_at=None, closed_at=closed_at),),
        (exchange("exchange-1", closed_at=closed_at),),
    )

    evidence = collect_funding_window_evidence(
        target,
        (),
        coverage=coverage,
    )

    assert evidence.status is FundingEvidenceStatus.INCOMPLETE
    assert evidence.reason_codes == (FundingEvidenceReasonCode.MISSING_FREQTRADE_OPEN_TIME,)
    assert evidence.window_start is None
    assert evidence.window_end == closed_at


def test_non_comparable_groups_do_not_attempt_attribution() -> None:
    opened_at, closed_at, coverage = base_times()
    bot_a = trade("bot-1", opened_at=opened_at, closed_at=closed_at)
    bot_b = trade("bot-2", opened_at=opened_at, closed_at=closed_at)
    exchange_record = exchange("exchange-1", closed_at=closed_at)

    ambiguous = collect_funding_window_evidence(
        group((bot_a, bot_b), (exchange_record,)),
        (),
        coverage=coverage,
    )
    incomplete = collect_funding_window_evidence(
        group((bot_a,), ()),
        (),
        coverage=coverage,
    )

    assert ambiguous.status is FundingEvidenceStatus.AMBIGUOUS
    assert incomplete.status is FundingEvidenceStatus.INCOMPLETE
    assert (
        ambiguous.reason_codes
        == incomplete.reason_codes
        == (FundingEvidenceReasonCode.GROUP_NOT_STRUCTURALLY_COMPARABLE,)
    )


def test_ledger_records_must_be_unique_and_inside_declared_coverage() -> None:
    opened_at, closed_at, coverage = base_times()
    target = group(
        (trade("bot-1", opened_at=opened_at, closed_at=closed_at),),
        (exchange("exchange-1", closed_at=closed_at),),
    )
    duplicate = LedgerRecord(
        record_id="duplicate",
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        occurred_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
        funding=Decimal("0.001"),
    )

    with pytest.raises(ValueError, match="duplicate"):
        collect_funding_window_evidence(
            target,
            (duplicate, duplicate),
            coverage=coverage,
        )

    outside = LedgerRecord(
        record_id="outside",
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        occurred_at=coverage.end_at + timedelta(seconds=1),
        funding=Decimal("0.001"),
    )
    with pytest.raises(ValueError, match="outside"):
        collect_funding_window_evidence(
            target,
            (outside,),
            coverage=coverage,
        )


def test_ledger_input_order_does_not_change_evidence() -> None:
    opened_at, closed_at, coverage = base_times()
    target = group(
        (trade("bot-1", opened_at=opened_at, closed_at=closed_at),),
        (exchange("exchange-1", closed_at=closed_at),),
    )
    first = LedgerRecord(
        record_id="funding-1",
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        occurred_at=datetime(2030, 1, 1, 12, tzinfo=UTC),
        funding=Decimal("0.001"),
    )
    second = LedgerRecord(
        record_id="funding-2",
        instrument="BTC/USD:BTC",
        settlement_currency="BTC",
        occurred_at=datetime(2030, 1, 1, 12, 5, tzinfo=UTC),
        funding=Decimal("-0.0002"),
    )

    forward = collect_funding_window_evidence(
        target,
        (first, second),
        coverage=coverage,
    )
    backward = collect_funding_window_evidence(
        target,
        (second, first),
        coverage=coverage,
    )

    assert backward == forward
