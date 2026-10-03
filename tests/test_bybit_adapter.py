import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.bybit import (
    BybitInverseClosedPnlAdapter,
    BybitResponseError,
)
from freqtrade_truth.adapters.http import JsonObject, JsonValue
from freqtrade_truth.core.models import ClosedTradeQuery, FinancialField, SourceKind


def milliseconds(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))


def closed_pnl_item(
    *,
    order_id: str,
    updated_at: datetime,
    symbol: str = "BTCUSD",
    closed_pnl: str = "0.00123",
    open_fee: str | None = "0.00001",
    close_fee: str | None = "0.00002",
) -> dict[str, JsonValue]:
    item: dict[str, JsonValue] = {
        "symbol": symbol,
        "orderId": order_id,
        "updatedTime": milliseconds(updated_at),
        "closedPnl": closed_pnl,
    }
    if open_fee is not None:
        item["openFee"] = open_fee
    if close_fee is not None:
        item["closeFee"] = close_fee
    return item


def response(
    items: list[dict[str, JsonValue]],
    *,
    cursor: str = "",
    category: str = "inverse",
    ret_code: int = 0,
) -> JsonObject:
    normalized_items: list[JsonValue] = [dict(item) for item in items]
    return {
        "retCode": ret_code,
        "retMsg": "OK" if ret_code == 0 else "synthetic error",
        "result": {
            "category": category,
            "list": normalized_items,
            "nextPageCursor": cursor,
        },
        "time": 0,
    }


class RecordingTransport:
    def __init__(self, pages: list[JsonObject]) -> None:
        self._pages = list(pages)
        self.requests: list[tuple[str, dict[str, str | int | bool]]] = []

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        normalized = dict(params or {})
        self.requests.append((path, normalized))
        if not self._pages:
            raise AssertionError("unexpected synthetic request")
        return self._pages.pop(0)


def query(
    *,
    closed_from: datetime | None = None,
    closed_until: datetime | None = None,
    instrument: str | None = "BTCUSD",
    limit: int | None = None,
) -> ClosedTradeQuery:
    return ClosedTradeQuery(
        closed_from=closed_from or datetime(2030, 1, 1, tzinfo=UTC),
        closed_until=closed_until or datetime(2030, 1, 2, tzinfo=UTC),
        instrument=instrument,
        limit=limit,
    )


def adapter(transport: RecordingTransport, *, page_size: int = 100) -> BybitInverseClosedPnlAdapter:
    return BybitInverseClosedPnlAdapter(
        transport,
        symbol="BTCUSD",
        settlement_currency="BTC",
        page_size=page_size,
    )


def test_normalizes_inverse_btcusd_record_without_inventing_funding() -> None:
    updated_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [response([closed_pnl_item(order_id="synthetic-order-1", updated_at=updated_at)])]
    )

    records = asyncio.run(adapter(transport).fetch_closed_trades(query()))

    assert len(records) == 1
    record = records[0]
    assert record.source_kind is SourceKind.EXCHANGE
    assert record.source_name == "bybit"
    assert record.trade_ref == "synthetic-order-1"
    assert record.instrument == "BTCUSD"
    assert record.settlement_currency == "BTC"
    assert record.opened_at is None
    assert record.closed_at == updated_at
    assert record.price_pnl is None
    assert record.trading_fees == Decimal("-0.00003")
    assert record.funding is None
    assert record.reported_net_pnl == Decimal("0.00123")


def test_capabilities_are_explicit_about_available_components() -> None:
    transport = RecordingTransport([])
    capabilities = adapter(transport).capabilities()

    assert capabilities.supports(FinancialField.TRADING_FEES)
    assert capabilities.supports(FinancialField.REPORTED_NET_PNL)
    assert not capabilities.supports(FinancialField.FUNDING)
    assert not capabilities.supports(FinancialField.PRICE_PNL)


def test_cursor_pagination_is_followed_and_records_are_sorted() -> None:
    first_time = datetime(2030, 1, 1, 8, tzinfo=UTC)
    second_time = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [closed_pnl_item(order_id="synthetic-order-2", updated_at=second_time)],
                cursor="synthetic-cursor",
            ),
            response([closed_pnl_item(order_id="synthetic-order-1", updated_at=first_time)]),
        ]
    )

    records = asyncio.run(adapter(transport).fetch_closed_trades(query()))

    assert [record.trade_ref for record in records] == [
        "synthetic-order-1",
        "synthetic-order-2",
    ]
    assert transport.requests[0][0] == "v5/position/closed-pnl"
    assert "cursor" not in transport.requests[0][1]
    assert transport.requests[1][1]["cursor"] == "synthetic-cursor"


def test_long_query_is_split_into_at_most_seven_day_windows() -> None:
    closed_from = datetime(2030, 1, 1, tzinfo=UTC)
    closed_until = datetime(2030, 1, 20, tzinfo=UTC)
    transport = RecordingTransport([response([]), response([]), response([])])

    records = asyncio.run(
        adapter(transport).fetch_closed_trades(
            query(closed_from=closed_from, closed_until=closed_until)
        )
    )

    assert records == ()
    assert len(transport.requests) == 3

    windows = [
        (int(params["startTime"]), int(params["endTime"])) for _, params in transport.requests
    ]
    assert windows[0][0] == int(closed_from.timestamp() * 1000)
    assert windows[-1][1] == int(closed_until.timestamp() * 1000)

    max_span_ms = int(timedelta(days=7).total_seconds() * 1000)
    for start_ms, end_ms in windows:
        assert end_ms - start_ms < max_span_ms

    assert windows[1][0] == windows[0][1] + 1
    assert windows[2][0] == windows[1][1] + 1


