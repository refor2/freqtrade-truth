"""Normalized public financial domain contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class SourceKind(StrEnum):
    """High-level origin of a normalized record."""

    FREQTRADE = "freqtrade"
    EXCHANGE = "exchange"


class FinancialField(StrEnum):
    """Financial fields an adapter may provide."""

    PRICE_PNL = "price_pnl"
    TRADING_FEES = "trading_fees"
    FUNDING = "funding"
    OTHER_ADJUSTMENTS = "other_adjustments"
    REPORTED_NET_PNL = "reported_net_pnl"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _require_finite(value: Decimal | None, field_name: str) -> None:
    if value is not None and not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal when present")


@dataclass(frozen=True, slots=True)
class NormalizedTradeRecord:
    """Closed-trade financial record emitted by a read-only adapter.

    Signed amount convention:
    - positive values increase account equity,
    - negative values decrease account equity.

    Missing values are represented by None and must never be silently
    replaced with zero by adapters.
    """

    record_id: str
    source_kind: SourceKind
    source_name: str
    trade_ref: str
    instrument: str
    settlement_currency: str
    closed_at: datetime
    opened_at: datetime | None = None
    price_pnl: Decimal | None = None
    trading_fees: Decimal | None = None
    funding: Decimal | None = None
    other_adjustments: Decimal | None = None
    reported_net_pnl: Decimal | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.record_id, "record_id")
        _require_non_empty(self.source_name, "source_name")
        _require_non_empty(self.trade_ref, "trade_ref")
        _require_non_empty(self.instrument, "instrument")
        _require_non_empty(self.settlement_currency, "settlement_currency")

        if self.settlement_currency != self.settlement_currency.upper():
            raise ValueError("settlement_currency must use uppercase canonical form")

        _require_utc(self.closed_at, "closed_at")

        if self.opened_at is not None:
            _require_utc(self.opened_at, "opened_at")
            if self.opened_at > self.closed_at:
                raise ValueError("opened_at must not be after closed_at")

        for field_name in (
            "price_pnl",
            "trading_fees",
            "funding",
            "other_adjustments",
            "reported_net_pnl",
        ):
            _require_finite(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class ClosedTradeQuery:
    """Time-bounded query for closed normalized trade records."""

    closed_from: datetime
    closed_until: datetime
    instrument: str | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.closed_from, "closed_from")
        _require_utc(self.closed_until, "closed_until")

        if self.closed_from > self.closed_until:
            raise ValueError("closed_from must not be after closed_until")

        if self.instrument is not None:
            _require_non_empty(self.instrument, "instrument")

        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be positive when present")


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """Financial fields a read-only adapter can provide."""

    fields: frozenset[FinancialField]

    def supports(self, field: FinancialField) -> bool:
        return field in self.fields
