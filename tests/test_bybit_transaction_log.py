import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.bybit_transaction_log import (
    BybitInverseTransactionLogReader,
    BybitTransactionLogError,
    BybitTransactionLogQuery,
)
from freqtrade_truth.adapters.http import JsonObject, JsonValue


def milliseconds(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))


def item(
    *,
    event_id: str,
    occurred_at: datetime,
    symbol: str = "BTCUSD",
    category: str = "inverse",
    currency: str = "BTC",
    event_type: str = "SETTLEMENT",
    funding: str = "0.00030",
    fee: str = "0.00010",
    cash_flow: str = "0.00100",
    change: str = "0.00120",
    order_id: str = "synthetic-order",
    trade_id: str = "synthetic-trade",
) -> dict[str, JsonValue]:
    return {
        "id": event_id,
        "symbol": symbol,
        "category": category,
        "side": "Buy",
        "transactionTime": milliseconds(occurred_at),
        "type": event_type,
        "currency": currency,
        "funding": funding,
        "fee": fee,
        "cashFlow": cash_flow,
        "change": change,
        "orderId": order_id,
        "tradeId": trade_id,
    }


def response(
    items: list[dict[str, JsonValue]],
    *,
    cursor: str = "",
    ret_code: int = 0,
) -> JsonObject:
    normalized_items: list[JsonValue] = [dict(entry) for entry in items]
    return {
        "retCode": ret_code,
        "retMsg": "OK" if ret_code == 0 else "synthetic error",
        "result": {
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
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int | None = None,
) -> BybitTransactionLogQuery:
    return BybitTransactionLogQuery(
        start_at=start_at or datetime(2030, 1, 1, tzinfo=UTC),
        end_at=end_at or datetime(2030, 1, 2, tzinfo=UTC),
        limit=limit,
    )


def reader(
    transport: RecordingTransport,
    *,
    page_size: int = 50,
) -> BybitInverseTransactionLogReader:
    return BybitInverseTransactionLogReader(
        transport,
        symbol="BTCUSD",
        settlement_currency="BTC",
        page_size=page_size,
    )


def test_normalizes_funding_fee_and_reported_change() -> None:
    occurred_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [response([item(event_id="synthetic-event-1", occurred_at=occurred_at)])]
    )

    records = asyncio.run(reader(transport).fetch(query()))

    assert len(records) == 1
    record = records[0]
    assert record.record_id == "bybit-ledger:synthetic-event-1"
    assert record.event_type == "SETTLEMENT"
    assert record.instrument == "BTCUSD"
    assert record.settlement_currency == "BTC"
    assert record.occurred_at == occurred_at
    assert record.funding == Decimal("0.00030")
    assert record.trading_fees == Decimal("-0.00010")
    assert record.cash_flow == Decimal("0.00100")
    assert record.reported_net_change == Decimal("0.00120")
    assert record.order_ref == "synthetic-order"
    assert record.trade_ref == "synthetic-trade"


def test_fee_rebate_becomes_positive_equity_effect() -> None:
    occurred_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    item(
                        event_id="synthetic-event-1",
                        occurred_at=occurred_at,
                        funding="0",
                        fee="-0.00010",
                        cash_flow="0",
                        change="0.00010",
                    )
                ]
            )
        ]
    )

    records = asyncio.run(reader(transport).fetch(query()))

    assert records[0].trading_fees == Decimal("0.00010")
    assert records[0].reported_net_change == Decimal("0.00010")


def test_empty_optional_funding_and_fee_remain_missing() -> None:
    occurred_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    item(
                        event_id="synthetic-event-1",
                        occurred_at=occurred_at,
                        funding="",
                        fee="",
                        cash_flow="0.00100",
                        change="0.00100",
                    )
                ]
            )
        ]
    )

    records = asyncio.run(reader(transport).fetch(query()))

    assert records[0].funding is None
    assert records[0].trading_fees is None


def test_component_mismatch_fails_closed() -> None:
    occurred_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    item(
                        event_id="synthetic-event-1",
                        occurred_at=occurred_at,
                        change="9.99",
                    )
                ]
            )
        ]
    )

    with pytest.raises(BybitTransactionLogError, match="components"):
        asyncio.run(reader(transport).fetch(query()))


def test_cursor_pagination_and_sorting() -> None:
    early = datetime(2030, 1, 1, 8, tzinfo=UTC)
    late = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [item(event_id="synthetic-event-2", occurred_at=late)],
                cursor="synthetic-cursor",
            ),
            response([item(event_id="synthetic-event-1", occurred_at=early)]),
        ]
    )

    records = asyncio.run(reader(transport).fetch(query()))

    assert [record.event_ref for record in records] == [
        "synthetic-event-1",
        "synthetic-event-2",
    ]
    assert transport.requests[0][0] == "v5/account/transaction-log"
    assert transport.requests[0][1]["accountType"] == "UNIFIED"
    assert transport.requests[0][1]["category"] == "inverse"
    assert transport.requests[0][1]["currency"] == "BTC"
    assert "cursor" not in transport.requests[0][1]
    assert transport.requests[1][1]["cursor"] == "synthetic-cursor"