def test_instrument_mismatch_returns_empty_without_request() -> None:
    transport = RecordingTransport([])

    records = asyncio.run(adapter(transport).fetch_closed_trades(query(instrument="ETHUSD")))

    assert records == ()
    assert transport.requests == []


def test_query_limit_is_applied_after_deterministic_sorting() -> None:
    early = datetime(2030, 1, 1, 8, tzinfo=UTC)
    late = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    closed_pnl_item(order_id="synthetic-order-2", updated_at=late),
                    closed_pnl_item(order_id="synthetic-order-1", updated_at=early),
                ]
            )
        ]
    )

    records = asyncio.run(adapter(transport).fetch_closed_trades(query(limit=1)))

    assert [record.trade_ref for record in records] == ["synthetic-order-1"]


def test_missing_both_fee_fields_preserves_missing_value() -> None:
    updated_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    closed_pnl_item(
                        order_id="synthetic-order-1",
                        updated_at=updated_at,
                        open_fee=None,
                        close_fee=None,
                    )
                ]
            )
        ]
    )

    records = asyncio.run(adapter(transport).fetch_closed_trades(query()))

    assert records[0].trading_fees is None


@pytest.mark.parametrize(
    ("open_fee", "close_fee"),
    [("0.00001", None), (None, "0.00002")],
)
def test_partial_fee_breakdown_fails_closed(
    open_fee: str | None,
    close_fee: str | None,
) -> None:
    updated_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    closed_pnl_item(
                        order_id="synthetic-order-1",
                        updated_at=updated_at,
                        open_fee=open_fee,
                        close_fee=close_fee,
                    )
                ]
            )
        ]
    )

    with pytest.raises(BybitResponseError, match="openFee and closeFee"):
        asyncio.run(adapter(transport).fetch_closed_trades(query()))


@pytest.mark.parametrize(
    "payload",
    [
        response([], category="linear"),
        response([], ret_code=10001),
    ],
)
def test_invalid_or_unsuccessful_response_fails_closed(payload: JsonObject) -> None:
    transport = RecordingTransport([payload])

    with pytest.raises(BybitResponseError):
        asyncio.run(adapter(transport).fetch_closed_trades(query()))


def test_symbol_mismatch_in_response_fails_closed() -> None:
    updated_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    closed_pnl_item(
                        order_id="synthetic-order-1",
                        updated_at=updated_at,
                        symbol="ETHUSD",
                    )
                ]
            )
        ]
    )

    with pytest.raises(BybitResponseError, match="symbol"):
        asyncio.run(adapter(transport).fetch_closed_trades(query()))


def test_repeated_cursor_fails_closed() -> None:
    transport = RecordingTransport(
        [
            response([], cursor="synthetic-cursor"),
            response([], cursor="synthetic-cursor"),
        ]
    )

    with pytest.raises(BybitResponseError, match="cursor repeated"):
        asyncio.run(adapter(transport).fetch_closed_trades(query()))


@pytest.mark.parametrize(
    ("symbol", "settlement_currency", "page_size", "source_name"),
    [
        ("", "BTC", 100, "bybit"),
        ("btcusd", "BTC", 100, "bybit"),
        ("BTCUSD", "", 100, "bybit"),
        ("BTCUSD", "btc", 100, "bybit"),
        ("BTCUSD", "BTC", 0, "bybit"),
        ("BTCUSD", "BTC", 101, "bybit"),
        ("BTCUSD", "BTC", 100, "   "),
    ],
)
def test_constructor_rejects_invalid_configuration(
    symbol: str,
    settlement_currency: str,
    page_size: int,
    source_name: str,
) -> None:
    with pytest.raises(ValueError):
        BybitInverseClosedPnlAdapter(
            RecordingTransport([]),
            symbol=symbol,
            settlement_currency=settlement_currency,
            page_size=page_size,
            source_name=source_name,
        )


def test_normalized_instrument_can_differ_from_exchange_symbol() -> None:
    updated_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [response([closed_pnl_item(order_id="synthetic-order-1", updated_at=updated_at)])]
    )
    normalized = BybitInverseClosedPnlAdapter(
        transport,
        symbol="BTCUSD",
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )

    records = asyncio.run(
        normalized.fetch_closed_trades(query(instrument="BTC/USD:BTC"))
    )

    assert len(records) == 1
    assert records[0].instrument == "BTC/USD:BTC"


def test_normalized_instrument_mismatch_returns_empty_without_request() -> None:
    transport = RecordingTransport([])
    normalized = BybitInverseClosedPnlAdapter(
        transport,
        symbol="BTCUSD",
        settlement_currency="BTC",
        normalized_instrument="BTC/USD:BTC",
    )

    records = asyncio.run(normalized.fetch_closed_trades(query(instrument="BTCUSD")))

    assert records == ()
    assert transport.requests == []
