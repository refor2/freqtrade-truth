import asyncio
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest

from freqtrade_truth.adapters.bybit_market import (
    BybitInverseMarketInfo,
    BybitMarketPreflightError,
    BybitV5PublicTransport,
    list_bybit_inverse_trading_symbols,
    verify_bybit_inverse_perpetual,
)
from freqtrade_truth.adapters.http import HttpTransportError, JsonObject, JsonValue


def instrument(
    *,
    symbol: str = "BTCUSD",
    contract_type: str = "InversePerpetual",
    status: str = "Trading",
    base_coin: str = "BTC",
    quote_coin: str = "USD",
    settle_coin: str = "BTC",
) -> dict[str, JsonValue]:
    return {
        "symbol": symbol,
        "contractType": contract_type,
        "status": status,
        "baseCoin": base_coin,
        "quoteCoin": quote_coin,
        "settleCoin": settle_coin,
        "leverageFilter": {
            "minLeverage": "1",
            "maxLeverage": "100.00",
            "leverageStep": "0.01",
        },
        "lotSizeFilter": {
            "minOrderQty": "1",
            "qtyStep": "1",
        },
        "priceFilter": {
            "tickSize": "0.10",
        },
    }


def response(
    items: list[dict[str, JsonValue]],
    *,
    category: str = "inverse",
    cursor: str = "",
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


class StaticTransport:
    def __init__(self, pages: list[JsonObject]) -> None:
        self._pages = list(pages)
        self.requests: list[tuple[str, dict[str, str | int | bool]]] = []

    async def get_json(
        self,
        path: str,
        params: Mapping[str, str | int | bool] | None = None,
    ) -> JsonObject:
        self.requests.append((path, dict(params or {})))
        if not self._pages:
            raise AssertionError("unexpected synthetic request")
        return self._pages.pop(0)


def verify(transport: StaticTransport) -> BybitInverseMarketInfo:
    return asyncio.run(
        verify_bybit_inverse_perpetual(
            transport,
            symbol="BTCUSD",
            expected_base_coin="BTC",
            expected_quote_coin="USD",
            expected_settle_coin="BTC",
        )
    )


def test_verifies_expected_btcusd_inverse_perpetual() -> None:
    transport = StaticTransport([response([instrument()])])

    info = verify(transport)

    assert info == BybitInverseMarketInfo(
        symbol="BTCUSD",
        base_coin="BTC",
        quote_coin="USD",
        settle_coin="BTC",
        contract_type="InversePerpetual",
        status="Trading",
        min_leverage=Decimal("1"),
        max_leverage=Decimal("100.00"),
        leverage_step=Decimal("0.01"),
        min_order_qty=Decimal("1"),
        qty_step=Decimal("1"),
        tick_size=Decimal("0.10"),
    )
    assert transport.requests == [
        (
            "v5/market/instruments-info",
            {
                "category": "inverse",
                "symbol": "BTCUSD",
                "status": "Trading",
            },
        )
    ]


@pytest.mark.parametrize(
    "item",
    [
        instrument(contract_type="InverseFutures"),
        instrument(status="PreLaunch"),
        instrument(base_coin="ETH"),
        instrument(quote_coin="USDT"),
        instrument(settle_coin="USDT"),
        instrument(symbol="ETHUSD"),
    ],
)
def test_market_invariant_mismatch_fails_closed(item: dict[str, JsonValue]) -> None:
    with pytest.raises(BybitMarketPreflightError):
        verify(StaticTransport([response([item])]))


def test_zero_or_multiple_matches_fail_closed() -> None:
    with pytest.raises(BybitMarketPreflightError, match="exactly one"):
        verify(StaticTransport([response([])]))

    with pytest.raises(BybitMarketPreflightError, match="exactly one"):
        verify(StaticTransport([response([instrument(), instrument(symbol="ETHUSD")])]))


def test_lists_inverse_trading_symbols_across_cursor_pages() -> None:
    transport = StaticTransport(
        [
            response(
                [instrument(symbol="BTCUSD"), instrument(symbol="ETHUSD")],
                cursor="synthetic-cursor",
            ),
            response([instrument(symbol="SOLUSD")]),
        ]
    )

    symbols = asyncio.run(list_bybit_inverse_trading_symbols(transport))

    assert symbols == ("BTCUSD", "ETHUSD", "SOLUSD")
    assert transport.requests[0][1] == {
        "category": "inverse",
        "status": "Trading",
        "limit": 1000,
    }
    assert transport.requests[1][1]["cursor"] == "synthetic-cursor"


def test_duplicate_symbol_or_repeated_cursor_fails_closed() -> None:
    duplicate = StaticTransport(
        [
            response([instrument()], cursor="next"),
            response([instrument()]),
        ]
    )
    with pytest.raises(BybitMarketPreflightError, match="duplicate"):
        asyncio.run(list_bybit_inverse_trading_symbols(duplicate))

    repeated_cursor = StaticTransport(
        [
            response([], cursor="same"),
            response([], cursor="same"),
        ]
    )
    with pytest.raises(BybitMarketPreflightError, match="cursor repeated"):
        asyncio.run(list_bybit_inverse_trading_symbols(repeated_cursor))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", ""),
        ("symbol", "btcusd"),
        ("expected_base_coin", "btc"),
        ("expected_quote_coin", ""),
        ("expected_settle_coin", "btc"),
    ],
)
def test_preflight_rejects_invalid_expected_values(field: str, value: str) -> None:
    kwargs = {
        "symbol": "BTCUSD",
        "expected_base_coin": "BTC",
        "expected_quote_coin": "USD",
        "expected_settle_coin": "BTC",
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        asyncio.run(
            verify_bybit_inverse_perpetual(
                StaticTransport([]),
                **kwargs,
            )
        )


class FakeResponse:
    def __init__(self, *, status: int = 200, payload: object | None = None) -> None:
        self.status = status
        self._body = json.dumps(payload if payload is not None else {"retCode": 0}).encode()
        self.closed = False

    def read(self, amount: int) -> bytes:
        return self._body[:amount]

    def close(self) -> None:
        self.closed = True


class FakeHttpsConnection:
    instances: list["FakeHttpsConnection"] = []
    response_status = 200
    response_payload: object = {"retCode": 0, "result": {}}

    def __init__(
        self,
        hostname: str,
        port: int | None,
        *,
        timeout: float,
    ) -> None:
        self.hostname = hostname
        self.port = port
        self.timeout = timeout
        self.method: str | None = None
        self.target: str | None = None
        self.headers: Mapping[str, str] | None = None
        self.closed = False
        self.response = FakeResponse(
            status=type(self).response_status,
            payload=type(self).response_payload,
        )
        type(self).instances.append(self)

    def request(
        self,
        method: str,
        target: str,
        *,
        headers: Mapping[str, str],
    ) -> None:
        self.method = method
        self.target = target
        self.headers = headers

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake_connection() -> None:
    FakeHttpsConnection.instances = []
    FakeHttpsConnection.response_status = 200
    FakeHttpsConnection.response_payload = {"retCode": 0, "result": {}}


def public_transport(**kwargs: Any) -> BybitV5PublicTransport:
    defaults: dict[str, Any] = {
        "base_url": "https://api.example.invalid",
    }
    defaults.update(kwargs)
    return BybitV5PublicTransport(**defaults)


def test_public_transport_builds_v5_get_request_without_credentials() -> None:
    FakeHttpsConnection.response_payload = response([instrument()])

    with patch(
        "freqtrade_truth.adapters.bybit_market.HTTPSConnection",
        FakeHttpsConnection,
    ):
        payload = asyncio.run(
            public_transport().get_json(
                "v5/market/instruments-info",
                {
                    "category": "inverse",
                    "symbol": "BTCUSD",
                },
            )
        )

    assert payload["retCode"] == 0
    connection = FakeHttpsConnection.instances[0]
    assert connection.method == "GET"
    assert connection.target == "/v5/market/instruments-info?category=inverse&symbol=BTCUSD"
    assert connection.headers == {"Accept": "application/json"}
    assert connection.closed
    assert connection.response.closed


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "http://api.example.invalid",
        "api.example.invalid",
        "https://api.example.invalid?debug=true",
        "https://api.example.invalid#fragment",
        "https://user:placeholder@api.example.invalid",
    ],
)
def test_public_transport_requires_clean_https_base_url(base_url: str) -> None:
    with pytest.raises(ValueError):
        public_transport(base_url=base_url)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../v5/market/instruments-info",
        "v5/market/instruments-info?category=inverse",
        "v5/market/instruments-info#fragment",
        "api/v3/example",
    ],
)
def test_public_transport_rejects_unsafe_or_non_v5_paths(path: str) -> None:
    with pytest.raises(ValueError):
        asyncio.run(public_transport().get_json(path))


def test_public_transport_http_error_has_no_sensitive_context() -> None:
    FakeHttpsConnection.response_status = 403

    with patch(
        "freqtrade_truth.adapters.bybit_market.HTTPSConnection",
        FakeHttpsConnection,
    ):
        with pytest.raises(HttpTransportError) as exc_info:
            asyncio.run(public_transport().get_json("v5/market/instruments-info"))

    assert str(exc_info.value) == "HTTP request failed with status 403"


def test_market_info_exposes_canonical_derivative_instrument() -> None:
    info = BybitInverseMarketInfo(
        symbol="BTCUSD",
        base_coin="BTC",
        quote_coin="USD",
        settle_coin="BTC",
        contract_type="InversePerpetual",
        status="Trading",
        min_leverage=Decimal("1"),
        max_leverage=Decimal("100"),
        leverage_step=Decimal("0.01"),
        min_order_qty=Decimal("1"),
        qty_step=Decimal("1"),
        tick_size=Decimal("0.1"),
    )

    assert info.normalized_instrument == "BTC/USD:BTC"
