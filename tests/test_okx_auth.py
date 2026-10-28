"""Synthetic, signed GET-only OKX V5 transport contract tests."""

import asyncio
import base64
import hashlib
import hmac
import io
import json

import pytest

import freqtrade_truth.adapters.okx_auth as okx_auth
from freqtrade_truth.adapters.http import HttpTransportError

NOW = "2030-01-02T03:04:05.678Z"
BASE = "https://www.okx.com"
PATH = "api/v5/account/positions-history"


class FakeResponse:
    def __init__(self, body: bytes = b'{"code":"0","data":[],"msg":""}', status: int = 200):
        self.status = status
        self.data = io.BytesIO(body)
        self.closed = False

    def read(self, n: int) -> bytes:
        return self.data.read(n)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    created: list["FakeConnection"] = []
    response = FakeResponse()

    def __init__(self, hostname: str, port: int | None, timeout: float):
        self.hostname = hostname
        self.port = port
        self.timeout = timeout
        self.called: tuple[str, str, dict[str, str]] | None = None
        self.closed = False
        self.__class__.created.append(self)

    def request(self, method: str, target: str, headers: dict[str, str]) -> None:
        self.called = (method, target, headers)

    def getresponse(self) -> FakeResponse:
        return self.__class__.response

    def close(self) -> None:
        self.closed = True


def reader() -> okx_auth.OkxV5ReadOnlyHmacTransport:
    return okx_auth.OkxV5ReadOnlyHmacTransport(
        BASE,
        api_key="dummy-key",
        api_secret="dummy-secret",
        api_passphrase="dummy-passphrase",
        clock=lambda: NOW,
    )


def test_okx_hmac_headers_query_and_no_post(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.created = []
    FakeConnection.response = FakeResponse()
    monkeypatch.setattr(okx_auth, "HTTPSConnection", FakeConnection)
    payload = asyncio.run(reader().get_json(PATH, {"instType": "FUTURES", "limit": 2}))
    assert payload["code"] == "0"
    assert len(FakeConnection.created) == 1
    conn = FakeConnection.created[0]
    assert conn.hostname == "www.okx.com"
    assert conn.called is not None
    method, target, headers = conn.called
    assert method == "GET"
    assert target == "/api/v5/account/positions-history?instType=FUTURES&limit=2"
    content = (NOW + "GET" + target).encode("utf-8")
    expected = base64.b64encode(hmac.new(b"dummy-secret", content, hashlib.sha256).digest()).decode(
        "ascii"
    )
    assert headers["OK-ACCESS-SIGN"] == expected
    assert headers["OK-ACCESS-KEY"] == "dummy-key"
    assert headers["OK-ACCESS-PASSPHRASE"] == "dummy-passphrase"
    assert headers["OK-ACCESS-TIMESTAMP"] == NOW
    assert headers["User-Agent"] == "freqtrade-truth/0.0.7"
    assert conn.closed
    assert FakeConnection.response.closed


@pytest.mark.parametrize(
    "path",
    [
        "api/v5/trade/order",
        "api/v5/trade/order-algo",
        "api/v5/account/withdrawal",
        "api/v5/account/positions",
        "api/v5/account/positions-history?instType=FUTURES",
        "../../api/v5/account/positions-history",
    ],
)
def test_only_one_source_read_endpoint_allowed(path: str) -> None:
    with pytest.raises(ValueError, match="allowlist|path"):
        asyncio.run(reader().get_json(path))


@pytest.mark.parametrize(
    "url",
    [
        "http://www.okx.com",
        "https://wrong.example",
        "https://www.okx.com.evil.example",
        "https://dummy:dummy@www.okx.com",
        "https://www.okx.com:444",
        "https://www.okx.com/api/v5",
    ],
)
def test_only_exact_documented_host_and_safe_base_url(url: str) -> None:
    with pytest.raises(ValueError):
        okx_auth.OkxV5ReadOnlyHmacTransport(
            url,
            api_key="dummy-key",
            api_secret="dummy-secret",
            api_passphrase="dummy-passphrase",
        )


@pytest.mark.parametrize("field", ["api_key", "api_secret", "api_passphrase"])
def test_credentials_must_be_single_line(field: str) -> None:
    data = dict(api_key="key", api_secret="secret", api_passphrase="pass")
    data[field] = "contains\nnewline"
    with pytest.raises(ValueError):
        okx_auth.OkxV5ReadOnlyHmacTransport(
            BASE,
            api_key=data["api_key"],
            api_secret=data["api_secret"],
            api_passphrase=data["api_passphrase"],
        )


def test_non_success_http_never_reveals_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeConnection.response = FakeResponse(b'{"msg":"leaky-secret-value"}', status=401)
    monkeypatch.setattr(okx_auth, "HTTPSConnection", FakeConnection)
    with pytest.raises(HttpTransportError) as caught:
        asyncio.run(reader().get_json(PATH))
    assert "dummy-" not in str(caught.value)
    assert "leaky-secret" not in str(caught.value)
    assert caught.value.status_code == 401


def test_oversized_and_malformed_response_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(okx_auth, "HTTPSConnection", FakeConnection)
    FakeConnection.response = FakeResponse(b"some long response")
    with pytest.raises(HttpTransportError, match="size"):
        asyncio.run(
            okx_auth.OkxV5ReadOnlyHmacTransport(
                BASE,
                api_key="a",
                api_secret="b",
                api_passphrase="c",
                max_response_bytes=4,
                clock=lambda: NOW,
            ).get_json(PATH)
        )
    FakeConnection.response = FakeResponse(b"{bad json")
    with pytest.raises(HttpTransportError, match="JSON"):
        asyncio.run(reader().get_json(PATH))


def test_decimal_json_is_not_coerced_through_binary_float(monkeypatch: pytest.MonkeyPatch) -> None:
    from decimal import Decimal

    FakeConnection.response = FakeResponse(
        json.dumps({"code": "0", "data": [{"ratio": 0.1}]}).encode()
    )
    monkeypatch.setattr(okx_auth, "HTTPSConnection", FakeConnection)
    payload = asyncio.run(reader().get_json(PATH))
    rows = payload["data"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    assert rows[0]["ratio"] == Decimal("0.1")
