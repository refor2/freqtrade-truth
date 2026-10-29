"""Read-only, fail-closed OKX fill-to-bill fee evidence.

Uses the existing strict native cycle verifier; never infers bill ownership by
time or promotes USDC to USD. A missing bill is INCOMPLETE, never zero.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from freqtrade_truth.core.okx_native_verification import (
    verify_okx_position_native,
)


class FeeEvidenceStatus(StrEnum):
    MATCH = "match"
    DIFFERENT = "different"
    INCOMPLETE = "incomplete"
    AMBIGUOUS = "ambiguous"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class FeeEvidenceResult:
    status: FeeEvidenceStatus
    reason: str
    fill_count: int = 0
    linked_bill_count: int = 0
    matched_count: int = 0
    currency: str | None = None
    bill_ids: tuple[str, ...] = ()
    usd_verified: bool = False
    account_verified_by_library: bool = False


def _fail(status: FeeEvidenceStatus, reason: str) -> FeeEvidenceResult:
    return FeeEvidenceResult(status, reason)


def _money(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    text = str(value)
    if not text or len(text) > 120:
        return None
    try:
        n = Decimal(text)
    except InvalidOperation:
        return None
    return n if n.is_finite() and abs(n.adjusted()) <= 300 else None


def _record(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def verify_okx_trade_fee_bills(
    payload: Mapping[str, Any], *, side: str, tolerance: Decimal
) -> FeeEvidenceResult:
    """Check exact fill IDs, fee currency, and source-native fee accounting.

    This is additional evidence, NOT an account authentication oracle. The
    sanitized payload must come from a separately authenticated GET-only source.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("finite nonnegative native tolerance required")
    native = verify_okx_position_native(payload, side=side, tolerance=tolerance)
    if native.cycle_key is None or native.currency is None:
        return _fail(FeeEvidenceStatus.UNVERIFIED, "STRICT_POSITION_CYCLE_UNVERIFIED")
    ev = _record(payload.get("exchange_evidence"))
    if ev is None:
        return _fail(FeeEvidenceStatus.UNVERIFIED, "EXCHANGE_EVIDENCE_MISSING")
    fills = ev.get("fills")
    bills = ev.get("linked_bills")
    if not isinstance(fills, list) or not isinstance(bills, list):
        return _fail(FeeEvidenceStatus.INCOMPLETE, "FILL_OR_BILL_SOURCE_INCOMPLETE")
    if not fills:
        return _fail(FeeEvidenceStatus.INCOMPLETE, "NO_FILL_FEES")
    if not bills:
        return FeeEvidenceResult(
            FeeEvidenceStatus.INCOMPLETE,
            "TRADE_BILLS_NOT_FOUND",
            fill_count=len(fills),
            currency=native.currency,
        )
    instrument = native.cycle_key[0]
    expected: dict[tuple[str, str], Decimal] = {}
    direct_bill_ids: dict[tuple[str, str], str] = {}
    for item in fills:
        fill = _record(item)
        if fill is None:
            return _fail(FeeEvidenceStatus.UNVERIFIED, "FILL_RECORD_INVALID")
        oid, tid = fill.get("ord_id"), fill.get("trade_id")
        money = _money(fill.get("fee"))
        if (
            not isinstance(oid, str)
            or not oid
            or not isinstance(tid, str)
            or not tid
            or fill.get("inst_id") != instrument
            or fill.get("fee_ccy") != native.currency
            or money is None
        ):
            return _fail(FeeEvidenceStatus.INCOMPLETE, "FILL_FEE_OR_CURRENCY_UNQUALIFIED")
        key = (oid, tid)
        if key in expected:
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "DUPLICATE_FILL_FEE_KEY")
        expected[key] = money
        # The optional exchange fills-history billId gives a stronger independent
        # cross-ledger assertion. Legacy sanitized payloads omit it entirely.
        if "bill_id" in fill:
            fill_bill_id = fill["bill_id"]
            if not isinstance(fill_bill_id, str) or not fill_bill_id:
                return _fail(FeeEvidenceStatus.INCOMPLETE, "FILL_BILL_ID_COVERAGE_INCOMPLETE")
            if fill_bill_id in direct_bill_ids.values():
                return _fail(FeeEvidenceStatus.AMBIGUOUS, "DUPLICATE_FILL_BILL_ID")
            direct_bill_ids[key] = fill_bill_id
    if direct_bill_ids and len(direct_bill_ids) != len(expected):
        return _fail(FeeEvidenceStatus.INCOMPLETE, "FILL_BILL_ID_COVERAGE_INCOMPLETE")
    matched: dict[tuple[str, str], Decimal] = {}
    ids: set[str] = set()
    for item in bills:
        bill = _record(item)
        if bill is None:
            return _fail(FeeEvidenceStatus.UNVERIFIED, "BILL_RECORD_INVALID")
        oid, tid, bid = bill.get("ord_id"), bill.get("trade_id"), bill.get("bill_id")
        if not isinstance(bid, str) or not bid or bid in ids:
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "BILL_ID_INVALID_OR_DUPLICATE")
        ids.add(bid)
        if (
            bill.get("sub_type") not in ("1", "2")
            or bill.get("ccy") != native.currency
            or bill.get("inst_id") != instrument
            or not isinstance(oid, str)
            or not isinstance(tid, str)
            or (oid, tid) not in expected
        ):
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "TRADE_BILL_LINKAGE_UNQUALIFIED")
        if direct_bill_ids and direct_bill_ids[(oid, tid)] != bid:
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "FILL_BILL_ID_DISAGREES")
        amount = _money(bill.get("fee"))
        if amount is None:
            return _fail(FeeEvidenceStatus.INCOMPLETE, "TRADE_BILL_FEE_MISSING")
        key = (oid, tid)
        if key in matched:
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "MULTIPLE_BILLS_PER_FILL_UNQUALIFIED")
        matched[key] = amount
    if any(abs(expected[key] - amount) > tolerance for key, amount in matched.items()):
        return FeeEvidenceResult(
            FeeEvidenceStatus.DIFFERENT,
            "MATCHED_FILL_BILL_FEE_DIFFERENT",
            fill_count=len(fills),
            linked_bill_count=len(bills),
            matched_count=len(matched),
            currency=native.currency,
            bill_ids=tuple(sorted(ids)),
        )
    if set(expected) != set(matched):
        return FeeEvidenceResult(
            FeeEvidenceStatus.INCOMPLETE,
            "ONE_OR_MORE_FILL_BILLS_MISSING",
            fill_count=len(fills),
            linked_bill_count=len(bills),
            matched_count=len(matched),
            currency=native.currency,
            bill_ids=tuple(sorted(ids)),
        )
    # The position was strictly selected by the existing audited core.
    position_history = ev.get("position_history")
    cycle = native.cycle_key
    matching = (
        [
            pos
            for pos in position_history
            if isinstance(pos, Mapping)
            and (pos.get("inst_id"), pos.get("pos_id")) == cycle[:2]
            and str(pos.get("c_time")) == str(cycle[2])
            and str(pos.get("u_time")) == str(cycle[3])
        ]
        if isinstance(position_history, list)
        else []
    )
    if len(matching) != 1:
        return _fail(FeeEvidenceStatus.UNVERIFIED, "SOURCE_POSITION_ROW_NOT_UNIQUE")
    position_fee = _money(matching[0].get("fee"))
    if position_fee is None:
        return _fail(FeeEvidenceStatus.INCOMPLETE, "POSITION_FEE_MISSING")
    bill_total = sum(matched.values(), Decimal(0))
    if abs(bill_total - position_fee) > tolerance:
        return FeeEvidenceResult(
            FeeEvidenceStatus.DIFFERENT,
            "BILL_FEE_POSITION_FEE_DIFFERENT",
            fill_count=len(fills),
            linked_bill_count=len(bills),
            matched_count=len(matched),
            currency=native.currency,
            bill_ids=tuple(sorted(ids)),
        )
    return FeeEvidenceResult(
        FeeEvidenceStatus.MATCH,
        "COMPLETE_NATIVE_TRADE_FEE_EQUATION",
        fill_count=len(fills),
        linked_bill_count=len(bills),
        matched_count=len(matched),
        currency=native.currency,
        bill_ids=tuple(sorted(ids)),
    )


