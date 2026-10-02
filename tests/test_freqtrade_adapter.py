import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from freqtrade_truth.adapters.base import ReadOnlyTradeAdapter
from freqtrade_truth.adapters.freqtrade import (
    FreqtradeReadAdapter,
    FreqtradeResponseError,
)
from freqtrade_truth.adapters.http import JsonObject
from freqtrade_truth.core.models import ClosedTradeQuery, FinancialField, SourceKind

FIXTURES = Path(__file__).parent / "fixtures"


class FixtureTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str | int | bool]]] = []

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        normalized_params = dict(params or {})
        self.requests.append((path, normalized_params))
        offset = int(normalized_params.get("offset", 0))
        fixture = FIXTURES / f"freqtrade_trades_page_{offset}.json"
        return cast(
            JsonObject,
            json.loads(fixture.read_text(encoding="utf-8"), parse_float=Decimal),
        )


class StaticTransport:
    def __init__(self, payload: JsonObject) -> None:
        self._payload = payload

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        del path, params
        return self._payload


def query(
    *,
    instrument: str | None = None,
    limit: int | None = None,
) -> ClosedTradeQuery:
    return ClosedTradeQuery(
        closed_from=datetime(2030, 1, 1, tzinfo=UTC),
        closed_until=datetime(2030, 1, 3, tzinfo=UTC),
        instrument=instrument,
        limit=limit,
    )


def test_adapter_is_structurally_read_only() -> None:
    adapter = FreqtradeReadAdapter(FixtureTransport(), page_size=2)

    assert isinstance(adapter, ReadOnlyTradeAdapter)
    assert adapter.source_kind is SourceKind.FREQTRADE
    assert adapter.capabilities().supports(FinancialField.REPORTED_NET_PNL)
    assert adapter.capabilities().supports(FinancialField.FUNDING)
    assert not adapter.capabilities().supports(FinancialField.TRADING_FEES)


def test_fetch_closed_trades_paginates_and_skips_open_trades() -> None:
    transport = FixtureTransport()
    adapter = FreqtradeReadAdapter(transport, page_size=2)

    records = asyncio.run(adapter.fetch_closed_trades(query()))

    assert [record.trade_ref for record in records] == ["1001", "1003"]
    assert records[0].reported_net_pnl == Decimal("12.34")
    assert records[0].funding == Decimal("-0.12")
    assert records[1].reported_net_pnl == Decimal("-3.21")
    assert records[1].funding == Decimal("0.05")
    assert records[0].settlement_currency == "USDT"
    assert [params["offset"] for _, params in transport.requests] == [0, 2]


def test_fetch_closed_trades_filters_instrument() -> None:
    adapter = FreqtradeReadAdapter(FixtureTransport(), page_size=2)

    records = asyncio.run(adapter.fetch_closed_trades(query(instrument="ETH/USDT:USDT")))

    assert [record.trade_ref for record in records] == ["1003"]


def test_fetch_closed_trades_honors_query_limit() -> None:
    transport = FixtureTransport()
    adapter = FreqtradeReadAdapter(transport, page_size=2)

    records = asyncio.run(adapter.fetch_closed_trades(query(limit=1)))

    assert [record.trade_ref for record in records] == ["1001"]
    assert len(transport.requests) == 1


def test_inverse_pair_uses_explicit_settlement_suffix() -> None:
    payload: JsonObject = {
        "trades": [
            {
                "trade_id": 2001,
                "pair": "BTC/USD:BTC",
                "quote_currency": "USD",
                "is_open": False,
                "open_timestamp": 1893484800000,
                "close_timestamp": 1893499200000,
                "profit_abs": Decimal("0.001"),
                "funding_fees": None,
            }
        ],
        "trades_count": 1,
        "offset": 0,
        "total_trades": 1,
    }
    adapter = FreqtradeReadAdapter(StaticTransport(payload))

    records = asyncio.run(adapter.fetch_closed_trades(query()))

    assert records[0].settlement_currency == "BTC"


def test_malformed_response_fails_closed() -> None:
    payload: JsonObject = {
        "trades": [{"trade_id": 1}],
        "trades_count": 1,
        "offset": 0,
        "total_trades": 1,
    }
    adapter = FreqtradeReadAdapter(StaticTransport(payload))

    with pytest.raises(FreqtradeResponseError):
        asyncio.run(adapter.fetch_closed_trades(query()))


@pytest.mark.parametrize("page_size", [0, 501])
def test_page_size_respects_freqtrade_limit(page_size: int) -> None:
    with pytest.raises(ValueError, match="page_size"):
        FreqtradeReadAdapter(FixtureTransport(), page_size=page_size)


def test_response_offset_mismatch_fails_closed() -> None:
    payload: JsonObject = {
        "trades": [],
        "trades_count": 0,
        "offset": 99,
        "total_trades": 0,
    }
    adapter = FreqtradeReadAdapter(StaticTransport(payload))

    with pytest.raises(FreqtradeResponseError, match="offset"):
        asyncio.run(adapter.fetch_closed_trades(query()))
