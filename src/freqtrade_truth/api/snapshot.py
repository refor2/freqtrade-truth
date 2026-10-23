"""Strict parsing for LOCAL-only normalized candidate snapshots; no network requests."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from freqtrade_truth.core.models import NormalizedTradeRecord, SourceKind
from freqtrade_truth.core.reconciliation import ReconciliationTolerance

MAX_BYTES = 2_000_000
MAX_ROWS = 1000
FINANCIAL = ("price_pnl", "trading_fees", "funding", "other_adjustments", "reported_net_pnl")
REQUIRED = (
    "record_id",
    "source_name",
    "trade_ref",
    "instrument",
    "settlement_currency",
    "closed_at",
)


def _money(value: object, name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be a decimal string or null")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal string") from exc
    if not amount.is_finite() or len(value) > 120 or abs(amount.adjusted()) > 300:
        raise ValueError(f"{name} must be finite and bounded")
    return amount


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a UTC string")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO8601 timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return dt


def _parse_row(value: object, kind: SourceKind) -> NormalizedTradeRecord:
    if not isinstance(value, dict):
        raise ValueError("record must be an object")
    if set(value) - (set(REQUIRED) | set(FINANCIAL) | {"opened_at"}):
        raise ValueError("unsupported record field")
    if any(not isinstance(value.get(field), str) or not value[field] for field in REQUIRED):
        raise ValueError("record is missing required string fields")
    opened = value.get("opened_at")
    return NormalizedTradeRecord(
        record_id=value["record_id"],
        source_kind=kind,
        source_name=value["source_name"],
        trade_ref=value["trade_ref"],
        instrument=value["instrument"],
        settlement_currency=value["settlement_currency"],
        opened_at=_utc(opened) if opened is not None else None,
        closed_at=_utc(value["closed_at"]),
        **{name: _money(value.get(name), name) for name in FINANCIAL},
    )


def parse_snapshot(
    payload: object,
) -> tuple[
    tuple[NormalizedTradeRecord, ...],
    tuple[NormalizedTradeRecord, ...],
    ReconciliationTolerance,
]:
    """Accept an explicit local snapshot; never infer source identity or zero values."""
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "freqtrade",
        "exchange",
        "tolerance",
    }:
        raise ValueError("snapshot envelope must have exact declared fields")
    if payload["schema"] != "freqtrade-truth.candidate.v1":
        raise ValueError("unsupported snapshot schema")
    left, right = payload["freqtrade"], payload["exchange"]
    if not isinstance(left, list) or not isinstance(right, list):
        raise ValueError("snapshot record collections must be arrays")
    if not 1 <= len(left) + len(right) <= MAX_ROWS:
        raise ValueError("snapshot size out of bounds")
    params = payload["tolerance"]
    if not isinstance(params, dict) or set(params) - {"close_seconds", *FINANCIAL}:
        raise ValueError("invalid tolerance fields")
    seconds = params.get("close_seconds", 0)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 <= seconds <= 86400:
        raise ValueError("close_seconds must be an integer in 0..86400")
    money = {name: _money(params.get(name, "0"), name) for name in FINANCIAL}
    if any(amount is None or amount < 0 for amount in money.values()):
        raise ValueError("tolerances must be non-negative decimal strings")
    tol = ReconciliationTolerance(
        close_time=timedelta(seconds=seconds),
        **money,  # type: ignore[arg-type]
    )
    return (
        tuple(_parse_row(row, SourceKind.FREQTRADE) for row in left),
        tuple(_parse_row(row, SourceKind.EXCHANGE) for row in right),
        tol,
    )
