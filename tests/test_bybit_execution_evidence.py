"""Synthetic, GET-only Bybit execution-ID evidence for issue #15."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from freqtrade_truth.adapters.bybit_execution_evidence import (
    BybitExecutionError,
    BybitExecutionQuery,
    BybitInverseExecutionReader,
)
from freqtrade_truth.adapters.http import JsonObject, JsonValue

START = datetime(2030, 1, 1, tzinfo=UTC)
END = datetime(2030, 1, 2, tzinfo=UTC)


def execution(
    ref: str,
    order: str = "synthetic-order",
    *,
    symbol: str = "BTCUSD",
    kind: str = "Trade",
    quantity: str = "0.2",
    fee: str | None = "0.00002",
    currency: str | None = "BTC",
    time: datetime = START + timedelta(hours=12),
) -> dict[str, JsonValue]:
    data: dict[str, JsonValue] = {
        "execId": ref,
        "orderId": order,
        "symbol": symbol,
        "execType": kind,
        "execQty": quantity,
        "execTime": str(int(time.timestamp() * 1000)),
    }
    if fee is not None:
        data["execFee"] = fee
    if currency is not None:
        data["feeCurrency"] = currency
    return data


def response(
    records: list[dict[str, JsonValue]],
    *,
    cursor: str = "",
    category: str = "inverse",
    code: int = 0,
) -> JsonObject:
    return {
        "retCode": code,
        "retMsg": "synthetic",
        "time": 0,
        "result": {
            "category": category,
            "list": [dict(record) for record in records],
            "nextPageCursor": cursor,
        },
    }


class RecordingTransport:
    def __init__(self, pages: list[JsonObject]):
        self.pages = pages
        self.requests: list[tuple[str, dict[str, str | int | bool]]] = []

    async def get_json(
        self, path: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        self.requests.append((path, dict(params or {})))
        if not self.pages:
            raise AssertionError("synthetic transport exhausted")
        return self.pages.pop(0)


def query(
    order_id: str = "synthetic-order", start: datetime = START, end: datetime = END
) -> BybitExecutionQuery:
    return BybitExecutionQuery(order_id=order_id, start_at=start, end_at=end)


def reader(transport: RecordingTransport, page_size: int = 100) -> BybitInverseExecutionReader:
    return BybitInverseExecutionReader(
        transport, symbol="BTCUSD", settlement_currency="BTC", page_size=page_size
    )


def test_multiple_partial_fills_keep_distinct_execution_ids_and_source_fees() -> None:
    source = RecordingTransport(
        [
            response([execution("fill-2", quantity="0.25", fee="-0.00001")], cursor="next"),
            response(
                [
                    execution(
                        "fill-1", quantity="0.10", fee="0.00002", time=START + timedelta(hours=1)
                    )
                ]
            ),
        ]
    )
    evidence = asyncio.run(reader(source).fetch_executions(query()))
    assert evidence.pagination_exhausted is True
    assert evidence.order_ref == "synthetic-order"
    assert evidence.symbol == "BTCUSD"
    assert evidence.settlement_currency == "BTC"
    assert [r.execution_ref for r in evidence.records] == ["fill-1", "fill-2"]
    assert [r.executed_qty for r in evidence.records] == [Decimal("0.10"), Decimal("0.25")]
    assert [r.source_exec_fee for r in evidence.records] == [
        Decimal("0.00002"),
        Decimal("-0.00001"),
    ]
    assert [r.fee_currency for r in evidence.records] == ["BTC", "BTC"]
    assert not hasattr(evidence, "position_ownership_verified")
    assert not hasattr(evidence, "reconstructed_net_pnl")
    assert all(path == "v5/execution/list" for path, _ in source.requests)
    assert source.requests[0][1]["orderId"] == "synthetic-order"
    assert source.requests[0][1]["category"] == "inverse"
    assert source.requests[0][1]["execType"] == "Trade"
    assert source.requests[1][1]["cursor"] == "next"


@pytest.mark.parametrize(
    ("changed", "expected_error"),
    [
        ({"orderId": "different-order"}, "orderId"),
        ({"symbol": "ETHUSD"}, "symbol"),
        ({"execType": "Funding"}, "execType"),
        ({"execType": "BustTrade"}, "execType"),
        ({"execId": ""}, "execId"),
        ({"execQty": "0"}, "execQty"),
        ({"execQty": "-1"}, "execQty"),
        ({"execQty": "NaN"}, "execQty"),
        ({"execQty": "Infinity"}, "execQty"),
        ({"execFee": "inf"}, "execFee"),
        ({"feeCurrency": "usd"}, "feeCurrency"),
        ({"execTime": "not-time"}, "execTime"),
    ],
)
def test_response_mismatch_or_malformed_fill_fails_closed(
    changed: dict[str, JsonValue], expected_error: str
) -> None:
    payload = execution("fill-1")
    payload.update(changed)
    source = RecordingTransport([response([payload])])
    with pytest.raises(BybitExecutionError, match=expected_error):
        asyncio.run(reader(source).fetch_executions(query()))


def test_fee_is_not_invented_when_source_field_missing() -> None:
    source = RecordingTransport([response([execution("fill-1", fee=None, currency="")])])
    ev = asyncio.run(reader(source).fetch_executions(query())).records[0]
    assert ev.source_exec_fee is None
    assert ev.fee_currency is None


def test_duplicate_exec_id_across_pages_fails_closed() -> None:
    one = execution("same")
    source = RecordingTransport([response([one], cursor="next"), response([one])])
    with pytest.raises(BybitExecutionError, match="duplicate"):
        asyncio.run(reader(source).fetch_executions(query()))


def test_repeated_cursor_fails_closed() -> None:
    source = RecordingTransport([response([], cursor="next"), response([], cursor="next")])
    with pytest.raises(BybitExecutionError, match="cursor"):
        asyncio.run(reader(source).fetch_executions(query()))


def test_rejected_or_wrong_category_response_fails_closed() -> None:
    for raw in (response([], code=10001), response([], category="linear")):
        source = RecordingTransport([raw])
        with pytest.raises(BybitExecutionError):
            asyncio.run(reader(source).fetch_executions(query()))


def test_order_id_filter_never_allows_wrong_symbol_in_result() -> None:
    # Official Bybit API ignores symbol filter when orderId is supplied.
    source = RecordingTransport([response([execution("fill-1", symbol="ETHUSD")])])
    with pytest.raises(BybitExecutionError, match="symbol"):
        asyncio.run(reader(source).fetch_executions(query()))


def test_seven_day_subwindows_preserve_boundaries_without_skipped_milliseconds() -> None:
    end = START + timedelta(days=19)
    source = RecordingTransport([response([]), response([]), response([])])
    ev = asyncio.run(reader(source).fetch_executions(query(end=end)))
    assert ev.records == ()
    assert ev.start_at == START
    assert ev.end_at == end
    assert len(source.requests) == 3
    windows = [(int(p["startTime"]), int(p["endTime"])) for _, p in source.requests]
    assert windows[1][0] == windows[0][1] + 1
    assert windows[2][0] == windows[1][1] + 1
    assert windows[-1][1] == int(end.timestamp() * 1000)


def test_execution_outside_the_requested_bounds_fails_closed() -> None:
    source = RecordingTransport(
        [response([execution("fill-1", time=END + timedelta(milliseconds=1))])]
    )
    with pytest.raises(BybitExecutionError, match="window"):
        asyncio.run(reader(source).fetch_executions(query()))


@pytest.mark.parametrize("order_id", ["", " ", " synthetic-order ", "a?b", "a/b"])
def test_invalid_order_id_rejected_before_http(order_id: str) -> None:
    source = RecordingTransport([])
    with pytest.raises(ValueError, match="order_id"):
        asyncio.run(reader(source).fetch_executions(query(order_id=order_id)))
    assert source.requests == []


def test_unaligned_or_reverse_query_rejected_before_http() -> None:
    source = RecordingTransport([])
    for start, end in (
        (START + timedelta(microseconds=1), END),
        (START, END + timedelta(microseconds=1)),
        (END, START),
        (START.replace(tzinfo=None), END),
    ):
        with pytest.raises(ValueError):
            asyncio.run(reader(source).fetch_executions(query(start=start, end=end)))
    assert source.requests == []


def test_missing_pages_never_returns_complete_execution_evidence() -> None:
    source = RecordingTransport([response([], cursor="next")])
    with pytest.raises(AssertionError, match="exhausted"):
        asyncio.run(reader(source).fetch_executions(query()))