def verify_okx_trade_fee_bill_batch(
    entries: Sequence[tuple[Mapping[str, Any], str]], *, tolerance: Decimal
) -> tuple[FeeEvidenceResult, ...]:
    """Reject cross-trade duplicate bill ID claims; never double count."""
    results = tuple(
        verify_okx_trade_fee_bills(payload, side=side, tolerance=tolerance)
        for payload, side in entries
    )
    from freqtrade_truth.core.okx_native_verification import verify_okx_native_batch

    native = verify_okx_native_batch(entries, tolerance=tolerance)
    claimed = Counter(bid for result in results for bid in result.bill_ids)
    return tuple(
        replace(
            result,
            status=FeeEvidenceStatus.CONFLICT,
            reason="POSITION_CYCLE_CLAIMED_BY_MULTIPLE_TRADES",
            bill_ids=(),
        )
        if source.status.value == "conflict"
        else replace(
            result,
            status=FeeEvidenceStatus.CONFLICT,
            reason="BILL_ID_CLAIMED_BY_MULTIPLE_TRADES",
            bill_ids=(),
        )
        if any(claimed[bid] > 1 for bid in result.bill_ids)
        else result
        for source, result in zip(native, results, strict=True)
    )


def verify_okx_fill_position_fees(
    payload: Mapping[str, Any], *, side: str, tolerance: Decimal
) -> FeeEvidenceResult:
    """Independently reconcile exact exchange fill fees to native position fee.

    Neither bill availability nor missing ledger entries are silently used as
    zero. This verifies signed source-native fees, not full bill ownership.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("finite nonnegative native tolerance required")
    native = verify_okx_position_native(payload, side=side, tolerance=tolerance)
    if native.cycle_key is None or native.currency is None:
        return _fail(FeeEvidenceStatus.UNVERIFIED, "STRICT_POSITION_CYCLE_UNVERIFIED")
    ev = _record(payload.get("exchange_evidence"))
    if ev is None:
        return _fail(FeeEvidenceStatus.UNVERIFIED, "EXCHANGE_EVIDENCE_MISSING")
    rows = ev.get("position_history")
    fills = ev.get("fills")
    if not isinstance(rows, list) or not isinstance(fills, list) or not fills:
        return _fail(FeeEvidenceStatus.INCOMPLETE, "FILL_OR_POSITION_FEE_SOURCE_INCOMPLETE")
    cycle = native.cycle_key
    candidates = [
        p
        for p in rows
        if isinstance(p, Mapping)
        and (p.get("inst_id"), p.get("pos_id")) == cycle[:2]
        and str(p.get("c_time")) == str(cycle[2])
        and str(p.get("u_time")) == str(cycle[3])
    ]
    if len(candidates) != 1:
        return _fail(FeeEvidenceStatus.AMBIGUOUS, "POSITION_FEE_ROW_NOT_UNIQUE")
    position_fee = _money(candidates[0].get("fee"))
    if position_fee is None:
        return _fail(FeeEvidenceStatus.INCOMPLETE, "POSITION_FEE_MISSING")
    fee_values: list[Decimal] = []
    seen: set[tuple[str, str]] = set()
    for raw in fills:
        fill = _record(raw)
        if fill is None:
            return _fail(FeeEvidenceStatus.UNVERIFIED, "FILL_RECORD_INVALID")
        oid, tid = fill.get("ord_id"), fill.get("trade_id")
        fee = _money(fill.get("fee"))
        if (
            not isinstance(oid, str)
            or not oid
            or not isinstance(tid, str)
            or not tid
            or (oid, tid) in seen
            or fill.get("inst_id") != cycle[0]
            or fill.get("fee_ccy") != native.currency
            or fee is None
        ):
            return _fail(FeeEvidenceStatus.INCOMPLETE, "FILL_FEE_COVERAGE_UNKNOWN")
        seen.add((oid, tid))
        fee_values.append(fee)
    difference = sum(fee_values, Decimal(0)) - position_fee
    if abs(difference) > tolerance:
        return FeeEvidenceResult(
            FeeEvidenceStatus.DIFFERENT,
            "FILL_FEE_POSITION_FEE_DIFFERENT",
            fill_count=len(fills),
            matched_count=len(fills),
            currency=native.currency,
        )
    return FeeEvidenceResult(
        FeeEvidenceStatus.MATCH,
        "FILL_FEE_POSITION_FEE_MATCH",
        fill_count=len(fills),
        matched_count=len(fills),
        currency=native.currency,
    )


def verify_okx_fill_position_fee_batch(
    entries: Sequence[tuple[Mapping[str, Any], str]], *, tolerance: Decimal
) -> tuple[FeeEvidenceResult, ...]:
    """Do not treat a position cycle claimed by two trade rows as matched twice."""
    from freqtrade_truth.core.okx_native_verification import verify_okx_native_batch

    native = verify_okx_native_batch(entries, tolerance=tolerance)
    results = tuple(
        verify_okx_fill_position_fees(payload, side=side, tolerance=tolerance)
        for payload, side in entries
    )
    return tuple(
        replace(
            result,
            status=FeeEvidenceStatus.CONFLICT,
            reason="POSITION_CYCLE_CLAIMED_BY_MULTIPLE_TRADES",
        )
        if source.status.value == "conflict"
        else result
        for source, result in zip(native, results, strict=True)
    )


def verify_okx_direct_fill_fee_ledger(
    payload: Mapping[str, Any], *, side: str, tolerance: Decimal
) -> FeeEvidenceResult:
    """Verify native signed fee & currency for exact fillId -> billId links.

    Requires strict exchange order/fill preflight and source completion, but
    intentionally does not depend on OKX position-history cTime. A native
    position lifecycle that is UNKNOWN stays UNKNOWN: this only verifies
    direct per-fill accounting evidence for a separately authenticated reader.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("finite nonnegative native tolerance required")
    native = verify_okx_position_native(payload, side=side, tolerance=tolerance)
    if native.cycle_key is None and native.reason != "POSITION_CYCLE_NOT_FOUND":
        return _fail(FeeEvidenceStatus.UNVERIFIED, "STRICT_EXCHANGE_ORDER_SOURCE_UNQUALIFIED")
    ev = _record(payload.get("exchange_evidence"))
    if ev is None:
        return _fail(FeeEvidenceStatus.UNVERIFIED, "EXCHANGE_EVIDENCE_MISSING")
    fills = ev.get("fills")
    bills = ev.get("linked_bills")
    if not isinstance(fills, list) or not isinstance(bills, list) or not fills:
        return _fail(FeeEvidenceStatus.INCOMPLETE, "DIRECT_FILL_OR_ACCOUNT_BILL_MISSING")
    inst = ev.get("inst_id")
    if not isinstance(inst, str) or not inst:
        return _fail(FeeEvidenceStatus.UNVERIFIED, "INSTRUMENT_UNQUALIFIED")

    expected: dict[tuple[str, str], tuple[str, str, Decimal]] = {}
    seen_ids: set[str] = set()
    currency: str | None = None
    for raw in fills:
        fill = _record(raw)
        if fill is None:
            return _fail(FeeEvidenceStatus.UNVERIFIED, "FILL_RECORD_INVALID")
        oid, tid, bid = fill.get("ord_id"), fill.get("trade_id"), fill.get("bill_id")
        ccy, fee = fill.get("fee_ccy"), _money(fill.get("fee"))
        if (
            not isinstance(oid, str)
            or not oid
            or not isinstance(tid, str)
            or not tid
            or fill.get("inst_id") != inst
            or not isinstance(ccy, str)
            or not ccy
        ):
            return _fail(FeeEvidenceStatus.INCOMPLETE, "FILL_FEE_OR_CURRENCY_UNQUALIFIED")
        if not isinstance(bid, str) or not bid or fee is None:
            return _fail(FeeEvidenceStatus.INCOMPLETE, "DIRECT_FILL_BILLID_OR_FEE_MISSING")
        if currency is not None and ccy != currency:
            return _fail(FeeEvidenceStatus.INCOMPLETE, "MIXED_NATIVE_FEE_CURRENCIES")
        currency = ccy
        key = (oid, tid)
        if key in expected or bid in seen_ids:
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "DUPLICATE_FILL_OR_BILL_ID")
        seen_ids.add(bid)
        expected[key] = (bid, ccy, fee)

    claimed: dict[tuple[str, str], Decimal] = {}
    observed_ids: set[str] = set()
    for raw in bills:
        bill = _record(raw)
        if bill is None:
            return _fail(FeeEvidenceStatus.UNVERIFIED, "ACCOUNT_BILL_RECORD_INVALID")
        oid, tid, bid = bill.get("ord_id"), bill.get("trade_id"), bill.get("bill_id")
        if (
            not isinstance(oid, str)
            or not oid
            or not isinstance(tid, str)
            or not tid
            or not isinstance(bid, str)
            or not bid
        ):
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "ACCOUNT_BILL_LINK_UNQUALIFIED")
        key = (oid, tid)
        if key not in expected or key in claimed or bid in observed_ids:
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "EXTRA_OR_DUPLICATE_ACCOUNT_BILL")
        observed_ids.add(bid)
        original_id, original_ccy, original_fee = expected[key]
        if bid != original_id:
            return _fail(FeeEvidenceStatus.AMBIGUOUS, "FILL_BILL_ID_DISAGREES")
        if (
            bill.get("inst_id") != inst
            or bill.get("sub_type") not in ("1", "2")
            or bill.get("ccy") != original_ccy
        ):
            return _fail(FeeEvidenceStatus.INCOMPLETE, "ACCOUNT_BILL_CURRENCY_OR_TYPE_UNKNOWN")
        money = _money(bill.get("fee"))
        if money is None:
            return _fail(FeeEvidenceStatus.INCOMPLETE, "ACCOUNT_BILL_FEE_MISSING")
        claimed[key] = money
        if abs(original_fee - money) > tolerance:
            return FeeEvidenceResult(
                FeeEvidenceStatus.DIFFERENT,
                "EXACT_FILL_BILL_FEE_DIFFERENT",
                fill_count=len(fills),
                linked_bill_count=len(bills),
                matched_count=len(claimed),
                currency=currency,
            )
    if set(expected) != set(claimed):
        return FeeEvidenceResult(
            FeeEvidenceStatus.INCOMPLETE,
            "ONE_OR_MORE_EXACT_ACCOUNT_BILLS_MISSING",
            fill_count=len(fills),
            linked_bill_count=len(bills),
            matched_count=len(claimed),
            currency=currency,
        )
    return FeeEvidenceResult(
        FeeEvidenceStatus.MATCH,
        "EXACT_FILL_ACCOUNT_BILL_LINK_AND_FEE",
        fill_count=len(fills),
        linked_bill_count=len(bills),
        matched_count=len(claimed),
        currency=currency,
        bill_ids=tuple(sorted(observed_ids)),
    )


def verify_okx_direct_fill_fee_ledger_batch(
    entries: Sequence[tuple[Mapping[str, Any], str]], *, tolerance: Decimal
) -> tuple[FeeEvidenceResult, ...]:
    """Keep exchange billId ownership disjoint across distinct Freqtrade trades."""
    results = tuple(
        verify_okx_direct_fill_fee_ledger(payload, side=side, tolerance=tolerance)
        for payload, side in entries
    )
    counts = Counter(bill_id for row in results for bill_id in row.bill_ids)
    return tuple(
        replace(
            row,
            status=FeeEvidenceStatus.CONFLICT,
            reason="ACCOUNT_BILL_CLAIMED_BY_MULTIPLE_TRADES",
            bill_ids=(),
        )
        if any(counts[bid] > 1 for bid in row.bill_ids)
        else row
        for row in results
    )
