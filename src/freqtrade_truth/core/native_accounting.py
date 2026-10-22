"""Strict source-native accounting identity, independent of exchange ownership.

All components use the NormalizedTradeRecord signed equity convention.
Currency stays native (USDC, BTC, USD etc); no implicit FX conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from freqtrade_truth.core.models import NormalizedTradeRecord


@dataclass(frozen=True, slots=True)
class NativeAccountingResult:
    """Native source arithmetic only, never a cross-source trade or USD proof."""

    status: str  # consistent | different | incomplete
    difference: Decimal | None
    currency: str
    ownership_verified: bool = False
    usd_valuation_verified: bool = False


def audit_native_record(
    record: NormalizedTradeRecord,
    *,
    tolerance: Decimal,
) -> NativeAccountingResult:
    """Check: price + signed fees + signed funding + signed other == net.

    Returns incomplete whenever any component or reported net is missing;
    no missing field may be coerced to zero.
    """
    if not isinstance(tolerance, Decimal) or not tolerance.is_finite() or tolerance < 0:
        raise ValueError("explicit finite nonnegative Decimal tolerance is required")

    components = (
        record.price_pnl,
        record.trading_fees,
        record.funding,
        record.other_adjustments,
        record.reported_net_pnl,
    )
    if any(value is None for value in components):
        return NativeAccountingResult(
            status="incomplete",
            difference=None,
            currency=record.settlement_currency,
        )
    price, fees, funding, other, reported = components
    assert price is not None and fees is not None and funding is not None
    assert other is not None and reported is not None
    difference = price + fees + funding + other - reported
    return NativeAccountingResult(
        status="consistent" if abs(difference) <= tolerance else "different",
        difference=difference,
        currency=record.settlement_currency,
    )
