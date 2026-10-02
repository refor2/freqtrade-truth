"""Read-only adapter for the public Freqtrade REST trades endpoint."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from freqtrade_truth.adapters.http import JsonHttpTransport, JsonObject, JsonValue
from freqtrade_truth.core.models import (
    AdapterCapabilities,
    ClosedTradeQuery,
    FinancialField,
    NormalizedTradeRecord,
    SourceKind,
)

_MAX_PAGE_SIZE = 500


class FreqtradeResponseError(RuntimeError):
    """Raised when Freqtrade returns an invalid or ambiguous trade payload."""


class FreqtradeReadAdapter:
    """Fetch and normalize closed trades without mutating Freqtrade state."""

    def __init__(
        self,
        transport: JsonHttpTransport,
        *,
        page_size: int = _MAX_PAGE_SIZE,
        source_name: str = "freqtrade",
    ) -> None:
        if page_size <= 0 or page_size > _MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
        if not source_name.strip():
            raise ValueError("source_name must not be empty")

        self._transport = transport
        self._page_size = page_size
        self._source_name = source_name

    @property
    def name(self) -> str:
        return self._source_name

    @property
    def source_kind(self) -> SourceKind:
        return SourceKind.FREQTRADE

    def capabilities(self) -> AdapterCapabilities:
        # Freqtrade reports net trade PnL and, where applicable, funding fees.
        # Price-only PnL and fee decomposition are intentionally not synthesized.
        return AdapterCapabilities(
            fields=frozenset(
                {
                    FinancialField.FUNDING,
                    FinancialField.REPORTED_NET_PNL,
                }
            )
        )

    async def fetch_closed_trades(
        self,
        query: ClosedTradeQuery,
    ) -> Sequence[NormalizedTradeRecord]:
        records: list[NormalizedTradeRecord] = []
        offset = 0

        while True:
            payload = await self._transport.get_json(
                "trades",
                {
                    "limit": self._page_size,
                    "offset": offset,
                    "order_by_id": False,
                },
            )

            trades = _require_trade_list(payload)
            total_trades = _require_non_negative_int(payload, "total_trades")
            response_offset = _require_non_negative_int(payload, "offset")

            if response_offset != offset:
                raise FreqtradeResponseError("Freqtrade response offset does not match request")

            for raw_trade in trades:
                record = self._normalize_trade(raw_trade)
                if record is None:
                    continue
                if record.closed_at < query.closed_from or record.closed_at > query.closed_until:
                    continue
                if query.instrument is not None and record.instrument != query.instrument:
                    continue

                records.append(record)
                if query.limit is not None and len(records) >= query.limit:
                    return tuple(records)

            page_count = len(trades)
            if page_count == 0:
                break

            offset += page_count
            if offset >= total_trades:
                break

        records.sort(key=lambda record: (record.closed_at, record.record_id))
        return tuple(records)

    def _normalize_trade(
        self,
        raw_trade: Mapping[str, JsonValue],
    ) -> NormalizedTradeRecord | None:
        if _require_bool(raw_trade, "is_open"):
            return None

        trade_id = _require_int(raw_trade, "trade_id")
        pair = _require_non_empty_str(raw_trade, "pair")
        quote_currency = _require_non_empty_str(raw_trade, "quote_currency")

        close_timestamp = _require_int(raw_trade, "close_timestamp")
        open_timestamp = _optional_int(raw_trade, "open_timestamp")

        settlement_currency = _settlement_currency(pair, quote_currency)
        reported_net_pnl = _optional_decimal(raw_trade, "profit_abs")
        funding = _optional_decimal(raw_trade, "funding_fees")

        return NormalizedTradeRecord(
            record_id=f"freqtrade:{trade_id}",
            source_kind=SourceKind.FREQTRADE,
            source_name=self.name,
            trade_ref=str(trade_id),
            instrument=pair,
            settlement_currency=settlement_currency,
            opened_at=_from_milliseconds(open_timestamp) if open_timestamp is not None else None,
            closed_at=_from_milliseconds(close_timestamp),
            funding=funding,
            reported_net_pnl=reported_net_pnl,
        )


def _require_trade_list(payload: JsonObject) -> list[Mapping[str, JsonValue]]:
    value = payload.get("trades")
    if not isinstance(value, list):
        raise FreqtradeResponseError("Freqtrade response field 'trades' must be a list")

    trades: list[Mapping[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise FreqtradeResponseError("Freqtrade trade entry must be a JSON object")
        trades.append(item)
    return trades


def _require_non_negative_int(payload: Mapping[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FreqtradeResponseError(f"Freqtrade response field '{key}' must be a non-negative int")
    return value


def _require_int(payload: Mapping[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FreqtradeResponseError(f"Freqtrade trade field '{key}' must be an int")
    return value


def _optional_int(payload: Mapping[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise FreqtradeResponseError(f"Freqtrade trade field '{key}' must be an int when present")
    return value


def _require_bool(payload: Mapping[str, JsonValue], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise FreqtradeResponseError(f"Freqtrade trade field '{key}' must be a bool")
    return value


def _require_non_empty_str(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FreqtradeResponseError(f"Freqtrade trade field '{key}' must be a non-empty string")
    return value


def _optional_decimal(payload: Mapping[str, JsonValue], key: str) -> Decimal | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise FreqtradeResponseError(f"Freqtrade trade field '{key}' must be numeric when present")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except ArithmeticError as exc:
            raise FreqtradeResponseError(
                f"Freqtrade trade field '{key}' must be numeric when present"
            ) from exc
    else:
        raise FreqtradeResponseError(f"Freqtrade trade field '{key}' must be numeric when present")

    if not result.is_finite():
        raise FreqtradeResponseError(f"Freqtrade trade field '{key}' must be finite")
    return result


def _from_milliseconds(timestamp_ms: int) -> datetime:
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise FreqtradeResponseError("Freqtrade timestamp is outside supported range") from exc


def _settlement_currency(pair: str, quote_currency: str) -> str:
    if ":" in pair:
        settlement = pair.rsplit(":", 1)[1].strip()
        if settlement:
            return settlement.upper()
    return quote_currency.upper()
