"""Audit authenticated, sanitized OKX position-cycle evidence read-only.

A consistent result certifies only source-native equation of one strictly
matched position cycle. It is never USD parity or ownership of ledger bills.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from freqtrade_truth.core.models import NormalizedTradeRecord, SourceKind
from freqtrade_truth.core.native_accounting import audit_native_record


class PositionNativeStatus(StrEnum):
    CONSISTENT = "consistent"
    DIFFERENT = "different"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class PositionNativeResult:
    status: PositionNativeStatus
    reason: str
    currency: str | None = None
    difference: Decimal | None = None
    cycle_key: tuple[str, str, int, int] | None = None
    usd_verified: bool = False
    account_verified_by_library: bool = False


def _unknown(reason: str) -> PositionNativeResult:
    return PositionNativeResult(PositionNativeStatus.UNKNOWN, reason)


def _ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        n = int(value)
    except ValueError:
        return None
    return n if n >= 0 and str(value).strip() == str(n) else None


def _num(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        return None
    return n if n.is_finite() and abs(n.adjusted()) <= 300 else None


def _obj(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def verify_okx_position_native(
    payload: Mapping[str, Any], *, side: str, tolerance: Decimal
) -> PositionNativeResult:
    """Strictly link every expected Freqtrade order to OKX fills and one cycle.

    The provider must independently authenticate and scope the account.
    Provided flags from an untrusted JSON file are NOT account proof.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("finite nonnegative decimal tolerance required")
    if not isinstance(side, str) or side.lower() not in ("long", "short"):
        return _unknown("DIRECTION_UNKNOWN")
    if (
        payload.get("financial_truth") is not False
        or payload.get("linkage") != "STRONG_EXCHANGE_ORDER_ID"
        or payload.get("status") != "PASS"
        or payload.get("read_only") is not True
    ):
        return _unknown("SOURCE_UNQUALIFIED")
    ev = _obj(payload.get("exchange_evidence"))
    if (
        ev is None
        or ev.get("status") != "PASS"
        or ev.get("read_only") is not True
        or ev.get("identity_verified") is not True
    ):
        return _unknown("EXCHANGE_SOURCE_UNQUALIFIED")
    if any(
        ev.get(k) is not False
        for k in ("fill_truncated", "bill_truncated", "position_history_truncated")
    ):
        return _unknown("SOURCE_PAGINATION_UNQUALIFIED")
    inst = ev.get("inst_id")
    if not isinstance(inst, str) or not inst:
        return _unknown("INSTRUMENT_UNQUALIFIED")
    expected = payload.get("exchange_order_ids")
    orders = payload.get("exchange_orders")
    if not isinstance(expected, list) or not isinstance(orders, list) or not expected:
        return _unknown("ORDERS_UNQUALIFIED")
    if any(not isinstance(i, str) or not i for i in expected):
        return _unknown("ORDER_ID_INVALID")
    if len(expected) != len(set(expected)):
        return _unknown("ORDER_ID_DUPLICATE")
    roles: dict[str, str] = {}
    ft_sides: dict[str, str] = {}
    for raw in orders:
        row = _obj(raw)
        if row is None:
            return _unknown("ORDER_INVALID")
        oid, role = row.get("order_id"), row.get("role")
        if not isinstance(oid, str) or not oid or role not in ("ENTRY", "EXIT") or oid in roles:
            return _unknown("ORDER_ROLE_INVALID")
        roles[oid] = role
        ft_sides[oid] = str(row.get("ft_order_side") or "")
    if set(expected) != set(roles):
        return _unknown("ORDER_ROLE_INCOMPLETE")

    aliases: dict[str, str] = {}
    required_triggers: dict[str, set[str]] = {}
    links = ev.get("algo_order_links")
    if not isinstance(links, list):
        return _unknown("ALGO_LINKS_UNKNOWN")
    for raw in links:
        link = _obj(raw)
        if link is None:
            return _unknown("ALGO_LINK_INVALID")
        src, targets = link.get("source_order_id"), link.get("triggered_order_ids")
        if (
            not isinstance(src, str)
            or roles.get(src) != "EXIT"
            or ft_sides.get(src) != "stoploss"
            or link.get("source_type") != "ALGO_CONDITIONAL"
            or link.get("state") != "effective"
            or link.get("inst_id") != inst
            or not isinstance(targets, list)
            or not targets
            or src in required_triggers
        ):
            return _unknown("ALGO_LINK_UNQUALIFIED")
        children: set[str] = set()
        for oid in targets:
            if (
                not isinstance(oid, str)
                or not oid
                or oid in roles
                or oid in aliases
                or oid in children
            ):
                return _unknown("ALGO_LINK_TRIGGER_CONFLICT")
            children.add(oid)
            aliases[oid] = "EXIT"
        required_triggers[src] = children
    fills = ev.get("fills")
    if not isinstance(fills, list) or not fills:
        return _unknown("FILLS_UNKNOWN")
    fill_order_ids: set[str] = set()
    fill_trade_ids: set[str] = set()
    times: list[int] = []
    for raw in fills:
        fill = _obj(raw)
        if fill is None:
            return _unknown("FILL_INVALID")
        oid, trade_id = fill.get("ord_id"), fill.get("trade_id")
        t = _ms(fill.get("fill_time"))
        if (
            not isinstance(oid, str)
            or oid not in roles
            and oid not in aliases
            or not isinstance(trade_id, str)
            or not trade_id
            or t is None
            or fill.get("inst_id") != inst
        ):
            return _unknown("FILL_ORDER_INSTRUMENT_TIME_UNKNOWN")
        if trade_id in fill_trade_ids:
            return _unknown("DUPLICATE_EXCHANGE_FILL")
        fill_order_ids.add(oid)
        fill_trade_ids.add(trade_id)
        times.append(t)
    for oid in expected:
        if oid in fill_order_ids:
            continue
        needed = required_triggers.get(oid)
        if needed is None or not needed.issubset(fill_order_ids):
            return _unknown("EXPECTED_ORDER_FILL_MISSING")
    history = ev.get("position_history")
    if not isinstance(history, list):
        return _unknown("POSITION_HISTORY_UNKNOWN")
    candidates: list[tuple[Mapping[str, Any], int, int]] = []
    for raw in history:
        pos = _obj(raw)
        if pos is None:
            continue
        if (
            pos.get("inst_id") != inst
            or str(pos.get("type")) != "2"
            or str(pos.get("direction") or "").lower() != side.lower()
            or str(pos.get("mgn_mode") or "").lower() != "isolated"
        ):
            continue
        start, end = _ms(pos.get("c_time")), _ms(pos.get("u_time"))
        if start is not None and end is not None and start <= min(times) and end >= max(times):
            candidates.append((pos, start, end))
    if len(candidates) != 1:
        return _unknown(
            "POSITION_CYCLE_NOT_FOUND" if not candidates else "POSITION_CYCLE_AMBIGUOUS"
        )
    pos, start, end = candidates[0]
    pos_id, ccy = pos.get("pos_id"), pos.get("ccy")
    if not isinstance(pos_id, str) or not pos_id:
        return _unknown("POSITION_ID_UNKNOWN")
    if not isinstance(ccy, str) or not ccy or ccy != ccy.upper():
        return _unknown("POSITION_NATIVE_CURRENCY_UNKNOWN")
    settled = pos.get("settled_pnl")
    if settled not in (None, "") and _num(settled) != Decimal(0):
        return _unknown("UNSUPPORTED_NONZERO_SETTLED_PNL")
    values = {
        key: _num(pos.get(key))
        for key in ("pnl", "fee", "funding_fee", "liq_penalty", "realized_pnl")
    }
    if any(value is None for value in values.values()):
        return _unknown("POSITION_NATIVE_COMPONENT_UNKNOWN")
    try:
        end_time = datetime.fromtimestamp(end / 1000, UTC)
    except (OSError, ValueError, OverflowError):
        return _unknown("POSITION_CLOSE_TIME_INVALID")
    record = NormalizedTradeRecord(
        record_id="source-native",
        source_kind=SourceKind.EXCHANGE,
        source_name="okx-position-history",
        trade_ref="source-cycle",
        instrument=inst,
        settlement_currency=ccy,
        closed_at=end_time,
        price_pnl=values["pnl"],
        trading_fees=values["fee"],
        funding=values["funding_fee"],
        other_adjustments=values["liq_penalty"],
        reported_net_pnl=values["realized_pnl"],
    )
    audit = audit_native_record(record, tolerance=tolerance)
    status = (
        PositionNativeStatus.CONSISTENT
        if audit.status == "consistent"
        else PositionNativeStatus.DIFFERENT
        if audit.status == "different"
        else PositionNativeStatus.UNKNOWN
    )
    return PositionNativeResult(
        status=status,
        reason="NATIVE_EQUATION_" + audit.status.upper(),
        currency=ccy,
        difference=audit.difference,
        cycle_key=(inst, pos_id, start, end),
    )


def verify_okx_native_batch(
    entries: Sequence[tuple[Mapping[str, Any], str]], *, tolerance: Decimal
) -> tuple[PositionNativeResult, ...]:
    """Multiple claims to an identical position cycle are always conflicted."""
    rows = tuple(
        verify_okx_position_native(p, side=side, tolerance=tolerance) for p, side in entries
    )
    count = Counter(r.cycle_key for r in rows if r.cycle_key is not None)
    return tuple(
        replace(
            row,
            status=PositionNativeStatus.CONFLICT,
            reason="POSITION_CYCLE_CLAIMED_MULTIPLE_TIMES",
            difference=None,
        )
        if row.cycle_key is not None and count[row.cycle_key] > 1
        else row
        for row in rows
    )
