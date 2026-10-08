"""Read-only source-side exchange-order references for issue #15 identity review.

This module collects *candidate evidence*, not final ownership or attribution.
Its types are deliberately not re-exported by the public adapter package.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from freqtrade_truth.adapters.http import JsonHttpTransport, JsonValue


class FreqtradeOrderEvidenceError(RuntimeError):
    """A closed-trade detail cannot support a trustworthy order-reference snapshot."""


@dataclass(frozen=True, slots=True)
class FreqtradeClosedTradeOrderEvidence:
    """Observed exchange order IDs from one Freqtrade closed-trade detail response.

    The IDs are source-side references only. They do not prove exchange account
    scope, actual fills, completeness, position ownership, or financial amounts.
    """

    source_name: str
    trade_ref: str
    instrument: str
    observed_exchange_order_ids: tuple[str, ...]
    positive_fill_exchange_order_ids: tuple[str, ...] = ()
    zero_fill_exchange_order_ids: tuple[str, ...] = ()
    unknown_fill_exchange_order_ids: tuple[str, ...] = ()


class FreqtradeClosedTradeOrderReader:
    """Fetch GET /trade/{trade_id} for identity candidates only.

    The read path is intentionally separate from normalized financial records.
    It neither changes reconciliation status nor publishes a v0.1 identity contract.
    """

    def __init__(self, transport: JsonHttpTransport, *, source_name: str = "freqtrade") -> None:
        if not source_name.strip():
            raise ValueError("source_name must not be empty")
        self._transport = transport
        self._source_name = source_name

    async def fetch(
        self, trade_id: int, *, expected_instrument: str
    ) -> FreqtradeClosedTradeOrderEvidence:
        if isinstance(trade_id, bool) or not isinstance(trade_id, int) or trade_id <= 0:
            raise ValueError("trade_id must be a positive integer")
        if not expected_instrument.strip():
            raise ValueError("expected_instrument must not be empty")

        response = await self._transport.get_json(f"trade/{trade_id}")

        source_trade_id = response.get("trade_id")
        if (
            isinstance(source_trade_id, bool)
            or not isinstance(source_trade_id, int)
            or source_trade_id != trade_id
        ):
            raise FreqtradeOrderEvidenceError("trade detail ID does not match requested ID")

        instrument = response.get("pair")
        if not isinstance(instrument, str) or instrument != expected_instrument:
            raise FreqtradeOrderEvidenceError("trade detail instrument does not match request")

        if response.get("is_open") is not False:
            raise FreqtradeOrderEvidenceError("trade detail must be an explicitly closed trade")

        raw_orders = response.get("orders")
        if not isinstance(raw_orders, list):
            raise FreqtradeOrderEvidenceError("trade detail orders must be an array")

        order_ids: set[str] = set()
        positive_fill_ids: set[str] = set()
        zero_fill_ids: set[str] = set()
        unknown_fill_ids: set[str] = set()
        for raw in raw_orders:
            if not isinstance(raw, dict):
                raise FreqtradeOrderEvidenceError("trade detail order must be an object")
            order: dict[str, JsonValue] = raw

            order_id = order.get("order_id")
            if not isinstance(order_id, str) or not order_id.strip():
                raise FreqtradeOrderEvidenceError("trade detail order_id must be a nonempty string")

            order_pair = order.get("pair")
            if order_pair is not None and order_pair != expected_instrument:
                raise FreqtradeOrderEvidenceError("trade detail order instrument mismatch")

            if order_id in order_ids:
                raise FreqtradeOrderEvidenceError("duplicate exchange order_id in trade detail")
            order_ids.add(order_id)

            # Order history includes canceled and partly filled orders. A source-side
            # order ID alone is not evidence that an economic execution occurred.
            raw_filled = order.get("filled")
            if raw_filled is None:
                unknown_fill_ids.add(order_id)
            elif isinstance(raw_filled, bool) or not isinstance(raw_filled, (int, Decimal)):
                raise FreqtradeOrderEvidenceError("order filled must be numeric when present")
            else:
                filled = Decimal(raw_filled)
                if not filled.is_finite() or filled < 0:
                    raise FreqtradeOrderEvidenceError(
                        "order filled must be finite and non-negative"
                    )
                if filled == 0:
                    zero_fill_ids.add(order_id)
                else:
                    positive_fill_ids.add(order_id)

        return FreqtradeClosedTradeOrderEvidence(
            source_name=self._source_name,
            trade_ref=str(trade_id),
            instrument=instrument,
            observed_exchange_order_ids=tuple(sorted(order_ids)),
            positive_fill_exchange_order_ids=tuple(sorted(positive_fill_ids)),
            zero_fill_exchange_order_ids=tuple(sorted(zero_fill_ids)),
            unknown_fill_exchange_order_ids=tuple(sorted(unknown_fill_ids)),
        )
