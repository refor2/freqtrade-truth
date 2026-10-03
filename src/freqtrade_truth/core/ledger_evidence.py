"""Fail-closed funding evidence collection from normalized ledger windows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from freqtrade_truth.core.reconciliation import ComparableRecordGroup


class FundingLedgerRecord(Protocol):
    """Minimal read-only ledger contract required for funding evidence."""

    @property
    def record_id(self) -> str: ...

    @property
    def instrument(self) -> str | None: ...

    @property
    def settlement_currency(self) -> str: ...

    @property
    def occurred_at(self) -> datetime: ...

    @property
    def funding(self) -> Decimal | None: ...


@dataclass(frozen=True, slots=True)
class LedgerCoverage:
    """Declared complete read window for a ledger result set."""

    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.start_at, "start_at")
        _require_utc(self.end_at, "end_at")
        if self.start_at > self.end_at:
            raise ValueError("start_at must not be after end_at")


class FundingEvidenceStatus(StrEnum):
    """State of time-window funding evidence."""

    AVAILABLE = "available"
    NO_EVENTS = "no_events"
    INCOMPLETE = "incomplete"
    AMBIGUOUS = "ambiguous"


class FundingEvidenceReasonCode(StrEnum):
    """Machine-readable reason for a funding evidence state."""

    FUNDING_EVENTS_FOUND = "funding_events_found"
    NO_FUNDING_EVENTS = "no_funding_events"
    GROUP_NOT_STRUCTURALLY_COMPARABLE = "group_not_structurally_comparable"
    MISSING_FREQTRADE_OPEN_TIME = "missing_freqtrade_open_time"
    LEDGER_COVERAGE_INCOMPLETE = "ledger_coverage_incomplete"
    UNKNOWN_INSTRUMENT_FUNDING_EVENT = "unknown_instrument_funding_event"


@dataclass(frozen=True, slots=True)
class FundingWindowEvidence:
    """Candidate funding evidence for one trade window, not final attribution."""

    instrument: str
    settlement_currency: str
    status: FundingEvidenceStatus
    reason_codes: tuple[FundingEvidenceReasonCode, ...]
    window_start: datetime | None
    window_end: datetime | None
    candidate_funding: Decimal | None
    candidate_record_ids: tuple[str, ...]
    ambiguous_record_ids: tuple[str, ...]


def collect_funding_window_evidence(
    group: ComparableRecordGroup,
    ledger_records: Sequence[FundingLedgerRecord],
    *,
    coverage: LedgerCoverage,
) -> FundingWindowEvidence:
    """Collect funding candidates without claiming ownership or mutating trade records."""

    records = _validated_records(ledger_records, coverage)

    if not group.is_structurally_comparable:
        return _evidence(
            group,
            status=FundingEvidenceStatus.AMBIGUOUS,
            reason_codes=(
                FundingEvidenceReasonCode.GROUP_NOT_STRUCTURALLY_COMPARABLE,
            ),
        )

    trade = group.freqtrade_records[0]
    if trade.opened_at is None:
        return _evidence(
            group,
            status=FundingEvidenceStatus.INCOMPLETE,
            reason_codes=(FundingEvidenceReasonCode.MISSING_FREQTRADE_OPEN_TIME,),
            window_end=trade.closed_at,
        )

    if coverage.start_at > trade.opened_at or coverage.end_at < trade.closed_at:
        return _evidence(
            group,
            status=FundingEvidenceStatus.INCOMPLETE,
            reason_codes=(FundingEvidenceReasonCode.LEDGER_COVERAGE_INCOMPLETE,),
            window_start=trade.opened_at,
            window_end=trade.closed_at,
        )

    relevant = tuple(
        record
        for record in records
        if trade.opened_at <= record.occurred_at <= trade.closed_at
        and record.settlement_currency == group.settlement_currency
        and record.funding is not None
    )

    ambiguous = tuple(
        sorted(record.record_id for record in relevant if record.instrument is None)
    )
    if ambiguous:
        return _evidence(
            group,
            status=FundingEvidenceStatus.AMBIGUOUS,
            reason_codes=(
                FundingEvidenceReasonCode.UNKNOWN_INSTRUMENT_FUNDING_EVENT,
            ),
            window_start=trade.opened_at,
            window_end=trade.closed_at,
            ambiguous_record_ids=ambiguous,
        )

    candidates = tuple(record for record in relevant if record.instrument == group.instrument)
    if not candidates:
        return _evidence(
            group,
            status=FundingEvidenceStatus.NO_EVENTS,
            reason_codes=(FundingEvidenceReasonCode.NO_FUNDING_EVENTS,),
            window_start=trade.opened_at,
            window_end=trade.closed_at,
            candidate_funding=Decimal("0"),
        )

    candidate_funding = sum(
        (record.funding for record in candidates if record.funding is not None),
        Decimal("0"),
    )
    return _evidence(
        group,
        status=FundingEvidenceStatus.AVAILABLE,
        reason_codes=(FundingEvidenceReasonCode.FUNDING_EVENTS_FOUND,),
        window_start=trade.opened_at,
        window_end=trade.closed_at,
        candidate_funding=candidate_funding,
        candidate_record_ids=tuple(sorted(record.record_id for record in candidates)),
    )


def _validated_records(
    records: Sequence[FundingLedgerRecord],
    coverage: LedgerCoverage,
) -> tuple[FundingLedgerRecord, ...]:
    normalized = tuple(records)
    seen: set[str] = set()
    for record in normalized:
        if not record.record_id.strip():
            raise ValueError("ledger record_id must not be empty")
        if record.record_id in seen:
            raise ValueError("ledger records contain a duplicate record_id")
        seen.add(record.record_id)

        _require_utc(record.occurred_at, "ledger occurred_at")
        if record.occurred_at < coverage.start_at or record.occurred_at > coverage.end_at:
            raise ValueError("ledger record falls outside declared coverage")
        if (
            not record.settlement_currency
            or record.settlement_currency != record.settlement_currency.upper()
        ):
            raise ValueError("ledger settlement_currency must be non-empty uppercase")
        if record.funding is not None and not record.funding.is_finite():
            raise ValueError("ledger funding must be finite when present")

    return tuple(
        sorted(
            normalized,
            key=lambda record: (record.occurred_at, record.record_id),
        )
    )


def _evidence(
    group: ComparableRecordGroup,
    *,
    status: FundingEvidenceStatus,
    reason_codes: tuple[FundingEvidenceReasonCode, ...],
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    candidate_funding: Decimal | None = None,
    candidate_record_ids: tuple[str, ...] = (),
    ambiguous_record_ids: tuple[str, ...] = (),
) -> FundingWindowEvidence:
    return FundingWindowEvidence(
        instrument=group.instrument,
        settlement_currency=group.settlement_currency,
        status=status,
        reason_codes=reason_codes,
        window_start=window_start,
        window_end=window_end,
        candidate_funding=candidate_funding,
        candidate_record_ids=candidate_record_ids,
        ambiguous_record_ids=ambiguous_record_ids,
    )


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be normalized to UTC")
