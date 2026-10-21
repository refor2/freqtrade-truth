"""Read-only OKX V5 historical *full-close* numeric records.

Not an economic-trade identity oracle: position history does not by itself
prove Freqtrade account/position ownership, or retained completeness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from freqtrade_truth.adapters.http import JsonHttpTransport, JsonValue
from freqtrade_truth.core.models import (
    AdapterCapabilities,
    ClosedTradeQuery,
    FinancialField,
    NormalizedTradeRecord,
    SourceKind,
)

_MAX_PAGE_SIZE = 100
_PATH = "api/v5/account/positions-history"


class OkxPositionHistoryError(RuntimeError):
    """Missing, inconsistent or malformed OKX position-history source evidence."""


def _nonempty_str(data: Mapping[str, JsonValue], field: str) -> str:
    raw = data.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise OkxPositionHistoryError(f"{field} must be a nonempty string")
    return raw


def _native_decimal(data: Mapping[str, JsonValue], field: str) -> Decimal | None:
    raw = data.get(field)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, (str, Decimal, int)) or isinstance(raw, bool):
        raise OkxPositionHistoryError(f"{field} is not a native decimal")
    try:
        result = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise OkxPositionHistoryError(f"{field} is not a native decimal") from exc
    if not result.is_finite():
        raise OkxPositionHistoryError(f"{field} must be finite")
    return result


def _millis(data: Mapping[str, JsonValue], field: str) -> int:
    raw = _nonempty_str(data, field)
    if not raw.isascii() or not raw.isdigit():
        raise OkxPositionHistoryError(f"{field} must be epoch milliseconds")
    result = int(raw)
    if result <= 0:
        raise OkxPositionHistoryError(f"{field} must be a positive timestamp")
    return result


class OkxPositionHistoryAdapter:
    """Bounded-query OKX full-close history via injected GET-only transport.

    Caller must establish the native OKX instId -> canonical Freqtrade symbol
    and settlement currency from independently qualified instrument metadata.
    The ccy field in position-history is checked but alone is not proof of
    settlement for every possible account mode. No authenticated credentials
    are accepted by this class; use a separate constrained signed transport.
    """

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        inst_id: str,
        inst_type: str,
        settlement_currency: str,
        normalized_instrument: str,
        page_size: int = _MAX_PAGE_SIZE,
        source_name: str = "okx",
    ) -> None:
        if not inst_id.strip() or inst_id != inst_id.upper():
            raise ValueError("inst_id must be an explicit uppercase OKX instrument")
        if inst_type not in ("FUTURES", "SWAP"):
            raise ValueError("inst_type must be FUTURES or SWAP")
        if not settlement_currency.strip() or settlement_currency != settlement_currency.upper():
            raise ValueError("settlement_currency must be uppercase and explicit")
        if not normalized_instrument.strip():
            raise ValueError("normalized_instrument must be explicit")
        if not 1 <= page_size <= _MAX_PAGE_SIZE:
            raise ValueError("page_size must be 1..100")
        if not source_name.strip():
            raise ValueError("source_name must be non-empty")
        self._transport = transport
        self._inst_id = inst_id
        self._inst_type = inst_type
        self._settlement_currency = settlement_currency
        self._normalized_instrument = normalized_instrument
        self._page_size = page_size
        self._source_name = source_name

    @property
    def name(self) -> str:
        return self._source_name

    @property
    def source_kind(self) -> SourceKind:
        return SourceKind.EXCHANGE

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(fields=frozenset(FinancialField))

    async def fetch_closed_trades(
        self,
        query: ClosedTradeQuery,
    ) -> Sequence[NormalizedTradeRecord]:
        if query.instrument is not None and query.instrument != self._normalized_instrument:
            return ()

        lower = int(query.closed_from.timestamp() * 1000)
        upper = int(query.closed_until.timestamp() * 1000)
        after: int | None = None
        seen_ids: set[tuple[str, int, str]] = set()
        collected: list[NormalizedTradeRecord] = []

        while True:
            params: dict[str, str | int | bool] = {
                "instType": self._inst_type,
                "instId": self._inst_id,
                "limit": self._page_size,
            }
            if after is not None:
                params["after"] = str(after)
            raw_response = await self._transport.get_json(_PATH, params)
            if raw_response.get("code") != "0":
                raise OkxPositionHistoryError("OKX response code was not 0")
            rows = raw_response.get("data")
            if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
                raise OkxPositionHistoryError("OKX data must be an array of objects")

            previous_time: int | None = None
            times: list[int] = []
            for raw in rows:
                if not isinstance(raw, dict):
                    raise OkxPositionHistoryError("OKX row must be an object")
                pos_id = _nonempty_str(raw, "posId")
                timestamp = _millis(raw, "uTime")
                position_type = _nonempty_str(raw, "type")
                if _nonempty_str(raw, "instId") != self._inst_id:
                    raise OkxPositionHistoryError("instId mismatch")
                if _nonempty_str(raw, "instType") != self._inst_type:
                    raise OkxPositionHistoryError("instType mismatch")
                if _nonempty_str(raw, "ccy") != self._settlement_currency:
                    raise OkxPositionHistoryError("ccy does not match explicit settlement currency")
                if previous_time is not None and timestamp > previous_time:
                    raise OkxPositionHistoryError("source pagination order inconsistent")
                if after is not None and timestamp >= after:
                    raise OkxPositionHistoryError("source pagination failed to advance")
                previous_time = timestamp
                times.append(timestamp)
                key = (pos_id, timestamp, position_type)
                if key in seen_ids:
                    raise OkxPositionHistoryError("duplicate OKX position-history event")
                seen_ids.add(key)

                # OKX type=2 indicates latest fully closed position. A partial
                # close cannot be silently marked as a full economic lifecycle.
                if position_type != "2" or timestamp < lower or timestamp > upper:
                    continue
                row = self._normalize(raw, pos_id=pos_id, time_ms=timestamp)
                collected.append(row)

            if not rows or len(rows) < self._page_size:
                break
            if not times:
                raise OkxPositionHistoryError("source pagination contained no timestamps")
            oldest = min(times)
            if after is not None and oldest >= after:
                raise OkxPositionHistoryError("source pagination did not progress")
            if oldest < lower:
                break
            after = oldest

        collected.sort(key=lambda r: (r.closed_at, r.record_id))
        if query.limit is not None:
            return tuple(collected[: query.limit])
        return tuple(collected)

    def _normalize(
        self,
        data: Mapping[str, JsonValue],
        *,
        pos_id: str,
        time_ms: int,
    ) -> NormalizedTradeRecord:
        try:
            close_time = datetime.fromtimestamp(time_ms / 1000, tz=UTC)
            open_time = datetime.fromtimestamp(_millis(data, "cTime") / 1000, tz=UTC)
        except (ValueError, OverflowError, OSError) as exc:
            raise OkxPositionHistoryError("position timestamp out of range") from exc

        pnl = _native_decimal(data, "pnl")
        fee = _native_decimal(data, "fee")
        funding = _native_decimal(data, "fundingFee")
        penalty = _native_decimal(data, "liqPenalty")
        settled = _native_decimal(data, "settledPnl")
        reported = _native_decimal(data, "realizedPnl")
        other = penalty + settled if penalty is not None and settled is not None else None

        if all(v is not None for v in (pnl, fee, funding, penalty, settled, reported)):
            assert pnl is not None and fee is not None and funding is not None
            assert penalty is not None and settled is not None and reported is not None
            if pnl + fee + funding + penalty + settled != reported:
                raise OkxPositionHistoryError("OKX realizedPnl equation mismatched source fields")

        return NormalizedTradeRecord(
            record_id=f"{self._source_name}:position:{pos_id}:{time_ms}",
            source_kind=SourceKind.EXCHANGE,
            source_name=self._source_name,
            trade_ref=pos_id,
            instrument=self._normalized_instrument,
            settlement_currency=self._settlement_currency,
            opened_at=open_time,
            closed_at=close_time,
            price_pnl=pnl,
            trading_fees=fee,
            funding=funding,
            other_adjustments=other,
            reported_net_pnl=reported,
        )
