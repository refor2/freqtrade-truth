import asyncio
import json
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

import pytest

from freqtrade_truth.adapters.bybit_auth import BybitV5HmacTransport
from freqtrade_truth.adapters.http import HttpTransportError


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


def transport(**kwargs: Any) -> BybitV5HmacTransport:
    defaults: dict[str, Any] = {
        "base_url": "https://api.example.invalid",
        "api_key": "synthetic-api-key",
        "api_secret": "synthetic-secret",
        "clock_ms": lambda: 1658384314791,
    }
    defaults.update(kwargs)
    return BybitV5HmacTransport(**defaults)


def test_get_request_uses_exact_query_string_for_signature_and_target() -> None:
    with patch(
        "freqtrade_truth.adapters.bybit_auth.HTTPSConnection",
        FakeHttpsConnection,
    ):
        payload = asyncio.run(
            transport().get_json(
                "v5/position/closed-pnl",
                {
                    "category": "inverse",
                    "symbol": "BTCUSD",
                },
            )
        )

    assert payload == {"retCode": 0, "result": {}}
    assert len(FakeHttpsConnection.instances) == 1

    connection = FakeHttpsConnection.instances[0]
    assert connection.method == "GET"
    assert connection.target == "/v5/position/closed-pnl?category=inverse&symbol=BTCUSD"

    headers = connection.headers
    assert headers is not None
    assert headers["X-BAPI-API-KEY"] == "synthetic-api-key"
    assert headers["X-BAPI-TIMESTAMP"] == "1658384314791"
    assert headers["X-BAPI-RECV-WINDOW"] == "5000"
    assert (
        headers["X-BAPI-SIGN"] == "32c1e61d4af6a38f3146e002c575c11ceffe9b66fdecf3489613f509b2e6bf8a"
    )
    assert connection.closed
    assert connection.response.closed


def test_boolean_query_values_use_lowercase_json_style() -> None:
    with patch(
        "freqtrade_truth.adapters.bybit_auth.HTTPSConnection",
        FakeHttpsConnection,
    ):
        asyncio.run(
            transport().get_json(
                "v5/example",
                {
                    "enabled": True,
                    "disabled": False,
                },
            )
        )

    assert FakeHttpsConnection.instances[0].target == "/v5/example?enabled=true&disabled=false"


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
def test_transport_requires_unambiguous_https_base_url(base_url: str) -> None:
    with pytest.raises(ValueError):
        transport(base_url=base_url)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_key", ""),
        ("api_key", "synthetic\napi-key"),
        ("api_secret", ""),
        ("recv_window_ms", 0),
        ("timeout_seconds", 0),
        ("max_response_bytes", 0),
    ],
)
def test_transport_rejects_invalid_configuration(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        transport(**{field: value})


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../v5/position/closed-pnl",
        "v5/position/closed-pnl?category=inverse",
        "v5/position/closed-pnl#fragment",
        "api/v3/example",
    ],
)
def test_transport_rejects_unsafe_or_non_v5_paths(path: str) -> None:
    with pytest.raises(ValueError):
        asyncio.run(transport().get_json(path))


def test_negative_clock_value_is_rejected_before_network_request() -> None:
    with pytest.raises(ValueError, match="clock_ms"):
        asyncio.run(transport(clock_ms=lambda: -1).get_json("v5/example"))

    assert FakeHttpsConnection.instances == []


def test_http_error_does_not_expose_credentials() -> None:
    FakeHttpsConnection.response_status = 403

    with patch(
        "freqtrade_truth.adapters.bybit_auth.HTTPSConnection",
        FakeHttpsConnection,
    ):
        with pytest.raises(HttpTransportError) as exc_info:
            asyncio.run(transport().get_json("v5/example"))

    message = str(exc_info.value)
    assert "synthetic-api-key" not in message
    assert "synthetic-secret" not in message
    assert "403" in message


def test_response_size_limit_is_enforced() -> None:
    FakeHttpsConnection.response_payload = {"value": "x" * 256}

    with patch(
        "freqtrade_truth.adapters.bybit_auth.HTTPSConnection",
        FakeHttpsConnection,
    ):
        with pytest.raises(HttpTransportError, match="size limit"):
            asyncio.run(transport(max_response_bytes=32).get_json("v5/example"))


def test_json_object_is_required() -> None:
    FakeHttpsConnection.response_payload = [1, 2, 3]

    with patch(
        "freqtrade_truth.adapters.bybit_auth.HTTPSConnection",
        FakeHttpsConnection,
    ):
        with pytest.raises(HttpTransportError, match="JSON object"):
            asyncio.run(transport().get_json("v5/example"))
