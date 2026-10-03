"""Read-only Bybit V5 transaction-log reader for inverse contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, JsonValue

_MAX_PAGE_SIZE = 50
_MAX_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


class BybitTransactionLogError(RuntimeError):
    """Raised when Bybit returns an invalid or inconsistent transaction log."""


@dataclass(frozen=True, slots=True)
class BybitTransactionLogQuery:
    """Time-bounded query for Bybit transaction-log events."""

    start_at: datetime
    end_at: datetime
    limit: int | None = None

    def __post_init__(self) -> None:
        _require_utc(self.start_at, "start_at")
        _require_utc(self.end_at, "end_at")
        if self.start_at > self.end_at:
            raise ValueError("start_at must not be after end_at")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be positive when present")


@dataclass(frozen=True, slots=True)
class BybitTransactionLogEntry:
    """Normalized Bybit ledger event with project sign conventions."""

    record_id: str
    source_name: str
    event_ref: str
    event_type: str
    instrument: str | None
    settlement_currency: str
    occurred_at: datetime
    funding: Decimal | None
    trading_fees: Decimal | None
    cash_flow: Decimal
    reported_net_change: Decimal
    order_ref: str | None
    trade_ref: str | None


class BybitInverseTransactionLogReader:
    """Read and normalize Bybit V5 inverse transaction-log events."""

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        symbol: str,
        settlement_currency: str,
        normalized_instrument: str | None = None,
        page_size: int = _MAX_PAGE_SIZE,
        source_name: str = "bybit",
    ) -> None:
        if not symbol or symbol != symbol.upper():
            raise ValueError("symbol must be a non-empty uppercase Bybit symbol")
        if not settlement_currency or settlement_currency != settlement_currency.upper():
            raise ValueError("settlement_currency must be non-empty uppercase")
        if normalized_instrument is not None and not normalized_instrument.strip():
            raise ValueError("normalized_instrument must not be empty when present")
        if page_size <= 0 or page_size > _MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
        if not source_name.strip():
            raise ValueError("source_name must not be empty")

        self._transport = transport
        self._symbol = symbol
        self._settlement_currency = settlement_currency
        self._normalized_instrument = normalized_instrument or symbol
        self._page_size = page_size
        self._source_name = source_name

    async def fetch(
        self,
        query: BybitTransactionLogQuery,
    ) -> Sequence[BybitTransactionLogEntry]:
        start_ms = _to_milliseconds(query.start_at)
        end_ms = _to_milliseconds(query.end_at)
        records: list[BybitTransactionLogEntry] = []
        seen_record_ids: set[str] = set()

        window_start = start_ms
        while window_start <= end_ms:
            window_end = min(window_start + _MAX_WINDOW_MS - 1, end_ms)
            cursor: str | None = None
            seen_cursors: set[str] = set()

            while True:
                params: dict[str, str | int | bool] = {
                    "accountType": "UNIFIED",
                    "category": "inverse",
                    "currency": self._settlement_currency,
                    "startTime": window_start,
                    "endTime": window_end,
                    "limit": self._page_size,
                }
                if cursor is not None:
                    params["cursor"] = cursor

                payload = await self._transport.get_json(
                    "v5/account/transaction-log",
                    params,
                )
                result = _require_success_result(payload)

                for raw_record in _require_object_list(result, "list"):
                    record = self._normalize_record(raw_record)
                    if record.settlement_currency != self._settlement_currency:
                        raise BybitTransactionLogError(
                            "Bybit transaction currency does not match configured currency"
                        )
                    if record.instrument is not None and record.instrument != self._symbol:
                        continue
                    if (
                        record.instrument is not None
                        and self._normalized_instrument != self._symbol
                    ):
                        record = replace(record, instrument=self._normalized_instrument)
                    if record.occurred_at < query.start_at or record.occurred_at > query.end_at:
                        continue
                    if record.record_id in seen_record_ids:
                        raise BybitTransactionLogError(
                            "Bybit returned a duplicate transaction-log record"
                        )
                    seen_record_ids.add(record.record_id)
                    records.append(record)

                next_cursor = _require_cursor(result)
                if not next_cursor:
                    break
                if next_cursor in seen_cursors:
                    raise BybitTransactionLogError("Bybit transaction-log cursor repeated")
                seen_cursors.add(next_cursor)
                cursor = next_cursor

            window_start = window_end + 1

        records.sort(key=lambda record: (record.occurred_at, record.record_id))
        if query.limit is not None:
            records = records[: query.limit]
        return tuple(records)

    def _normalize_record(
        self,
        raw_record: Mapping[str, JsonValue],
    ) -> BybitTransactionLogEntry:
        category = _require_non_empty_str(raw_record, "category")
        if category != "inverse":
            raise BybitTransactionLogError("Bybit transaction category must be 'inverse'")

        event_id = _require_non_empty_str(raw_record, "id")
        event_type = _require_non_empty_str(raw_record, "type")
        currency = _require_non_empty_str(raw_record, "currency")
        transaction_time = _require_integer_string(raw_record, "transactionTime")

        symbol = _optional_non_empty_str(raw_record, "symbol")
        order_ref = _optional_non_empty_str(raw_record, "orderId")
        trade_ref = _optional_non_empty_str(raw_record, "tradeId")

        funding = _optional_decimal_string(raw_record, "funding")
        source_fee = _optional_decimal_string(raw_record, "fee")
        trading_fees = -source_fee if source_fee is not None else None
        cash_flow = _require_decimal_string(raw_record, "cashFlow")
        reported_net_change = _require_decimal_string(raw_record, "change")

        if funding is not None and trading_fees is not None:
            expected_change = cash_flow + funding + trading_fees
            if reported_net_change != expected_change:
                raise BybitTransactionLogError(
                    "Bybit transaction components do not match reported change"
                )

        return BybitTransactionLogEntry(
            record_id=f"bybit-ledger:{event_id}",
            source_name=self._source_name,
            event_ref=event_id,
            event_type=event_type,
            instrument=symbol,
            settlement_currency=currency.upper(),
            occurred_at=_from_milliseconds(transaction_time),
            funding=funding,
            trading_fees=trading_fees,
            cash_flow=cash_flow,
            reported_net_change=reported_net_change,
            order_ref=order_ref,
            trade_ref=trade_ref,
        )


def _require_success_result(payload: JsonObject) -> Mapping[str, JsonValue]:
    ret_code = payload.get("retCode")
    if isinstance(ret_code, bool) or not isinstance(ret_code, int):
        raise BybitTransactionLogError("Bybit response field 'retCode' must be an int")
    if ret_code != 0:
        raise BybitTransactionLogError(f"Bybit API returned retCode {ret_code}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise BybitTransactionLogError("Bybit response field 'result' must be an object")
    return result


def _require_object_list(
    payload: Mapping[str, JsonValue],
    key: str,
) -> list[Mapping[str, JsonValue]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise BybitTransactionLogError(f"Bybit response field '{key}' must be a list")

    items: list[Mapping[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BybitTransactionLogError(f"Bybit response field '{key}' must contain objects")
        items.append(item)
    return items


def _require_non_empty_str(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BybitTransactionLogError(f"Bybit response field '{key}' must be a non-empty string")
    return value


def _optional_non_empty_str(
    payload: Mapping[str, JsonValue],
    key: str,
) -> str | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise BybitTransactionLogError(
            f"Bybit response field '{key}' must be a string when present"
        )
    return value


def _require_integer_string(payload: Mapping[str, JsonValue], key: str) -> int:
    value = _require_non_empty_str(payload, key)
    if not value.isdigit():
        raise BybitTransactionLogError(f"Bybit response field '{key}' must be an integer string")
    return int(value)


def _require_decimal_string(
    payload: Mapping[str, JsonValue],
    key: str,
) -> Decimal:
    value = _require_non_empty_str(payload, key)
    return _parse_decimal(value, key)


def _optional_decimal_string(
    payload: Mapping[str, JsonValue],
    key: str,
) -> Decimal | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BybitTransactionLogError(
            f"Bybit response field '{key}' must be a string when present"
        )
    return _parse_decimal(value, key)


def _parse_decimal(value: str, key: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BybitTransactionLogError(f"Bybit response field '{key}' must be decimal") from exc
    if not result.is_finite():
        raise BybitTransactionLogError(f"Bybit response field '{key}' must be finite")
    return result


def _require_cursor(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("nextPageCursor")
    if not isinstance(value, str):
        raise BybitTransactionLogError("Bybit response field 'nextPageCursor' must be a string")
    return value


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _to_milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _from_milliseconds(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise BybitTransactionLogError(
            "Bybit transaction timestamp is outside supported range"
        ) from exc
