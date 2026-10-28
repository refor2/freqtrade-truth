"""Strict local source-native settlement candidate reducer.

Input is operator-supplied evidence, not an authenticated exchange oracle.
No user-supplied identifiers are emitted. An arithmetic match is NEVER
independent account identity, ledger completeness, or USD valuation proof.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Any

_SCHEMA = "freqtrade-truth.settlement-ledger.v1"
_COMPONENTS = (
    ("price_pnl", "price_complete"),
    ("trading_fees", "fees_complete"),
    ("funding", "funding_complete"),
    ("other_adjustments", "other_complete"),
)
_EVENT_TO_COMPONENT = {
    "price_pnl": "price_pnl",
    "trading_fee": "trading_fees",
    "funding": "funding",
    "other_adjustment": "other_adjustments",
}
_KINDS = frozenset(_EVENT_TO_COMPONENT)
_MONEY_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]{0,80})(?:\.[0-9]{1,32})?\Z")
_MAX_EVENTS = 1000


def _object(raw: object, names: set[str], kind: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != names:
        raise ValueError(f"invalid {kind}")
    return raw


def _nonempty(raw: object, kind: str) -> str:
    if not isinstance(raw, str) or not 1 <= len(raw.strip()) <= 256:
        raise ValueError(f"invalid {kind}")
    return raw


def _time(raw: object) -> datetime:
    if not isinstance(raw, str) or not raw.endswith("Z") or len(raw) > 40:
        raise ValueError("timestamp must be UTC")
    try:
        result = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("timestamp must be UTC") from error
    offset = result.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("timestamp must be UTC")
    return result.astimezone(UTC)


def _amount(raw: object, *, missing_allowed: bool = False) -> Decimal | None:
    if raw is None and missing_allowed:
        return None
    if not isinstance(raw, str) or not _MONEY_PATTERN.fullmatch(raw):
        raise ValueError("invalid monetary value")
    value = Decimal(raw)
    if not value.is_finite():
        raise ValueError("invalid monetary value")
    return value


def _sum_exact(values: list[Decimal]) -> Decimal:
    # Largest accepted event has 81 integral and 32 fractional digits;
    # 1,000 events require four additional digits, not default precision 28.
    with localcontext() as ctx:
        ctx.prec = 160
        return sum(values, Decimal("0"))


def _difference_exact(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 160
        return left - right


def audit_settlement_ledger(snapshot: object, *, tolerance: Decimal) -> dict[str, object]:
    """Compare exact-position event components with reported exchange and bot net.

    Complete category flags are *claims* in the input and not independently
    verified by this library. Missing category evidence never becomes zero
    without the explicit claim. Every event must match the requested position,
    instrument, settlement currency and trade time window. Multiple partial
    fills with unique IDs are aggregated in signed native units only.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("invalid tolerance")
    envelope = _object(snapshot, {"schema", "trade", "coverage", "events"}, "settlement envelope")
    if envelope["schema"] != _SCHEMA:
        raise ValueError("invalid settlement envelope")
    trade = _object(
        envelope["trade"],
        {
            "trade_ref",
            "position_ref",
            "instrument",
            "settlement_currency",
            "opened_at",
            "closed_at",
            "bot_net_pnl",
            "exchange_reported_net_pnl",
        },
        "trade evidence",
    )
    coverage = _object(
        envelope["coverage"],
        {
            "start_at",
            "end_at",
            "price_complete",
            "fees_complete",
            "funding_complete",
            "other_complete",
        },
        "coverage evidence",
    )
    for field in ("trade_ref", "position_ref", "instrument", "settlement_currency"):
        _nonempty(trade[field], field)
    currency = trade["settlement_currency"]
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z0-9]{2,12}", currency):
        raise ValueError("invalid settlement currency")
    opened = _time(trade["opened_at"])
    closed = _time(trade["closed_at"])
    start = _time(coverage["start_at"])
    end = _time(coverage["end_at"])
    if opened > closed or start > end:
        raise ValueError("invalid event chronology")
    for _component, flag in _COMPONENTS:
        if type(coverage[flag]) is not bool:
            raise ValueError("invalid coverage completeness flag")
    bot_net = _amount(trade["bot_net_pnl"], missing_allowed=True)
    exchange_net = _amount(trade["exchange_reported_net_pnl"], missing_allowed=True)
    events = envelope["events"]
    if not isinstance(events, list) or not 0 <= len(events) <= _MAX_EVENTS:
        raise ValueError("invalid settlement event count")

    problems: set[str] = set()
    if start > opened or end < closed:
        problems.add("ledger_coverage_incomplete")

    seen: set[str] = set()
    values: dict[str, list[Decimal]] = {kind: [] for kind, _flag in _COMPONENTS}
    for raw in events:
        event = _object(
            raw,
            {"id", "position_ref", "instrument", "currency", "occurred_at", "kind", "amount"},
            "settlement event",
        )
        record_id = _nonempty(event["id"], "event id")
        position = _nonempty(event["position_ref"], "position_ref")
        inst = _nonempty(event["instrument"], "instrument")
        ccy = _nonempty(event["currency"], "currency")
        when = _time(event["occurred_at"])
        money = _amount(event["amount"])
        assert money is not None
        kind = event["kind"]
        if record_id in seen:
            problems.add("duplicate_event_id")
        seen.add(record_id)
        if position != trade["position_ref"]:
            problems.add("foreign_position_event")
        if inst != trade["instrument"]:
            problems.add("instrument_mismatch")
        if ccy != currency:
            problems.add("currency_mismatch")
        if not opened <= when <= closed or not start <= when <= end:
            problems.add("event_outside_trade_window")
        if not isinstance(kind, str) or kind not in _KINDS:
            problems.add("unsupported_event_kind")
        else:
            values[_EVENT_TO_COMPONENT[kind]].append(money)

    missing: list[str] = []
    components: dict[str, str | None] = {}
    for kind, flag in _COMPONENTS:
        if not coverage[flag] or "ledger_coverage_incomplete" in problems:
            missing.append(f"{kind}_source_incomplete")
            components[kind] = None
        elif kind == "price_pnl" and not values[kind]:
            missing.append("price_source_missing")
            components[kind] = None
        else:
            components[kind] = str(_sum_exact(values[kind]))
    if bot_net is None:
        missing.append("bot_net_missing")
    if exchange_net is None:
        missing.append("exchange_net_missing")
    exchange_minus_bot = (
        _difference_exact(exchange_net, bot_net)
        if bot_net is not None and exchange_net is not None
        else None
    )
    computed = (
        _sum_exact([Decimal(value) for value in components.values() if value is not None])
        if all(value is not None for value in components.values()) and not problems
        else None
    )
    computed_minus_exchange = (
        _difference_exact(computed, exchange_net)
        if computed is not None and exchange_net is not None
        else None
    )
    mismatch = (
        (exchange_minus_bot is not None and abs(exchange_minus_bot) > tolerance)
        or (computed_minus_exchange is not None and abs(computed_minus_exchange) > tolerance)
        or (
            computed is not None
            and bot_net is not None
            and abs(_difference_exact(computed, bot_net)) > tolerance
        )
    )
    if problems - {"ledger_coverage_incomplete"}:
        status = "ambiguous"
    elif mismatch:
        status = "review"
    elif problems or missing:
        status = "incomplete"
    else:
        status = "match"
    reasons = sorted(problems | set(missing) | ({"numeric_difference"} if mismatch else set()))
    # Even an exact event equation cannot prove the source account scope,
    # event completeness or genuine cross-source economic trade ownership.
    return {
        "schema": "freqtrade-truth.settlement-audit.v1",
        "scope": "local_operator_supplied_candidate_only",
        "numeric_status": status,
        "reasons": reasons,
        "event_count": len(events),
        "event_kind_counts": dict(
            sorted(
                Counter(
                    raw["kind"]
                    for raw in events
                    if isinstance(raw, dict)
                    and isinstance(raw.get("kind"), str)
                    and raw.get("kind") in _KINDS
                ).items()
            )
        ),
        "settlement_currency": currency,
        "native_components": (
            {field: None for field in components}
            if problems - {"ledger_coverage_incomplete"}
            else components
        ),
        "computed_native_net": str(computed) if computed is not None else None,
        "exchange_minus_bot": str(exchange_minus_bot) if exchange_minus_bot is not None else None,
        "computed_minus_exchange": (
            str(computed_minus_exchange) if computed_minus_exchange is not None else None
        ),
        "account_verified": False,
        "verified_trade_identity": False,
        "ledger_coverage_independently_verified": False,
        "verified_financial_truth": False,
        "usd_verified": False,
        "warning": (
            "Arithmetic may agree; independent account provenance and "
            "complete exchange history remain unverified."
        ),
    }
