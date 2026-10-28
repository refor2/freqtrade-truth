"""Strict *candidate* funding reconciliation from existing OKX GET-only evidence.

This deliberately DOES NOT assert bill ownership, full account coverage or USD
settlement. Time/currency/instrument overlap can identify candidates only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from freqtrade_truth.core.okx_native_verification import (
    PositionNativeResult,
    verify_okx_native_batch,
)


class FundingCandidateStatus(StrEnum):
    CANDIDATE_SUM_EQUAL = "candidate_sum_equal"
    CANDIDATE_SUM_DIFFERENT = "candidate_sum_different"
    MISSING_SOURCE_EVENT = "missing_source_event"
    ZERO_SOURCE_NOT_PROVEN = "zero_source_not_proven"
    UNVERIFIED = "unverified"
    AMBIGUOUS = "ambiguous"
    SOURCE_CONFLICT = "source_conflict"


@dataclass(frozen=True, slots=True)
class FundingCandidateResult:
    status: FundingCandidateStatus
    reason: str
    candidate_count: int = 0
    native_currency: str | None = None
    funding_ownership_verified: bool = False
    full_ledger_coverage_verified: bool = False
    usd_valuation_verified: bool = False


def _result(
    status: FundingCandidateStatus,
    reason: str,
    *,
    count: int = 0,
    currency: str | None = None,
) -> FundingCandidateResult:
    return FundingCandidateResult(status, reason, count, currency)


def _number(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    raw = str(value)
    if not raw or len(raw) > 120:
        return None
    try:
        value_d = Decimal(raw)
    except InvalidOperation:
        return None
    return value_d if value_d.is_finite() and abs(value_d.adjusted()) <= 300 else None


def _millis(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        num = int(value)
    except (ValueError, TypeError):
        return None
    return num if num > 0 and str(value).strip() == str(num) else None


def _position_native_funding(
    payload: Mapping[str, Any], native: PositionNativeResult
) -> Decimal | None:
    if native.cycle_key is None:
        return None
    ev = payload.get("exchange_evidence")
    if not isinstance(ev, Mapping):
        return None
    history = ev.get("position_history")
    if not isinstance(history, list):
        return None
    inst, pos_id, start, end = native.cycle_key
    matches = [
        row
        for row in history
        if isinstance(row, Mapping)
        and row.get("inst_id") == inst
        and row.get("pos_id") == pos_id
        and _millis(row.get("c_time")) == start
        and _millis(row.get("u_time")) == end
    ]
    if len(matches) != 1:
        return None
    return _number(matches[0].get("funding_fee"))


def verify_okx_funding_candidates_batch(
    entries: Sequence[tuple[Mapping[str, Any], str]],
    *,
    tolerance: Decimal,
) -> tuple[FundingCandidateResult, ...]:
    """De-duplicate bill IDs; distinguish candidate arithmetic from ownership.

    The same funding bill in many per-trade GET snapshots is counted once.
    Unknown/non-unique cycles, malformed records, incorrect funding signs,
    missing bill IDs or disputed bill content all fail closed.

    The true OKX account may include manual/unobserved positions and account
    records outside GET pagination/retention. Therefore even an exact
    candidate funding sum never means verified ownership or USD settlement.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("finite nonnegative decimal tolerance required")
    native_rows = verify_okx_native_batch(entries, tolerance=tolerance)
    events: dict[str, tuple[str, str, int, str, Decimal]] = {}
    conflicted: set[str] = set()
    input_problems: set[int] = set()

    for index, (payload, _side) in enumerate(entries):
        ev = payload.get("exchange_evidence")
        if not isinstance(ev, Mapping):
            input_problems.add(index)
            continue
        if (
            ev.get("status") != "PASS"
            or ev.get("identity_verified") is not True
            or ev.get("read_only") is not True
            or ev.get("bill_truncated") is not False
        ):
            input_problems.add(index)
            continue
        bills = ev.get("unlinked_instrument_bills")
        if not isinstance(bills, list):
            input_problems.add(index)
            continue
        for raw in bills:
            if not isinstance(raw, Mapping):
                input_problems.add(index)
                continue
            subtype = str(raw.get("sub_type") or "")
            if subtype not in ("173", "174"):
                continue
            if str(raw.get("type") or "") != "8":
                input_problems.add(index)
                continue
            bill_id = raw.get("bill_id")
            instrument = raw.get("inst_id")
            currency = raw.get("ccy")
            ts = _millis(raw.get("ts"))
            pnl = _number(raw.get("pnl"))
            if (
                not isinstance(bill_id, str)
                or not bill_id
                or not isinstance(instrument, str)
                or not instrument
                or not isinstance(currency, str)
                or not currency
                or ts is None
                or pnl is None
            ):
                input_problems.add(index)
                continue
            # Funding expense (173) is a debit; funding income (174) a credit.
            # A contradictory sign is not interpreted as a different FX rate.
            if (subtype == "173" and pnl > 0) or (subtype == "174" and pnl < 0):
                input_problems.add(index)
                continue
            value = (instrument, currency, ts, subtype, pnl)
            previous = events.get(bill_id)
            if previous is not None and previous != value:
                conflicted.add(bill_id)
            else:
                events[bill_id] = value

    claims: dict[str, list[int]] = defaultdict(list)
    candidate_ids: list[list[str]] = []
    for index, native in enumerate(native_rows):
        if native.cycle_key is None or native.currency is None:
            candidate_ids.append([])
            continue
        inst, _pos_id, start, end = native.cycle_key
        found = [
            bill_id
            for bill_id, (ev_inst, ccy, ts, _sub, _pnl) in events.items()
            if ev_inst == inst and ccy == native.currency and start <= ts <= end
        ]
        candidate_ids.append(found)
        for bill_id in found:
            claims[bill_id].append(index)

    results: list[FundingCandidateResult] = []
    for index, (payload, _side) in enumerate(entries):
        native = native_rows[index]
        ids = candidate_ids[index]
        if native.status.value == "conflict":
            results.append(
                _result(
                    FundingCandidateStatus.SOURCE_CONFLICT, "POSITION_CYCLE_CLAIMED_MULTIPLE_TIMES"
                )
            )
            continue
        if native.cycle_key is None or native.currency is None:
            results.append(
                _result(FundingCandidateStatus.UNVERIFIED, "STRICT_SOURCE_CYCLE_NOT_VERIFIED")
            )
            continue
        if index in input_problems:
            results.append(
                _result(
                    FundingCandidateStatus.UNVERIFIED,
                    "SOURCE_FUNDING_BILL_DATA_INCOMPLETE",
                    currency=native.currency,
                )
            )
            continue
        if any(bill_id in conflicted for bill_id in ids):
            results.append(
                _result(
                    FundingCandidateStatus.SOURCE_CONFLICT,
                    "DUPLICATE_BILL_ID_DIFFERENT_CONTENT",
                    count=len(ids),
                    currency=native.currency,
                )
            )
            continue
        if any(len(claims[bill_id]) > 1 for bill_id in ids):
            results.append(
                _result(
                    FundingCandidateStatus.AMBIGUOUS,
                    "FUNDING_BILL_CANDIDATE_MULTIPLE_CYCLES",
                    count=len(ids),
                    currency=native.currency,
                )
            )
            continue
        funding = _position_native_funding(payload, native)
        if funding is None:
            results.append(
                _result(
                    FundingCandidateStatus.UNVERIFIED,
                    "POSITION_FUNDING_COMPONENT_MISSING",
                    count=len(ids),
                    currency=native.currency,
                )
            )
            continue
        if not ids:
            results.append(
                _result(
                    FundingCandidateStatus.MISSING_SOURCE_EVENT
                    if funding != 0
                    else FundingCandidateStatus.ZERO_SOURCE_NOT_PROVEN,
                    "NONZERO_POSITION_FUNDING_WITH_NO_SOURCE_EVENT"
                    if funding != 0
                    else "ZERO_WITH_UNPROVEN_ACCOUNT_COVERAGE",
                    currency=native.currency,
                )
            )
            continue
        candidate_sum = sum((events[bill_id][4] for bill_id in ids), Decimal(0))
        equal = abs(candidate_sum - funding) <= tolerance
        results.append(
            _result(
                FundingCandidateStatus.CANDIDATE_SUM_EQUAL
                if equal
                else FundingCandidateStatus.CANDIDATE_SUM_DIFFERENT,
                "FUNDING_SUM_CANDIDATE_ONLY_NOT_OWNERSHIP"
                if equal
                else "FUNDING_CANDIDATE_SUM_DIFFERENT_OR_COVERAGE_INCOMPLETE",
                count=len(ids),
                currency=native.currency,
            )
        )
    return tuple(results)
