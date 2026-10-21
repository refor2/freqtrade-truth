"""Read-only Bybit inverse Trade execution evidence for issue #15.

Exact order and unique execution IDs are stronger source candidates than
timestamps; they still do not prove complete lifecycle or bot/account ownership.
The module is deliberately not exported as a stable public API.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, JsonValue

_MAX_PAGE_SIZE = 100
_MAX_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


class BybitExecutionError(RuntimeError):
    """Malformed or inconsistent Bybit execution evidence."""


def _require_utc_millis(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be UTC timezone-aware")
    if value.microsecond % 1000:
        raise ValueError(f"{field} must be millisecond-aligned")


@dataclass(frozen=True, slots=True)
class BybitExecutionQuery:
    order_id: str
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        if (
            not self.order_id
            or self.order_id != self.order_id.strip()
            or any(char in self.order_id for char in "/?#")
        ):
            raise ValueError("order_id must be a non-empty literal exchange order ID")
        _require_utc_millis(self.start_at, "start_at")
        _require_utc_millis(self.end_at, "end_at")
        if self.end_at < self.start_at:
            raise ValueError("end_at must be on or after start_at")


@dataclass(frozen=True, slots=True)
class BybitTradeExecution:
    """Source-native quantity and fee; no conversion or financial attribution."""

    execution_ref: str
    order_ref: str
    symbol: str
    occurred_at: datetime
    executed_qty: Decimal
    source_exec_fee: Decimal | None
    fee_currency: str | None


@dataclass(frozen=True, slots=True)
class BybitExecutionWindowEvidence:
    """Exhausted cursor traversal within bounds; NOT verified trade ownership."""

    source_name: str
    order_ref: str
    symbol: str
    settlement_currency: str
    start_at: datetime
    end_at: datetime
    records: tuple[BybitTradeExecution, ...]
    pagination_exhausted: bool = True


class BybitInverseExecutionReader:
    """Read exact orderId and Trade execId via GET /v5/execution/list only.

    The Bybit API prioritizes orderId over symbol query filters. Every returned
    record is independently checked against the configured symbol and order.
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
            raise ValueError("symbol must be uppercase and nonempty")
        if not settlement_currency or settlement_currency != settlement_currency.upper():
            raise ValueError("settlement_currency must be uppercase and nonempty")
        if not 1 <= page_size <= _MAX_PAGE_SIZE:
            raise ValueError("page_size must be 1..100")
        if not source_name.strip():
            raise ValueError("source_name must be non-empty")
        self._transport = transport
        self._symbol = symbol
        self._settlement_currency = settlement_currency
        self._page_size = page_size
        self._source_name = source_name

    async def fetch_executions(self, query: BybitExecutionQuery) -> BybitExecutionWindowEvidence:
        # The frozen query validates all potential unsafe inputs before HTTP.
        start = int(query.start_at.timestamp() * 1000)
        end = int(query.end_at.timestamp() * 1000)
        window_start = start
        seen_executions: set[str] = set()
        collected: list[BybitTradeExecution] = []

        while window_start <= end:
            window_end = min(window_start + _MAX_WINDOW_MS - 1, end)
            cursor: str | None = None
            seen_cursors: set[str] = set()

            while True:
                params: dict[str, str | int | bool] = {
                    "category": "inverse",
                    "orderId": query.order_id,
                    "execType": "Trade",
                    "startTime": window_start,
                    "endTime": window_end,
                    "limit": self._page_size,
                }
                if cursor is not None:
                    params["cursor"] = cursor
                payload = await self._transport.get_json("v5/execution/list", params)
                result = _result(payload)
                if result.get("category") != "inverse":
                    raise BybitExecutionError("response category must be inverse")
                records = result.get("list")
                if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
                    raise BybitExecutionError("response list must contain objects")

                for raw in records:
                    if not isinstance(raw, dict):
                        raise BybitExecutionError("response entry must be an object")
                    execution = self._normalize(raw, order_id=query.order_id)
                    time_ms = int(execution.occurred_at.timestamp() * 1000)
                    if time_ms < window_start or time_ms > window_end:
                        raise BybitExecutionError("execution timestamp outside requested window")
                    if execution.execution_ref in seen_executions:
                        raise BybitExecutionError("duplicate execution ID")
                    seen_executions.add(execution.execution_ref)
                    collected.append(execution)

                new_cursor = result.get("nextPageCursor")
                if not isinstance(new_cursor, str):
                    raise BybitExecutionError("nextPageCursor must be a string")
                if not new_cursor:
                    break
                if new_cursor in seen_cursors:
                    raise BybitExecutionError("pagination cursor repeated")
                seen_cursors.add(new_cursor)
                cursor = new_cursor
            window_start = window_end + 1

        collected.sort(key=lambda item: (item.occurred_at, item.execution_ref))
        return BybitExecutionWindowEvidence(
            source_name=self._source_name,
            order_ref=query.order_id,
            symbol=self._symbol,
            settlement_currency=self._settlement_currency,
            start_at=query.start_at,
            end_at=query.end_at,
            records=tuple(collected),
        )

    def _normalize(self, record: Mapping[str, JsonValue], *, order_id: str) -> BybitTradeExecution:
        order = _string(record, "orderId")
        if order != order_id:
            raise BybitExecutionError("response orderId differs from requested order")
        symbol = _string(record, "symbol")
        if symbol != self._symbol:
            raise BybitExecutionError("response symbol differs from configured symbol")
        if _string(record, "execType") != "Trade":
            raise BybitExecutionError("response execType must be Trade")

        ref = _string(record, "execId")
        qty = _decimal(record, "execQty")
        if qty <= 0:
            raise BybitExecutionError("execQty must be strictly positive")
        timestamp = _string(record, "execTime")
        if not timestamp.isdigit():
            raise BybitExecutionError("execTime must be decimal milliseconds")
        try:
            when = datetime.fromtimestamp(int(timestamp) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise BybitExecutionError("execTime is outside supported range") from exc

        fee: Decimal | None = None
        if record.get("execFee") is not None and record.get("execFee") != "":
            fee = _decimal(record, "execFee")
        currency = record.get("feeCurrency")
        if currency in ("", None):
            currency = None
        elif not isinstance(currency, str) or currency != currency.upper() or not currency.strip():
            raise BybitExecutionError("feeCurrency must be uppercase when supplied")

        return BybitTradeExecution(
            execution_ref=ref,
            order_ref=order,
            symbol=symbol,
            occurred_at=when,
            executed_qty=qty,
            source_exec_fee=fee,
            fee_currency=currency,
        )


def _result(payload: JsonObject) -> Mapping[str, JsonValue]:
    code = payload.get("retCode")
    if isinstance(code, bool) or not isinstance(code, int) or code != 0:
        raise BybitExecutionError("Bybit execution response retCode not successful")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise BybitExecutionError("response result must be an object")
    return result


def _string(record: Mapping[str, JsonValue], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BybitExecutionError(f"{key} must be a nonempty string")
    return value


def _decimal(record: Mapping[str, JsonValue], key: str) -> Decimal:
    value = _string(record, key)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BybitExecutionError(f"{key} must be decimal") from exc
    if not result.is_finite():
        raise BybitExecutionError(f"{key} must be finite")
    return result