def test_long_query_is_split_into_seven_day_windows() -> None:
    start_at = datetime(2030, 1, 1, tzinfo=UTC)
    end_at = datetime(2030, 1, 20, tzinfo=UTC)
    transport = RecordingTransport([response([]), response([]), response([])])

    records = asyncio.run(
        reader(transport).fetch(query(start_at=start_at, end_at=end_at))
    )

    assert records == ()
    assert len(transport.requests) == 3

    windows = [
        (int(params["startTime"]), int(params["endTime"])) for _, params in transport.requests
    ]
    max_span_ms = int(timedelta(days=7).total_seconds() * 1000)
    for start_ms, end_ms in windows:
        assert end_ms - start_ms < max_span_ms

    assert windows[1][0] == windows[0][1] + 1
    assert windows[2][0] == windows[1][1] + 1


def test_other_symbols_are_filtered_locally() -> None:
    occurred_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    item(
                        event_id="synthetic-event-1",
                        occurred_at=occurred_at,
                        symbol="ETHUSD",
                    )
                ]
            )
        ]
    )

    records = asyncio.run(reader(transport).fetch(query()))

    assert records == ()


def test_currency_or_category_mismatch_fails_closed() -> None:
    occurred_at = datetime(2030, 1, 1, 12, tzinfo=UTC)

    wrong_currency = RecordingTransport(
        [
            response(
                [
                    item(
                        event_id="synthetic-event-1",
                        occurred_at=occurred_at,
                        currency="ETH",
                    )
                ]
            )
        ]
    )
    with pytest.raises(BybitTransactionLogError, match="currency"):
        asyncio.run(reader(wrong_currency).fetch(query()))

    wrong_category = RecordingTransport(
        [
            response(
                [
                    item(
                        event_id="synthetic-event-1",
                        occurred_at=occurred_at,
                        category="linear",
                    )
                ]
            )
        ]
    )
    with pytest.raises(BybitTransactionLogError, match="category"):
        asyncio.run(reader(wrong_category).fetch(query()))


def test_repeated_cursor_and_duplicate_records_fail_closed() -> None:
    repeated_cursor = RecordingTransport(
        [
            response([], cursor="synthetic-cursor"),
            response([], cursor="synthetic-cursor"),
        ]
    )
    with pytest.raises(BybitTransactionLogError, match="cursor repeated"):
        asyncio.run(reader(repeated_cursor).fetch(query()))

    occurred_at = datetime(2030, 1, 1, 12, tzinfo=UTC)
    duplicate = item(event_id="synthetic-event-1", occurred_at=occurred_at)
    duplicate_records = RecordingTransport(
        [
            response([duplicate], cursor="next"),
            response([duplicate]),
        ]
    )
    with pytest.raises(BybitTransactionLogError, match="duplicate"):
        asyncio.run(reader(duplicate_records).fetch(query()))


def test_query_limit_is_applied_after_sorting() -> None:
    early = datetime(2030, 1, 1, 8, tzinfo=UTC)
    late = datetime(2030, 1, 1, 12, tzinfo=UTC)
    transport = RecordingTransport(
        [
            response(
                [
                    item(event_id="synthetic-event-2", occurred_at=late),
                    item(event_id="synthetic-event-1", occurred_at=early),
                ]
            )
        ]
    )

    records = asyncio.run(reader(transport).fetch(query(limit=1)))

    assert [record.event_ref for record in records] == ["synthetic-event-1"]


@pytest.mark.parametrize(
    ("symbol", "currency", "page_size", "source_name"),
    [
        ("", "BTC", 50, "bybit"),
        ("btcusd", "BTC", 50, "bybit"),
        ("BTCUSD", "", 50, "bybit"),
        ("BTCUSD", "btc", 50, "bybit"),
        ("BTCUSD", "BTC", 0, "bybit"),
        ("BTCUSD", "BTC", 51, "bybit"),
        ("BTCUSD", "BTC", 50, "   "),
    ],
)
def test_constructor_rejects_invalid_configuration(
    symbol: str,
    currency: str,
    page_size: int,
    source_name: str,
) -> None:
    with pytest.raises(ValueError):
        BybitInverseTransactionLogReader(
            RecordingTransport([]),
            symbol=symbol,
            settlement_currency=currency,
            page_size=page_size,
            source_name=source_name,
        )


def test_query_requires_utc_and_valid_range() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        BybitTransactionLogQuery(
            start_at=datetime(2030, 1, 1),
            end_at=datetime(2030, 1, 2, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="after"):
        BybitTransactionLogQuery(
            start_at=datetime(2030, 1, 2, tzinfo=UTC),
            end_at=datetime(2030, 1, 1, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="limit"):
        BybitTransactionLogQuery(
            start_at=datetime(2030, 1, 1, tzinfo=UTC),
            end_at=datetime(2030, 1, 2, tzinfo=UTC),
            limit=0,
        )
