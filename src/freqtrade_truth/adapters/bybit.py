"""Read-only Bybit V5 adapter for inverse-contract closed PnL."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, JsonValue
from freqtrade_truth.core.models import (
    AdapterCapabilities,
    ClosedTradeQuery,
    FinancialField,
    NormalizedTradeRecord,
    SourceKind,
)

_MAX_PAGE_SIZE = 100
_MAX_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


class BybitResponseError(RuntimeError):
    """Raised when Bybit returns an invalid or ambiguous closed-PnL payload."""


class BybitInverseClosedPnlAdapter:
    """Normalize Bybit inverse closed-PnL records for one configured symbol.

    Bybit's closedPnl is treated as the exchange-reported net result. The
    endpoint exposes openFee and closeFee separately, so those are normalized
    into signed trading fees. Funding is intentionally left missing because
    this endpoint does not provide a separate funding component.
    """

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        symbol: str,
        settlement_currency: str,
        page_size: int = _MAX_PAGE_SIZE,
        source_name: str = "bybit",
    ) -> None:
        if not symbol or symbol != symbol.upper():
            raise ValueError("symbol must be a non-empty uppercase Bybit symbol")
        if not settlement_currency or settlement_currency != settlement_currency.upper():
            raise ValueError("settlement_currency must be non-empty uppercase")
        if page_size <= 0 or page_size > _MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
        if not source_name.strip():
            raise ValueError("source_name must not be empty")

        self._transport = transport
        self._symbol = symbol
        self._settlement_currency = settlement_currency
        self._page_size = page_size
        self._source_name = source_name

    @property
    def name(self) -> str:
        return self._source_name

    @property
    def source_kind(self) -> SourceKind:
        return SourceKind.EXCHANGE

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            fields=frozenset(
                {
                    FinancialField.TRADING_FEES,
                    FinancialField.REPORTED_NET_PNL,
                }
            )
        )

    async def fetch_closed_trades(
        self,
        query: ClosedTradeQuery,
    ) -> Sequence[NormalizedTradeRecord]:
        if query.instrument is not None and query.instrument != self._symbol:
            return ()

        start_ms = _to_milliseconds(query.closed_from)
        end_ms = _to_milliseconds(query.closed_until)
        records: list[NormalizedTradeRecord] = []
        seen_record_ids: set[str] = set()

        window_start = start_ms
        while window_start <= end_ms:
            window_end = min(window_start + _MAX_WINDOW_MS - 1, end_ms)
            cursor: str | None = None
            seen_cursors: set[str] = set()

            while True:
                params: dict[str, str | int | bool] = {
                    "category": "inverse",
                    "symbol": self._symbol,
                    "startTime": window_start,
                    "endTime": window_end,
                    "limit": self._page_size,
                }
                if cursor is not None:
                    params["cursor"] = cursor

                payload = await self._transport.get_json(
                    "v5/position/closed-pnl",
                    params,
                )
                result = _require_success_result(payload)
                category = _require_non_empty_str(result, "category")
                if category != "inverse":
                    raise BybitResponseError("Bybit response category must be 'inverse'")

                for raw_record in _require_object_list(result, "list"):
                    record = self._normalize_record(raw_record)
                    if record.closed_at < query.closed_from or record.closed_at > query.closed_until:
                        continue
                    if record.record_id in seen_record_ids:
                        raise BybitResponseError("Bybit returned a duplicate closed-PnL record")
                    seen_record_ids.add(record.record_id)
                    records.append(record)

                next_cursor = _require_cursor(result)
                if not next_cursor:
                    break
                if next_cursor in seen_cursors:
                    raise BybitResponseError("Bybit pagination cursor repeated")
                seen_cursors.add(next_cursor)
                cursor = next_cursor

            window_start = window_end + 1

        records.sort(key=lambda record: (record.closed_at, record.record_id))
        if query.limit is not None:
            records = records[: query.limit]
        return tuple(records)

    def _normalize_record(
        self,
        raw_record: Mapping[str, JsonValue],
    ) -> NormalizedTradeRecord:
        symbol = _require_non_empty_str(raw_record, "symbol")
        if symbol != self._symbol:
            raise BybitResponseError("Bybit response symbol does not match configured symbol")

        order_id = _require_non_empty_str(raw_record, "orderId")
        updated_time = _require_integer_string(raw_record, "updatedTime")
        reported_net_pnl = _require_decimal(raw_record, "closedPnl")

        open_fee = _optional_decimal(raw_record, "openFee")
        close_fee = _optional_decimal(raw_record, "closeFee")
        if (open_fee is None) != (close_fee is None):
            raise BybitResponseError(
                "Bybit openFee and closeFee must either both be present or both be missing"
            )

        trading_fees = None
        if open_fee is not None and close_fee is not None:
            trading_fees = -(open_fee + close_fee)

        return NormalizedTradeRecord(
            record_id=f"bybit:{order_id}",
            source_kind=SourceKind.EXCHANGE,
            source_name=self.name,
            trade_ref=order_id,
            instrument=symbol,
            settlement_currency=self._settlement_currency,
            opened_at=None,
            closed_at=_from_milliseconds(updated_time),
            trading_fees=trading_fees,
            funding=None,
            reported_net_pnl=reported_net_pnl,
        )


def _require_success_result(payload: JsonObject) -> Mapping[str, JsonValue]:
    ret_code = payload.get("retCode")
    if isinstance(ret_code, bool) or not isinstance(ret_code, int):
        raise BybitResponseError("Bybit response field 'retCode' must be an int")
    if ret_code != 0:
        raise BybitResponseError(f"Bybit API returned retCode {ret_code}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise BybitResponseError("Bybit response field 'result' must be an object")
    return result


def _require_object_list(
    payload: Mapping[str, JsonValue],
    key: str,
) -> list[Mapping[str, JsonValue]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise BybitResponseError(f"Bybit response field '{key}' must be a list")

    items: list[Mapping[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BybitResponseError(f"Bybit response field '{key}' must contain objects")
        items.append(item)
    return items


def _require_non_empty_str(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BybitResponseError(f"Bybit response field '{key}' must be a non-empty string")
    return value


def _require_integer_string(payload: Mapping[str, JsonValue], key: str) -> int:
    value = _require_non_empty_str(payload, key)
    if not value.isdigit():
        raise BybitResponseError(f"Bybit response field '{key}' must be an integer string")
    return int(value)


def _require_decimal(payload: Mapping[str, JsonValue], key: str) -> Decimal:
    value = _require_non_empty_str(payload, key)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BybitResponseError(f"Bybit response field '{key}' must be decimal") from exc
    if not result.is_finite():
        raise BybitResponseError(f"Bybit response field '{key}' must be finite")
    return result


def _optional_decimal(payload: Mapping[str, JsonValue], key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise BybitResponseError(f"Bybit response field '{key}' must be decimal when present")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BybitResponseError(
            f"Bybit response field '{key}' must be decimal when present"
        ) from exc
    if not result.is_finite():
        raise BybitResponseError(f"Bybit response field '{key}' must be finite")
    return result


def _require_cursor(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("nextPageCursor")
    if not isinstance(value, str):
        raise BybitResponseError("Bybit response field 'nextPageCursor' must be a string")
    return value


def _to_milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _from_milliseconds(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise BybitResponseError("Bybit timestamp is outside supported range") from exc

