"""Explicitly distinguish absent account bills from a bill query that missed fills.

This analysis diagnoses the bounded READ-only OKX source window, not actual
bill existence or account ownership. It does not issue API calls.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BillWindowStatus(StrEnum):
    COVERED_BY_WINDOW = "covered_by_window"
    OUTSIDE_BILL_WINDOW = "outside_bill_window"
    SOURCE_UNQUALIFIED = "source_unqualified"


@dataclass(frozen=True, slots=True)
class BillWindowResult:
    status: BillWindowStatus
    reason: str
    fills_total: int = 0
    linked_inside: int = 0
    unlinked_before: int = 0
    unlinked_inside: int = 0
    unlinked_after: int = 0
    usd_verified: bool = False
    account_ledger_verified: bool = False


def _ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        n = int(value)
    except ValueError:
        return None
    return n if 0 <= n <= 999999999999999 and str(value).strip() == str(n) else None


def _map(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def verify_okx_bill_query_window(payload: Mapping[str, Any]) -> BillWindowResult:
    """Report missed *query window* vs missing billed evidence.

    A bill timestamp may lag a fill; in-window fill timing is necessary but
    not sufficient evidence that every bill should have been fetched.
    """
    ev = _map(payload.get("exchange_evidence"))
    if ev is None or ev.get("status") != "PASS" or ev.get("read_only") is not True:
        return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "SOURCE_NOT_PASS")
    # New GET-only source records the actual bill-query bounds separately
    # from the historical operational open/close time window.
    window = _map(ev["bill_query_window"] if "bill_query_window" in ev else ev.get("trade_window"))
    fills, bills = ev.get("fills"), ev.get("linked_bills")
    if window is None or not isinstance(fills, list) or not isinstance(bills, list):
        return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "WINDOW_OR_RECORDS_MISSING")
    start, end = _ms(window.get("begin_ms")), _ms(window.get("end_ms"))
    if start is None or end is None or end < start:
        return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "INVALID_BILL_WINDOW")
    bill_keys: set[tuple[str, str]] = set()
    for raw in bills:
        bill = _map(raw)
        if bill is None:
            return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "INVALID_BILL_RECORD")
        oid, tid = bill.get("ord_id"), bill.get("trade_id")
        if not isinstance(oid, str) or not isinstance(tid, str) or not oid or not tid:
            return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "BILL_LINK_KEY_MISSING")
        key = (oid, tid)
        if key in bill_keys:
            return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "DUPLICATE_BILL_KEY")
        bill_keys.add(key)
    linked_inside = unlinked_before = unlinked_inside = unlinked_after = 0
    observed_keys: set[tuple[str, str]] = set()
    for raw in fills:
        fill = _map(raw)
        if fill is None:
            return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "INVALID_FILL")
        oid, tid = fill.get("ord_id"), fill.get("trade_id")
        tm = _ms(fill.get("fill_time"))
        if not isinstance(oid, str) or not isinstance(tid, str) or not oid or not tid or tm is None:
            return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "FILL_ID_OR_TIME_MISSING")
        key = (oid, tid)
        if key in observed_keys:
            return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "DUPLICATE_FILL_KEY")
        observed_keys.add(key)
        if key in bill_keys:
            if not start <= tm <= end:
                return BillWindowResult(
                    BillWindowStatus.SOURCE_UNQUALIFIED, "LINKED_FILL_OUTSIDE_BILL_WINDOW"
                )
            linked_inside += 1
        elif tm < start:
            unlinked_before += 1
        elif tm > end:
            unlinked_after += 1
        else:
            unlinked_inside += 1
    if not bill_keys.issubset(observed_keys):
        return BillWindowResult(BillWindowStatus.SOURCE_UNQUALIFIED, "FOREIGN_LINKED_BILL")
    missing_outside = unlinked_before + unlinked_after
    return BillWindowResult(
        status=(
            BillWindowStatus.OUTSIDE_BILL_WINDOW
            if missing_outside
            else BillWindowStatus.COVERED_BY_WINDOW
        ),
        reason=(
            "BILL_QUERY_EXCLUDES_FILL_TIMESTAMPS"
            if missing_outside
            else "FILL_TIMES_WITHIN_QUERY_WINDOW_NOT_LEDGER_PROOF"
        ),
        fills_total=len(fills),
        linked_inside=linked_inside,
        unlinked_before=unlinked_before,
        unlinked_inside=unlinked_inside,
        unlinked_after=unlinked_after,
    )


def verify_okx_bill_query_window_batch(
    entries: Sequence[tuple[Mapping[str, Any], str]],
) -> dict[str, object]:
    """Bounded, identifier-free aggregate for reports."""
    results = [verify_okx_bill_query_window(p) for p, _side in entries]
    statuses = Counter(r.status.value for r in results)
    return {
        "states": {state.value: statuses[state.value] for state in BillWindowStatus},
        "fills": sum(r.fills_total for r in results),
        "linked_inside": sum(r.linked_inside for r in results),
        "unlinked_before_window": sum(r.unlinked_before for r in results),
        "unlinked_inside_window": sum(r.unlinked_inside for r in results),
        "unlinked_after_window": sum(r.unlinked_after for r in results),
        "account_ledger_verified": False,
        "usd_verified": False,
    }
